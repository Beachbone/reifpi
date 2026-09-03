"""
api/archive.py — Historische Messdaten für Charts.
"""

import time
from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("archive", __name__)

_MAX_HOURS = 8760   # 1 Jahr


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

    since = time.time() - hours * 3600
    db    = current_app.db

    rows = db.fetchall(
        """
        SELECT timestamp       AS ts,
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
        FROM   state_archive
        WHERE  timestamp >= ?
        ORDER  BY timestamp ASC
        """,
        (since,),
    )

    # Optionales Client-seitiges Downsampling
    limit = request.args.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            return jsonify({"error": "'limit' muss eine ganze Zahl sein"}), 400
        if limit < 2:
            return jsonify({"error": "'limit' muss ≥ 2 sein"}), 400
        rows = _downsample(rows, limit)

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


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _downsample(rows: list, limit: int) -> list:
    """
    Gleichmäßiges Ausdünnen: behält ~`limit` Datenpunkte.
    Ersten und letzten Punkt immer beibehalten.
    """
    n = len(rows)
    if n <= limit:
        return rows
    step = n / limit
    return [rows[int(i * step)] for i in range(limit - 1)] + [rows[-1]]
