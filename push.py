"""Web Push (RFC 8291 aes128gcm + RFC 8292 VAPID) — sans dépendance fragile.

`pywebpush` (la lib standard) dépend de `http_ece`, un paquet abandonné qui
ne compile plus avec les outils de build actuels. On réimplémente ici le
sous-ensemble strictement nécessaire — `aes128gcm` est le seul format que
les navigateurs et push services modernes (FCM, Mozilla, Apple) utilisent
aujourd'hui — avec `cryptography` uniquement (déjà une dépendance directe
pour le chiffrement des sauvegardes).

La paire de clés VAPID est générée une fois et persistée dans
`app_settings` (clé privée jamais exposée — seule la clé publique, encodée
en base64url non tronquée, est envoyée au navigateur pour `PushManager.subscribe`).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from database import get_setting, set_setting

logger = logging.getLogger(__name__)

_SETTING_VAPID_PRIVATE = "vapid_private_key_pem"
_VAPID_SUBJECT = "mailto:jarvis@localhost"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _load_or_create_vapid_private_key() -> ec.EllipticCurvePrivateKey:
    pem = get_setting(_SETTING_VAPID_PRIVATE, "")
    if pem:
        return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)

    private_key = ec.generate_private_key(ec.SECP256R1())
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    set_setting(_SETTING_VAPID_PRIVATE, pem_bytes.decode("utf-8"))
    logger.info("[push] nouvelle paire de clés VAPID générée")
    return private_key


def get_vapid_public_key_b64url() -> str:
    """Clé publique VAPID (point non compressé, base64url) — pour `applicationServerKey`."""
    private_key = _load_or_create_vapid_private_key()
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return _b64url_encode(raw)


def _vapid_jwt(audience: str) -> str:
    """JWT ES256 (RFC 8292) — signature JOSE (r||s bruts), pas DER."""
    private_key = _load_or_create_vapid_private_key()
    header = _b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}).encode("utf-8"))
    payload = _b64url_encode(json.dumps({
        "aud": audience,
        "exp": int(time.time()) + 12 * 3600,
        "sub": _VAPID_SUBJECT,
    }).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")

    der_signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_signature)
    raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    return f"{header}.{payload}.{_b64url_encode(raw_signature)}"


def _encrypt_aes128gcm(payload: bytes, p256dh_b64url: str, auth_b64url: str) -> bytes:
    """Chiffre `payload` pour un abonnement donné (RFC 8291). Retourne le corps HTTP prêt à envoyer."""
    subscriber_public_bytes = _b64url_decode(p256dh_b64url)
    auth_secret = _b64url_decode(auth_b64url)
    subscriber_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), subscriber_public_bytes
    )

    ephemeral_private_key = ec.generate_private_key(ec.SECP256R1())
    ephemeral_public_bytes = ephemeral_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    shared_secret = ephemeral_private_key.exchange(ec.ECDH(), subscriber_public_key)

    # Contexte WebPush (RFC 8291 §3.4) : info = "WebPush: info\x00" + destinataire + expéditeur
    context = b"WebPush: info\x00" + subscriber_public_bytes + ephemeral_public_bytes
    prk = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=context).derive(
        shared_secret
    )

    salt = os.urandom(16)
    key = HKDF(
        algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00"
    ).derive(prk)
    nonce = HKDF(
        algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00"
    ).derive(prk)

    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    ciphertext = encryptor.update(payload + b"\x02") + encryptor.finalize() + encryptor.tag

    header = (
        salt
        + struct.pack("!L", 4096)
        + struct.pack("!B", len(ephemeral_public_bytes))
        + ephemeral_public_bytes
    )
    return header + ciphertext


def send_web_push(subscription: dict, payload: dict, ttl: int = 60) -> tuple[bool, int]:
    """Envoie une notification push. Retourne (succès, status_http).

    `subscription` : `{"endpoint", "keys": {"p256dh", "auth"}}` (format
    `PushSubscription.toJSON()` du navigateur). L'appelant doit supprimer
    l'abonnement si le statut est 404/410 (expiré côté navigateur).
    """
    endpoint = subscription["endpoint"]
    keys = subscription["keys"]
    body = _encrypt_aes128gcm(json.dumps(payload).encode("utf-8"), keys["p256dh"], keys["auth"])

    origin = "/".join(endpoint.split("/")[:3])
    jwt = _vapid_jwt(origin)
    vapid_public = get_vapid_public_key_b64url()

    headers = {
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Authorization": f"vapid t={jwt}, k={vapid_public}",
    }
    try:
        resp = httpx.post(endpoint, content=body, headers=headers, timeout=10.0)
        return resp.status_code in (200, 201, 202), resp.status_code
    except httpx.HTTPError as e:
        logger.warning("[push] envoi échoué (%s) : %s", endpoint[:60], e)
        return False, 0
