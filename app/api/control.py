"""
api/control.py — Steuerung: Modus, Sollwerte, Relais, Power, Safe-Mode-Reset.
"""

import signal
import subprocess

from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("control", __name__)

VALID_RELAYS = {"fanu", "cool", "fana", "mois", "heat"}
VALID_MODES  = {0, 1, 2, 3}


@bp.post("/api/power")
def set_power():
    """{"power": true|false}"""
    data = request.get_json(silent=True) or {}
    if "power" not in data:
        return jsonify({"error": "Feld 'power' fehlt"}), 400

    current_app.controller.update_config(power=bool(data["power"]))
    return jsonify({"ok": True, "power": bool(data["power"])})


@bp.post("/api/mode")
def set_mode():
    """{"mode": 0-3}"""
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")

    if mode is None or int(mode) not in VALID_MODES:
        return jsonify({"error": f"Ungültiger Modus. Erlaubt: {sorted(VALID_MODES)}"}), 400

    current_app.controller.update_config(mode=int(mode))
    return jsonify({"ok": True, "mode": int(mode)})


@bp.post("/api/targets")
def set_targets():
    """
    Setzt Sollwerte und Hysteresen.
    Alle Felder optional — nur übergebene Felder werden aktualisiert.

    {
        "target_temp":        12.0,
        "target_hum":         80.0,
        "hum_hyst_mois":      5.0,
        "temp_hyst_cool":     0.5,
        "hum_hyst_fana":      2.0,
        "temp_hyst_heat":     1.0,
        "abs_hum_threshold":  0.5,
        "aht21_hyst_humidify": 4.0,
        "aht21_hyst_protect":  2.0,
        "refreshrate":        30
    }
    """
    data = request.get_json(silent=True) or {}

    allowed = {
        "target_temp", "target_hum",
        "hum_hyst_mois", "temp_hyst_cool", "hum_hyst_fana", "temp_hyst_heat",
        "abs_hum_threshold", "aht21_hyst_humidify", "aht21_hyst_protect",
        "refreshrate",
    }
    updates = {}
    errors  = []

    for key in allowed:
        if key not in data:
            continue
        try:
            val = int(data[key]) if key == "refreshrate" else float(data[key])
            if key == "refreshrate" and not (1 <= val <= 3600):
                errors.append(f"{key}: muss zwischen 1 und 3600 liegen")
                continue
            updates[key] = val
        except (ValueError, TypeError):
            errors.append(f"{key}: ungültiger Wert '{data[key]}'")

    if errors:
        return jsonify({"error": "Validierungsfehler", "details": errors}), 400
    if not updates:
        return jsonify({"error": "Keine gültigen Felder übergeben"}), 400

    current_app.controller.update_config(**updates)
    return jsonify({"ok": True, "updated": updates})


@bp.post("/api/relay/<name>/<action>")
def set_relay(name: str, action: str):
    """
    Schaltet ein Relais manuell. Nur im Modus 3 (Manuell) möglich.

    POST /api/relay/cool/on
    POST /api/relay/heat/off
    """
    if name not in VALID_RELAYS:
        return jsonify({"error": f"Unbekanntes Relais '{name}'"}), 404
    if action not in ("on", "off"):
        return jsonify({"error": "Aktion muss 'on' oder 'off' sein"}), 400

    ok = current_app.controller.set_relay_manual(name, action == "on")
    if not ok:
        return jsonify({
            "error": "Manuelles Schalten nur im Modus 3 (Manuell) möglich"
        }), 409

    return jsonify({"ok": True, "relay": name, "active": action == "on"})


@bp.post("/api/safe_mode/reset")
def reset_safe_mode():
    """Setzt den Safe-Mode nach manueller Prüfung zurück."""
    current_app.controller.reset_safe_mode()
    return jsonify({"ok": True})


@bp.post("/api/humidifier/reset")
def reset_humidifier():
    """Gibt den Befeuchter nach Trockenlauf-Sperre wieder frei."""
    current_app.controller.reset_humidifier_fault()
    return jsonify({"ok": True})


# Aktion → (systemctl-Argumente, Relais vorher abschalten).
# Die Rechte kommen aus der polkit-Regel des Pakets (debian/reifpi.polkit.rules),
# sudo scheitert am NoNewPrivileges der Unit.
_SYSTEM_ACTIONS = {
    "shutdown": (["poweroff"], True),
    "reboot":   (["reboot"], True),
    "restart":  (["--no-block", "restart", "reifpi.service"], False),
}


@bp.post("/api/system/<action>")
def system_action(action: str):
    """Pi herunterfahren / neu starten oder nur die Steuerung neu starten."""
    if action not in _SYSTEM_ACTIONS:
        return jsonify({"error": f"Unbekannte Aktion: {action}"}), 404
    args, relays_off = _SYSTEM_ACTIONS[action]

    if current_app.config.get("GPIO_DUMMY"):
        current_app.logger.warning(f"System-Aktion '{action}' im Dev-Modus übersprungen")
        return jsonify({"ok": True, "simulated": True})

    if relays_off:
        current_app.controller.all_off_immediate()
    try:
        r = subprocess.run(
            ["systemctl", "--no-ask-password", *args],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return jsonify({"error": str(e)}), 500
    # Beim Dienst-Neustart beendet systemd auch den systemctl-Aufruf
    # (gleiche cgroup) — SIGTERM heißt hier: Auftrag angenommen.
    restarting = action == "restart" and r.returncode == -signal.SIGTERM
    if r.returncode != 0 and not restarting:
        msg = (r.stderr or r.stdout).strip() or f"Exit-Code {r.returncode}"
        current_app.logger.error(f"System-Aktion '{action}' fehlgeschlagen: {msg}")
        return jsonify({"error": msg}), 500
    current_app.logger.info(f"System-Aktion '{action}' ausgelöst")
    return jsonify({"ok": True})
