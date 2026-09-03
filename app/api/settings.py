"""
api/settings.py — Konfiguration lesen und schreiben.

GET  /api/settings          Alle Einstellungen aus DB (ohne Passwort)
POST /api/settings          Felder aktualisieren (nur bekannte Schlüssel)
POST /api/settings/test_email   Test-E-Mail senden
POST /api/settings/test_ntfy    Test-Benachrichtigung senden
"""

from flask import Blueprint, current_app, jsonify, request

from app.notifications.email_notifier import EmailNotifier
from app.notifications.ntfy_notifier  import NtfyNotifier

bp = Blueprint("settings", __name__)

# Felder die niemals an den Client gesendet werden
_HIDDEN_FIELDS = {"mail_password", "ntfy_token"}

# Erlaubte Felder mit ihrem Typ (str, int, float, bool)
_ALLOWED_FIELDS: dict[str, type] = {
    # Regelung
    "target_temp":          float,
    "target_hum":           float,
    "hum_hyst_mois":        float,
    "temp_hyst_cool":       float,
    "hum_hyst_fana":        float,
    "temp_hyst_heat":       float,
    "abs_hum_threshold":    float,
    "aht21_hyst_humidify":  float,
    "aht21_hyst_protect":   float,
    "refreshrate":          int,
    # GPIO
    "gpio_pin_fanu":        int,
    "gpio_pin_cool":        int,
    "gpio_pin_fana":        int,
    "gpio_pin_mois":        int,
    "gpio_pin_heat":        int,
    "relay_active_high":    bool,
    # Waage (Zukunft)
    "weight_sensor_type":   str,
    "weight_sensor_pin":    int,
    "weight_tare":          float,
    "weight_factor":        float,
    # E-Mail
    "mail_enabled":         bool,
    "mail_smtp":            str,
    "mail_port":            int,
    "mail_user":            str,
    "mail_password":        str,
    "mail_receiver":        str,
    "email_sleep":          int,
    # ntfy
    "ntfy_enabled":         bool,
    "ntfy_topic":           str,
    "ntfy_server":          str,
    "ntfy_user":            str,
    "ntfy_token":           str,
    # Archiv
    "arch_handling":        int,
}

# Validierungsgrenzen für numerische Felder
_RANGES: dict[str, tuple] = {
    "target_temp":         (-50.0,  60.0),
    "target_hum":          (0.0,   100.0),
    "hum_hyst_mois":       (0.1,    50.0),
    "temp_hyst_cool":      (0.1,    20.0),
    "hum_hyst_fana":       (0.1,    50.0),
    "temp_hyst_heat":      (0.1,    20.0),
    "abs_hum_threshold":   (0.0,    50.0),
    "aht21_hyst_humidify": (0.1,    50.0),
    "aht21_hyst_protect":  (0.1,    50.0),
    "refreshrate":         (1,    3600),
    "mail_port":           (1,   65535),
    "email_sleep":         (1,    1440),
    "arch_handling":       (100, 100000),
}


@bp.get("/api/settings")
def get_settings():
    """Alle Einstellungen (ohne Passwort)."""
    db  = current_app.db
    row = db.fetchone("SELECT * FROM config WHERE id = 1")
    if not row:
        return jsonify({"error": "Keine Konfiguration gefunden"}), 500
    data = {k: v for k, v in dict(row).items() if k not in _HIDDEN_FIELDS}
    return jsonify(data)


@bp.post("/api/settings")
def update_settings():
    """
    Beliebige Kombination der erlaubten Felder aktualisieren.
    GPIO-Änderungen erfordern Service-Neustart (Hinweis im Response).
    """
    data   = request.get_json(silent=True) or {}
    errors = []
    updates: dict = {}

    gpio_changed = False

    for key, raw in data.items():
        if key not in _ALLOWED_FIELDS:
            errors.append(f"'{key}': unbekanntes Feld")
            continue

        cast = _ALLOWED_FIELDS[key]
        try:
            if cast == bool:
                val = bool(raw)
            elif raw is None and cast == str:
                val = ""
            else:
                val = cast(raw)
        except (ValueError, TypeError):
            errors.append(f"'{key}': ungültiger Wert '{raw}'")
            continue

        if key in _RANGES:
            lo, hi = _RANGES[key]
            if not (lo <= val <= hi):
                errors.append(f"'{key}': muss zwischen {lo} und {hi} liegen")
                continue

        updates[key] = val
        if key.startswith("gpio_pin_") or key == "relay_active_high":
            gpio_changed = True

    if errors:
        return jsonify({"error": "Validierungsfehler", "details": errors}), 400
    if not updates:
        return jsonify({"error": "Keine gültigen Felder übergeben"}), 400

    # In DB persistieren
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    current_app.db.execute(
        f"UPDATE config SET {set_clause} WHERE id = 1",
        list(updates.values()),
    )

    # Controller-State live aktualisieren (außer GPIO — braucht Neustart)
    live_updates = {k: v for k, v in updates.items() if not k.startswith("gpio_pin_")
                    and k != "relay_active_high"
                    and not k.startswith(("mail_", "ntfy_", "weight_"))}
    if live_updates:
        current_app.controller.update_config(**live_updates)

    resp = {"ok": True, "updated": updates}
    if gpio_changed:
        resp["warning"] = "GPIO-Änderungen sind erst nach Service-Neustart aktiv."
    return jsonify(resp)


@bp.post("/api/settings/test_email")
def test_email():
    """Sendet eine Test-E-Mail mit den aktuell gespeicherten Einstellungen."""
    db  = current_app.db
    row = db.fetchone("SELECT * FROM config WHERE id = 1")
    if not row:
        return jsonify({"error": "Keine Konfiguration gefunden"}), 500

    cfg     = dict(row)
    # Throttle für Test auf 0 setzen damit E-Mail sofort gesendet wird
    notifier = EmailNotifier(
        smtp_server  = cfg.get("mail_smtp", ""),
        port         = int(cfg.get("mail_port", 587)),
        user         = cfg.get("mail_user", ""),
        password     = cfg.get("mail_password", ""),
        receiver     = cfg.get("mail_receiver", ""),
        throttle_min = 0,
        enabled      = True,
    )
    ok = notifier.send(
        subject = "Verbindungstest",
        body    = "Diese E-Mail bestätigt die korrekte Konfiguration des Reifeschranks.",
    )
    if ok:
        return jsonify({"ok": True, "message": "Test-E-Mail gesendet."})
    return jsonify({"error": "E-Mail konnte nicht gesendet werden. Einstellungen prüfen."}), 502


@bp.post("/api/settings/test_ntfy")
def test_ntfy():
    """Sendet eine Test-Nachricht via ntfy."""
    db  = current_app.db
    row = db.fetchone("SELECT * FROM config WHERE id = 1")
    if not row:
        return jsonify({"error": "Keine Konfiguration gefunden"}), 500

    cfg      = dict(row)
    notifier = NtfyNotifier(
        topic   = cfg.get("ntfy_topic",  ""),
        server  = cfg.get("ntfy_server", "https://ntfy.sh"),
        user    = cfg.get("ntfy_user",   ""),
        token   = cfg.get("ntfy_token",  ""),
        enabled = True,
    )
    ok = notifier.send(
        title   = "Reifeschrank – Verbindungstest",
        message = "ntfy-Benachrichtigungen sind korrekt konfiguriert.",
        tags    = ["white_check_mark"],
    )
    if ok:
        return jsonify({"ok": True, "message": "ntfy-Nachricht gesendet."})
    return jsonify({"error": "ntfy-Nachricht konnte nicht gesendet werden. Topic/Server prüfen."}), 502
