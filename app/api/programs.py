"""
api/programs.py — Reifeprogramme: CRUD, Aktivieren, Stoppen.
"""

import time
from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("programs", __name__)


def _get_program_with_steps(db, program_id: int) -> dict | None:
    pgm = db.fetchone("SELECT * FROM riping_pgms WHERE id = ?", (program_id,))
    if not pgm:
        return None
    steps = db.fetchall(
        "SELECT * FROM riping_steps WHERE riping_pgms_id = ? ORDER BY step ASC",
        (program_id,),
    )
    return {**pgm, "steps": steps}


@bp.get("/api/programs")
def list_programs():
    """Alle Programme mit Schritten."""
    db   = current_app.db
    pgms = db.fetchall("SELECT * FROM riping_pgms ORDER BY name ASC")
    result = []
    for pgm in pgms:
        steps = db.fetchall(
            "SELECT * FROM riping_steps WHERE riping_pgms_id = ? ORDER BY step ASC",
            (pgm["id"],),
        )
        result.append({**pgm, "steps": steps})
    return jsonify(result)


@bp.get("/api/programs/<int:program_id>")
def get_program(program_id: int):
    pgm = _get_program_with_steps(current_app.db, program_id)
    if not pgm:
        return jsonify({"error": "Programm nicht gefunden"}), 404
    return jsonify(pgm)


@bp.post("/api/programs")
def create_program():
    """
    Neues Programm anlegen.

    {
        "name": "Kulen",
        "description": "...",
        "steps": [
            {"step": 1, "duration_h": 48, "temp": 18, "moist": 70,
             "description": "...", "modes_id": 1}
        ]
    }
    """
    data   = request.get_json(silent=True) or {}
    errors = _validate_program(data)
    if errors:
        return jsonify({"error": "Validierungsfehler", "details": errors}), 400

    db = current_app.db
    try:
        cursor = db.execute(
            "INSERT INTO riping_pgms (name, description, created_at) VALUES (?, ?, ?)",
            (data["name"].strip(), data.get("description", "").strip(), time.time()),
        )
        pgm_id = cursor.lastrowid
        _insert_steps(db, pgm_id, data["steps"])
        pgm = _get_program_with_steps(db, pgm_id)
        return jsonify(pgm), 201
    except Exception as e:
        if "UNIQUE" in str(e):
            return jsonify({"error": f"Name '{data['name']}' bereits vorhanden"}), 409
        return jsonify({"error": str(e)}), 500


@bp.put("/api/programs/<int:program_id>")
def update_program(program_id: int):
    """Programm und seine Schritte aktualisieren."""
    if not current_app.db.fetchone("SELECT id FROM riping_pgms WHERE id = ?", (program_id,)):
        return jsonify({"error": "Programm nicht gefunden"}), 404

    # Laufendes Programm nicht bearbeiten
    state = current_app.controller.state
    if state.active_program_id == program_id:
        return jsonify({"error": "Laufendes Programm kann nicht bearbeitet werden"}), 409

    data   = request.get_json(silent=True) or {}
    errors = _validate_program(data)
    if errors:
        return jsonify({"error": "Validierungsfehler", "details": errors}), 400

    db = current_app.db
    try:
        db.execute(
            "UPDATE riping_pgms SET name = ?, description = ? WHERE id = ?",
            (data["name"].strip(), data.get("description", "").strip(), program_id),
        )
        # Schritte ersetzen (CASCADE DELETE über FK)
        db.execute("DELETE FROM riping_steps WHERE riping_pgms_id = ?", (program_id,))
        _insert_steps(db, program_id, data["steps"])
        return jsonify(_get_program_with_steps(db, program_id))
    except Exception as e:
        if "UNIQUE" in str(e):
            return jsonify({"error": f"Name '{data['name']}' bereits vorhanden"}), 409
        return jsonify({"error": str(e)}), 500


@bp.delete("/api/programs/<int:program_id>")
def delete_program(program_id: int):
    if not current_app.db.fetchone("SELECT id FROM riping_pgms WHERE id = ?", (program_id,)):
        return jsonify({"error": "Programm nicht gefunden"}), 404

    state = current_app.controller.state
    if state.active_program_id == program_id:
        return jsonify({"error": "Laufendes Programm kann nicht gelöscht werden"}), 409

    current_app.db.execute("DELETE FROM riping_pgms WHERE id = ?", (program_id,))
    return jsonify({"ok": True})


@bp.post("/api/programs/<int:program_id>/activate")
def activate_program(program_id: int):
    """Startet ein Reifeprogramm."""
    state  = current_app.controller.state
    runner = current_app.program_runner

    ok = runner.activate_program(program_id, state)
    if not ok:
        return jsonify({"error": "Programm konnte nicht gestartet werden"}), 400

    current_app.state_manager.persist_config(state)
    current_app.state_manager.write_state_file(state)
    return jsonify({"ok": True, "program_id": program_id, "program_name": state.active_program_name})


@bp.post("/api/programs/stop")
def stop_program():
    """Stoppt das laufende Reifeprogramm."""
    state  = current_app.controller.state
    runner = current_app.program_runner

    if not state.active_program_id:
        return jsonify({"error": "Kein Programm aktiv"}), 409

    runner.stop_program(state)
    current_app.state_manager.write_state_file(state)
    return jsonify({"ok": True})


@bp.get("/api/programs/running")
def get_running():
    """Status des laufenden Programms."""
    status = current_app.program_runner.get_status(current_app.controller.state)
    return jsonify(status)


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _validate_program(data: dict) -> list[str]:
    errors = []
    if not data.get("name", "").strip():
        errors.append("name: darf nicht leer sein")
    steps = data.get("steps", [])
    if not steps:
        errors.append("steps: mindestens ein Schritt erforderlich")
    if len(steps) > 20:
        errors.append("steps: maximal 20 Schritte erlaubt")
    for i, step in enumerate(steps, 1):
        if not (1 <= int(step.get("duration_h", 0)) <= 9999):
            errors.append(f"Schritt {i}: duration_h muss zwischen 1 und 9999 liegen")
        if not (-50 <= float(step.get("temp", 999)) <= 60):
            errors.append(f"Schritt {i}: temp muss zwischen -50 und 60°C liegen")
        if not (0 <= float(step.get("moist", -1)) <= 100):
            errors.append(f"Schritt {i}: moist muss zwischen 0 und 100% liegen")
    return errors


def _insert_steps(db, pgm_id: int, steps: list) -> None:
    for i, step in enumerate(steps, 1):
        db.execute(
            """
            INSERT INTO riping_steps
                (riping_pgms_id, step, duration_h, temp, moist,
                 description, modes_id, target_weight_loss_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pgm_id,
                i,
                int(step.get("duration_h", 24)),
                float(step.get("temp", 14.0)),
                float(step.get("moist", 80.0)),
                step.get("description", "").strip(),
                int(step.get("modes_id", 1)),
                step.get("target_weight_loss_pct"),
            ),
        )
