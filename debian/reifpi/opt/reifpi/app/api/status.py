"""
api/status.py — GET /api/status, GET /api/health

Liest ausschließlich die RAM-Datei (state.json) — kein DB-Zugriff.
"""

import time
from flask import Blueprint, current_app, jsonify

bp = Blueprint("status", __name__)


@bp.get("/api/status")
def get_status():
    """Vollständiger Systemzustand aus RAM-Datei."""
    state_mgr = current_app.state_manager
    data = state_mgr.read_state_file()
    if data is None:
        return jsonify({"error": "State-Datei nicht verfügbar"}), 503
    return jsonify(data)


@bp.get("/api/health")
def get_health():
    """
    Minimal-Check: Läuft der Controller, ist die letzte Messung aktuell?
    Gibt 200 OK oder 503 Service Unavailable zurück.
    """
    controller = current_app.controller
    state_mgr  = current_app.state_manager

    data = state_mgr.read_state_file()
    if data is None:
        return jsonify({"healthy": False, "reason": "Keine State-Datei"}), 503

    if not controller.is_running:
        return jsonify({"healthy": False, "reason": "Controller nicht aktiv"}), 503

    # Letzte Messung darf nicht älter als 3× refreshrate sein
    max_age   = data.get("refreshrate", 30) * 3
    last_ts   = data.get("timestamp", 0)
    age_secs  = int(time.time() - last_ts)

    if age_secs > max_age:
        return jsonify({
            "healthy": False,
            "reason":  f"Letzte Messung vor {age_secs}s (max {max_age}s)",
        }), 503

    return jsonify({
        "healthy":       True,
        "age_secs":      age_secs,
        "cycle_count":   data.get("cycle_count", 0),
        "safe_mode":     data.get("safe_mode", False),
    })
