#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# JARVIS — Génération du certificat SSL auto-signé (HTTPS local + Tailscale)
# ═══════════════════════════════════════════════════════════════════════════
#
# Usage :
#   chmod +x scripts/generate_ssl.sh
#   ./scripts/generate_ssl.sh
#
# Produit :
#   certs/cert.pem  — certificat public (à faire accepter dans le navigateur)
#   certs/key.pem   — clé privée      (NE PAS committer — dans .gitignore)
#
# Pour faire confiance au certificat sur macOS (une seule fois) :
#   sudo security add-trusted-cert -d -r trustRoot \
#     -k /Library/Keychains/System.keychain certs/cert.pem
#
# Sur iPhone (via Tailscale) :
#   1. Envoie certs/cert.pem sur ton iPhone (AirDrop ou mail)
#   2. Installe-le : Réglages > Général > Gestion VPN et appareils
#   3. Active la confiance : Réglages > Général > À propos > Certificats de confiance
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERTS_DIR="$ROOT/certs"
CERT="$CERTS_DIR/cert.pem"
KEY="$CERTS_DIR/key.pem"
DAYS=825   # max accepté par iOS/macOS pour les certs auto-signés

mkdir -p "$CERTS_DIR"

# ── Détection automatique de l'IP Tailscale ─────────────────────────────
TAILSCALE_IP=""
if command -v tailscale &>/dev/null; then
  TAILSCALE_IP=$(tailscale ip -4 2>/dev/null \
    | awk '/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print; exit }' \
    || true)
fi
if [[ -z "$TAILSCALE_IP" ]]; then
  # Fallback : lecture des interfaces réseau (Tailscale utilise 100.x.x.x)
  TAILSCALE_IP=$(ifconfig 2>/dev/null \
    | grep 'inet ' \
    | awk '{print $2}' \
    | grep '^100\.' \
    | head -1 || true)
fi

echo "── JARVIS SSL ──────────────────────────────────────────"
echo "Répertoire : $CERTS_DIR"
echo "Validité   : ${DAYS} jours"
if [[ -n "$TAILSCALE_IP" ]]; then
  echo "IP Tailscale détectée : $TAILSCALE_IP"
else
  echo "IP Tailscale : non détectée (Tailscale déconnecté ?)"
  echo "  → Le cert ne couvrira que localhost / 127.0.0.1."
  echo "  → Relance ce script après avoir connecté Tailscale pour un cert complet."
fi
echo "────────────────────────────────────────────────────────"

# ── Construction du bloc SAN ────────────────────────────────────────────
SAN="subjectAltName=DNS:localhost,IP:127.0.0.1"
if [[ -n "$TAILSCALE_IP" ]]; then
  SAN="${SAN},IP:${TAILSCALE_IP}"
fi

# ── Génération ──────────────────────────────────────────────────────────
openssl req -x509 -nodes \
  -newkey rsa:2048 \
  -keyout "$KEY" \
  -out    "$CERT" \
  -days   "$DAYS" \
  -subj   "/CN=jarvis.local/O=JARVIS/C=FR" \
  -addext "$SAN"

chmod 600 "$KEY"
chmod 644 "$CERT"

echo ""
echo "✓ Certificat généré :"
echo "  cert : $CERT"
echo "  key  : $KEY"
echo ""
echo "Empreinte SHA-256 :"
openssl x509 -noout -fingerprint -sha256 -in "$CERT"
echo ""
echo "SAN couverts :"
openssl x509 -noout -text -in "$CERT" 2>/dev/null \
  | grep -A1 "Subject Alternative Name" \
  || echo "  (impossible d'afficher les SAN)"
echo ""
echo "Prochaine étape — faire confiance sur macOS (2 options) :"
echo ""
echo "Option A : double-clic dans Finder sur $CERT"
echo "  → Trousseau d'accès s'ouvre → double-clic sur jarvis.local"
echo "  → 'Faire confiance' → 'Toujours faire confiance'"
echo ""
echo "Option B : depuis un Terminal ouvert en session GUI (pas SSH) :"
echo "  sudo security add-trusted-cert -d -r trustRoot \\"
echo "    -k /Library/Keychains/System.keychain $CERT"
