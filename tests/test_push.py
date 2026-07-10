"""Tests : Web Push (VAPID + chiffrement aes128gcm, RFC 8291/8292) — implémentation maison."""

from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "test_jarvis.db"
    monkeypatch.setattr("config.DB_PATH", str(db_path))
    monkeypatch.setattr("database.DB_PATH", db_path)
    from database import init_db

    init_db()
    return db_path


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decrypt_aes128gcm_reference(body: bytes, subscriber_private_key, auth_secret: bytes) -> bytes:
    """Décrypteur indépendant (côté 'navigateur') — valide le format produit par push.py."""
    salt = body[:16]
    keyid_len = body[20]
    ephemeral_pub_bytes = body[21:21 + keyid_len]
    ciphertext = body[21 + keyid_len:]

    ephemeral_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), ephemeral_pub_bytes
    )
    subscriber_public_bytes = subscriber_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    shared_secret = subscriber_private_key.exchange(ec.ECDH(), ephemeral_public_key)

    context = b"WebPush: info\x00" + subscriber_public_bytes + ephemeral_pub_bytes
    prk = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=context).derive(
        shared_secret
    )
    key = HKDF(
        algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00"
    ).derive(prk)
    nonce = HKDF(
        algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00"
    ).derive(prk)

    tag = ciphertext[-16:]
    data = ciphertext[:-16]
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    assert padded[-1] == 2  # délimiteur "dernier enregistrement"
    return padded[:-1]


# ── Clés VAPID ─────────────────────────────────────────────────

def test_vapid_key_generated_and_persisted(tmp_db):
    import push

    key1 = push.get_vapid_public_key_b64url()
    key2 = push.get_vapid_public_key_b64url()
    assert key1 == key2  # persistée, pas régénérée à chaque appel
    assert len(push._b64url_decode(key1)) == 65  # point non compressé P-256 : 0x04 + 32 + 32


def test_vapid_public_key_is_valid_ec_point(tmp_db):
    import push

    raw = push._b64url_decode(push.get_vapid_public_key_b64url())
    # Doit être chargeable comme clé publique EC P-256 valide
    pubkey = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    assert pubkey.curve.name == "secp256r1"


def test_vapid_jwt_structure_and_signature_valid(tmp_db):
    import push

    jwt = push._vapid_jwt("https://push.example.com")
    parts = jwt.split(".")
    assert len(parts) == 3

    header = json.loads(push._b64url_decode(parts[0]))
    payload = json.loads(push._b64url_decode(parts[1]))
    assert header == {"typ": "JWT", "alg": "ES256"}
    assert payload["aud"] == "https://push.example.com"
    assert payload["sub"] == "mailto:jarvis@localhost"
    assert payload["exp"] > 0

    signature = push._b64url_decode(parts[2])
    assert len(signature) == 64  # r(32) || s(32), format JOSE (pas DER)

    # La signature doit être vérifiable avec la clé publique VAPID correspondante
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    der_sig = encode_dss_signature(r, s)

    pub_raw = push._b64url_decode(push.get_vapid_public_key_b64url())
    pubkey = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_raw)
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    pubkey.verify(der_sig, signing_input, ec.ECDSA(hashes.SHA256()))  # ne lève pas si valide


# ── Chiffrement aes128gcm (round-trip avec un décrypteur indépendant) ──

def test_encrypt_roundtrip_recovers_original_payload(tmp_db):
    import push

    subscriber_private_key = ec.generate_private_key(ec.SECP256R1())
    subscriber_public_bytes = subscriber_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    auth_secret = b"0123456789abcdef"  # 16 octets

    plaintext = b'{"title": "Test", "body": "Ceci est un test"}'
    encrypted = push._encrypt_aes128gcm(
        plaintext, _b64url(subscriber_public_bytes), _b64url(auth_secret)
    )

    recovered = _decrypt_aes128gcm_reference(encrypted, subscriber_private_key, auth_secret)
    assert recovered == plaintext


def test_encrypt_produces_different_ciphertext_each_time(tmp_db):
    """Le sel + la clé éphémère sont aléatoires — jamais le même corps chiffré deux fois."""
    import push

    subscriber_private_key = ec.generate_private_key(ec.SECP256R1())
    subscriber_public_bytes = subscriber_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    auth_secret = b"0123456789abcdef"

    e1 = push._encrypt_aes128gcm(b"hello", _b64url(subscriber_public_bytes), _b64url(auth_secret))
    e2 = push._encrypt_aes128gcm(b"hello", _b64url(subscriber_public_bytes), _b64url(auth_secret))
    assert e1 != e2


def test_encrypt_header_format(tmp_db):
    """Vérifie la structure salt(16) + rs(4) + keyid_len(1) + keyid + ciphertext."""
    import push

    subscriber_private_key = ec.generate_private_key(ec.SECP256R1())
    subscriber_public_bytes = subscriber_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    auth_secret = b"0123456789abcdef"

    encrypted = push._encrypt_aes128gcm(
        b"x", _b64url(subscriber_public_bytes), _b64url(auth_secret)
    )
    rs = struct.unpack("!L", encrypted[16:20])[0]
    assert rs == 4096
    keyid_len = encrypted[20]
    assert keyid_len == 65  # point non compressé P-256


# ── send_web_push (réseau mocké) ─────────────────────────────────

def test_send_web_push_success(tmp_db):
    import push

    subscriber_private_key = ec.generate_private_key(ec.SECP256R1())
    subscriber_public_bytes = subscriber_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    subscription = {
        "endpoint": "https://push.example.com/abc123",
        "keys": {
            "p256dh": _b64url(subscriber_public_bytes),
            "auth": _b64url(b"0123456789abcdef"),
        },
    }

    fake_response = MagicMock(status_code=201)
    with patch("httpx.post", return_value=fake_response) as mock_post:
        ok, status = push.send_web_push(subscription, {"title": "Salut"})

    assert ok is True
    assert status == 201
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["headers"]["Content-Encoding"] == "aes128gcm"
    assert "vapid t=" in call_kwargs["headers"]["Authorization"]


def test_send_web_push_expired_subscription_returns_false(tmp_db):
    import push

    subscriber_private_key = ec.generate_private_key(ec.SECP256R1())
    subscriber_public_bytes = subscriber_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    subscription = {
        "endpoint": "https://push.example.com/gone",
        "keys": {
            "p256dh": _b64url(subscriber_public_bytes),
            "auth": _b64url(b"0123456789abcdef"),
        },
    }

    fake_response = MagicMock(status_code=410)
    with patch("httpx.post", return_value=fake_response):
        ok, status = push.send_web_push(subscription, {"title": "Salut"})

    assert ok is False
    assert status == 410


def test_send_web_push_network_error_handled(tmp_db):
    import httpx
    import push

    subscriber_private_key = ec.generate_private_key(ec.SECP256R1())
    subscriber_public_bytes = subscriber_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    subscription = {
        "endpoint": "https://push.example.com/unreachable",
        "keys": {
            "p256dh": _b64url(subscriber_public_bytes),
            "auth": _b64url(b"0123456789abcdef"),
        },
    }

    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        ok, status = push.send_web_push(subscription, {"title": "Salut"})

    assert ok is False
    assert status == 0
