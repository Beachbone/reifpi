#!/bin/bash
# setup_tmpfs.sh — Richtet das tmpfs für state.json ein.
# Einmalig als root ausführen: sudo bash setup_tmpfs.sh

set -e

MOUNT_POINT="/run/reifeschrank"
# uid=1000,gid=1000 (pi-User): tmpfs gehört sofort ab Mount pi:pi,
# damit der Service auch ohne den chown-Fix in reife-pi2.service sauber startet.
FSTAB_ENTRY="tmpfs  ${MOUNT_POINT}  tmpfs  defaults,size=10m,mode=0755,uid=1000,gid=1000  0 0"

echo "=== Reifeschrank tmpfs Setup ==="

# Verzeichnis anlegen
mkdir -p "$MOUNT_POINT"

# fstab-Eintrag nur einmal hinzufügen
if grep -q "$MOUNT_POINT" /etc/fstab; then
    echo "fstab-Eintrag bereits vorhanden — übersprungen."
else
    echo "$FSTAB_ENTRY" >> /etc/fstab
    echo "fstab-Eintrag hinzugefügt."
fi

# Sofort mounten
if mountpoint -q "$MOUNT_POINT"; then
    echo "tmpfs bereits gemountet."
else
    mount "$MOUNT_POINT"
    echo "tmpfs gemountet."
fi

# Berechtigungen für pi-User
chown pi:pi "$MOUNT_POINT"

echo "Fertig. ${MOUNT_POINT} ist bereit."
