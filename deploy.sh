#!/bin/bash
# deploy.sh — Baut das reifpi-.deb lokal und installiert es zum Testen
# auf einem Ziel-Pi (SSH-Zugang vorausgesetzt).
#
# Ersetzt den alten rsync-Direktdeploy (frühere reife-pi2.service, setup_tmpfs.sh) —
# der Installationsweg ist jetzt ausschließlich das .deb-Paket, dessen
# postinst/systemd-Unit (User-Anlage, RuntimeDirectory, venv) alles selbst
# erledigt, was hier früher Schritt für Schritt manuell gemacht wurde.
#
# Verwendung: bash deploy.sh [pi@192.168.2.118]

set -e

PI="${1:-pi@192.168.2.118}"

echo "=== reifpi Dev-Deploy ==="
echo "Ziel: ${PI}"
echo ""

echo "[1/3] .deb lokal bauen..."
dpkg-buildpackage --no-sign -b

DEB_FILE="$(ls -t ../reifpi_*_all.deb | head -1)"
echo "      Gebaut: ${DEB_FILE}"

echo "[2/3] .deb auf den Pi kopieren..."
scp "$DEB_FILE" "${PI}:/tmp/reifpi.deb"

echo "[3/3] Installieren (apt install, danach Service-Status prüfen)..."
ssh "$PI" "
    sudo apt-get install -y /tmp/reifpi.deb
    sleep 3
    sudo systemctl status reifpi --no-pager
"

echo ""
echo "=== Deploy abgeschlossen ==="
echo "Dashboard erreichbar unter: http://${PI#*@}:5000"
