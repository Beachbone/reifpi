#!/bin/bash
# deploy.sh — Installiert reife-pi2 auf dem Pi.
# Ausführen von diesem Rechner aus: bash deploy.sh
# Voraussetzung: SSH-Zugang zu pi@192.168.2.118

set -e

PI="pi@192.168.2.118"
APP_DIR="/opt/reife-pi2"
LOG_DIR="/var/log/reife-pi2"
SERVICE="reife-pi2"
OLD_SERVICE="reifeschrank"

echo "=== reife-pi2 Deploy ==="
echo "Ziel: ${PI}:${APP_DIR}"
echo ""

# ── 1. Dateien übertragen ──────────────────────────────────────────────────
# --delete räumt verwaiste Dateien vom letzten Deploy weg (z.B. entfernte
# Module aus Refactorings), die sonst auf dem Pi liegen bleiben. Alles, was
# NICHT aus dem Repo kommt sondern auf dem Pi selbst entsteht — venv, die
# Laufzeit-DBs — ist deshalb explizit ausgeschlossen, sonst würde --delete
# es beim nächsten Deploy löschen.
echo "[1/7] Dateien übertragen (räumt verwaiste Dateien mit auf)..."
ssh "$PI" "sudo mkdir -p ${APP_DIR} && sudo chown pi:pi ${APP_DIR}"
rsync -av --delete \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
    --exclude='venv/' \
    --exclude='reifeschrank*.db*' \
    /home/beach/projekte/reife-pi/reife-pi2/ \
    "${PI}:${APP_DIR}/"

# ── 2. Python venv + Abhängigkeiten ───────────────────────────────────────
echo "[2/7] Python venv einrichten..."
ssh "$PI" "
    cd ${APP_DIR}
    python3 -m venv venv
    venv/bin/pip install --upgrade pip
    venv/bin/pip install -r requirements.txt
"

# ── 3. tmpfs einrichten ───────────────────────────────────────────────────
echo "[3/7] tmpfs einrichten..."
ssh "$PI" "sudo bash ${APP_DIR}/setup_tmpfs.sh"

# ── 4. Log-Verzeichnis anlegen ────────────────────────────────────────────
echo "[4/7] Log-Verzeichnis anlegen..."
ssh "$PI" "sudo mkdir -p ${LOG_DIR} && sudo chown pi:pi ${LOG_DIR}"

# ── 5. systemd-Service installieren ──────────────────────────────────────
echo "[5/7] systemd-Service installieren..."
ssh "$PI" "sudo cp ${APP_DIR}/reife-pi2.service /etc/systemd/system/ && sudo systemctl daemon-reload"

# ── 6. Alten Service stoppen, neuen starten ───────────────────────────────
echo "[6/7] Alten Service stoppen, neuen starten..."
echo "      ACHTUNG: Reifeschrank ist kurz ohne Steuerung!"
read -rp "      Weiter? (j/N): " confirm
if [[ "$confirm" != "j" && "$confirm" != "J" ]]; then
    echo "Abgebrochen."
    exit 1
fi

ssh "$PI" "
    sudo systemctl stop ${OLD_SERVICE} || true
    sudo systemctl disable ${OLD_SERVICE} || true
    sudo systemctl enable ${SERVICE}
    sudo systemctl start ${SERVICE}
"

# ── 7. Status prüfen ──────────────────────────────────────────────────────
echo "[7/7] Status prüfen..."
sleep 3
ssh "$PI" "sudo systemctl status ${SERVICE} --no-pager"

echo ""
echo "=== Deploy abgeschlossen ==="
echo "BOF erreichbar unter: http://192.168.2.118:5000"
