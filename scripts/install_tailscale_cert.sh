#!/usr/bin/env bash
# Installe un certificat TLS Tailscale valide pour le mode WEB_HTTPS direct.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_PATH="${WEB_SSL_CERT_PATH:-certs/cert.pem}"
KEY_PATH="${WEB_SSL_KEY_PATH:-certs/key.pem}"

if [[ "$CERT_PATH" != /* ]]; then
    CERT_PATH="$PROJECT_DIR/$CERT_PATH"
fi
if [[ "$KEY_PATH" != /* ]]; then
    KEY_PATH="$PROJECT_DIR/$KEY_PATH"
fi

if ! command -v tailscale >/dev/null 2>&1; then
    echo "Erreur : la commande tailscale est introuvable." >&2
    exit 1
fi

TAILSCALE_DOMAIN="${1:-}"
if [[ -z "$TAILSCALE_DOMAIN" ]]; then
    TAILSCALE_DOMAIN="$(
        tailscale status --json |
            python3 -c 'import json, sys; print(json.load(sys.stdin).get("Self", {}).get("DNSName", "").rstrip("."))'
    )"
fi
if [[ -z "$TAILSCALE_DOMAIN" ]]; then
    echo "Erreur : nom MagicDNS introuvable. Passez-le en argument." >&2
    exit 1
fi

mkdir -p "$(dirname "$CERT_PATH")" "$(dirname "$KEY_PATH")"
umask 077
tailscale cert \
    --cert-file "$CERT_PATH" \
    --key-file "$KEY_PATH" \
    "$TAILSCALE_DOMAIN"
chmod 644 "$CERT_PATH"
chmod 600 "$KEY_PATH"

echo "Certificat Tailscale installé pour $TAILSCALE_DOMAIN"
echo "  cert : $CERT_PATH"
echo "  key  : $KEY_PATH"
