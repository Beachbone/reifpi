"""
api/hardware_setup.py — Hardware-Sensor-Konfiguration lesen und schreiben.

GET  /api/hardware_setup   Aktuelle Sensor-Konfiguration aus DB
POST /api/hardware_setup   Sensor-Konfiguration speichern (Neustart erforderlich)
"""

from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("hardware_setup", __name__)

_ALLOWED_FIELDS: dict[str, type] = {
    "sensor_intern":         str,
    "sensor_intern_address": int,
    "sensor_extern":         str,
    "sensor_extern_address": int,
    "i2c_bus":               int,
}

_SENSOR_TYPES = {"sht31", "aht21", "bme280", "none"}

_RANGES: dict[str, tuple] = {
    "i2c_bus":               (0, 9),
    "sensor_intern_address": (0x03, 0x77),
    "sensor_extern_address": (0x03, 0x77),
}


@bp.get("/api/hardware_setup")
def get_hardware_setup():
    """Sensor-Konfiguration aus DB."""
    row = current_app.db.fetchone("SELECT * FROM config WHERE id = 1")
    if not row:
        return jsonify({"error": "Keine Konfiguration gefunden"}), 500

    d = dict(row)
    return jsonify({
        "sensor_intern":         d.get("sensor_intern",         "sht31"),
        "sensor_intern_address": d.get("sensor_intern_address", 0x44),
        "sensor_extern":         d.get("sensor_extern",         "aht21"),
        "sensor_extern_address": d.get("sensor_extern_address", 0x38),
        "i2c_bus":               d.get("i2c_bus",               1),
    })


@bp.post("/api/hardware_setup")
def update_hardware_setup():
    """Sensor-Konfiguration speichern. Service-Neustart erforderlich."""
    data   = request.get_json(silent=True) or {}
    errors = []
    updates: dict = {}

    for key, raw in data.items():
        if key not in _ALLOWED_FIELDS:
            errors.append(f"'{key}': unbekanntes Feld")
            continue

        cast = _ALLOWED_FIELDS[key]
        try:
            val = cast(raw)
        except (ValueError, TypeError):
            errors.append(f"'{key}': ungültiger Wert '{raw}'")
            continue

        if cast == str and key in ("sensor_intern", "sensor_extern"):
            if val not in _SENSOR_TYPES:
                errors.append(f"'{key}': ungültiger Sensor-Typ '{val}'")
                continue

        if key in _RANGES:
            lo, hi = _RANGES[key]
            if not (lo <= val <= hi):
                errors.append(f"'{key}': muss zwischen {lo} und {hi} liegen")
                continue

        updates[key] = val

    if errors:
        return jsonify({"error": "Validierungsfehler", "details": errors}), 400
    if not updates:
        return jsonify({"error": "Keine gültigen Felder übergeben"}), 400

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    current_app.db.execute(
        f"UPDATE config SET {set_clause} WHERE id = 1",
        list(updates.values()),
    )

    return jsonify({
        "ok":      True,
        "updated": updates,
        "warning": "Sensor-Änderungen sind erst nach Service-Neustart aktiv.",
    })
