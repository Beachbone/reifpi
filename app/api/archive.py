"""
api/archive.py — Historische Messdaten für Charts.
"""

import time
from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("archive", __name__)

_MAX_HOURS = 8760   # 1 Jahr

_COLUMNS = """
    timestamp       AS ts,
    temp_intern     AS temp,
    hum_intern      AS hum,
    weight,
    target_temp,
    target_hum,
    relay_heat,
    relay_cool,
    relay_mois,
    relay_fanu,
    relay_fana
"""


@bp.get("/api/archive")
def get_archive():
    """
    Messdaten für Charts.

    Query-Parameter:
        hours  (int, default 24)   — Zeitraum in Stunden
        limit  (int, optional)     — Maximale Anzahl Datenpunkte (Downsampling)

    Gibt Liste von Objekten zurück:
    [{"ts": 1711000000, "temp": 14.1, "hum": 83.2, "weight": null}, ...]
    """
    try:
        hours = int(request.args.get("hours", 24))
    except (ValueError, TypeError):
        return jsonify({"error": "'hours' muss eine ganze Zahl sein"}), 400

    if not (1 <= hours <= _MAX_HOURS):
        return jsonify({"error": f"'hours' muss zwischen 1 und {_MAX_HOURS} liegen"}), 400

    # Optionales Downsampling
    limit = request.args.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            return jsonify({"error": "'limit' muss eine ganze Zahl sein"}), 400
        if limit < 3:
            return jsonify({"error": "'limit' muss ≥ 3 sein"}), 400

    since = time.time() - hours * 3600
    db    = current_app.db

    # Bei großem Archiv (30 Tage ≈ 260 000 Zeilen) schon in SQLite
    # ausdünnen: jede step-te id plus erster und letzter Punkt. Auf dem
    # Pi 3 0,4 s statt 5 s für alle Zeilen (ROW_NUMBER() wäre 5 s).
    n    = db.fetchscalar(
        "SELECT COUNT(*) FROM state_archive WHERE timestamp >= ?", (since,)
    ) or 0
    step = -(-n // (limit - 2)) if limit and n > limit else 1

    rows = db.fetchall(
        f"""
        SELECT {_COLUMNS}
        FROM   state_archive
        WHERE  timestamp >= :since
          AND  (id % :step = 0
                OR id = (SELECT MIN(id) FROM state_archive WHERE timestamp >= :since)
                OR id = (SELECT MAX(id) FROM state_archive))
        ORDER  BY timestamp ASC
        """,
        {"since": since, "step": step},
    )

    return jsonify([dict(r) for r in rows])


@bp.get("/api/archive/count")
def get_archive_count():
    """Anzahl Einträge in state_archive (für Debug/Admin)."""
    db    = current_app.db
    total = db.fetchscalar("SELECT COUNT(*) FROM state_archive") or 0

    since_24h = time.time() - 86400
    last_24h  = db.fetchscalar(
        "SELECT COUNT(*) FROM state_archive WHERE timestamp >= ?", (since_24h,)
    ) or 0

    oldest = db.fetchscalar("SELECT MIN(timestamp) FROM state_archive")
    newest = db.fetchscalar("SELECT MAX(timestamp) FROM state_archive")

    return jsonify({
        "total":    total,
        "last_24h": last_24h,
        "oldest":   oldest,
        "newest":   newest,
    })
