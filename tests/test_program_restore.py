"""
test_program_restore.py — Reifeprogramm nach Neustart und bei Schrittwechsel.

Prüft, dass nach einem Neustart der tatsächlich laufende Schritt
wiederhergestellt wird (nicht der letzte, vorab angelegte) und dass die
Sollwerte eines neuen Schritts das Neuladen der Konfiguration überstehen.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.state import StateManager
from app.core.program_runner import ProgramRunner
from tests.test_phase3 import make_db, make_controller


def add_program(db) -> int:
    """Programm mit drei Schritten wie 'Domaca slavonska Kobasica'."""
    db.execute("INSERT INTO riping_pgms (name) VALUES ('Testwurst')")
    pid = db.fetchscalar("SELECT id FROM riping_pgms WHERE name = 'Testwurst'")
    for step, hours, temp, moist in ((1, 48, 5.0, 80.0), (2, 48, 9.0, 80.0), (3, 480, 14.0, 80.0)):
        db.execute(
            "INSERT INTO riping_steps (riping_pgms_id, step, duration_h, temp, moist) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, step, hours, temp, moist),
        )
    return pid


def start_program(db, pid):
    sm    = StateManager("/tmp/test_restore_state.json", db)
    state = sm.load_from_db()
    assert ProgramRunner(db).activate_program(pid, state)
    sm.persist_config(state)
    return sm


def test_restore_right_after_start():
    print("── Neustart direkt nach Programmstart ───────────")
    db  = make_db()
    pid = add_program(db)
    sm  = start_program(db, pid)

    fresh = sm.load_from_db()
    sm.restore_after_poweroff(fresh)
    assert fresh.active_program_id == pid
    assert fresh.active_program_step == 1, f"Schritt {fresh.active_program_step} statt 1"
    assert fresh.target_temp == 5.0
    assert fresh.program_step_end - time.time() > 47 * 3600
    print("  Schritt 1 wiederhergestellt ✓")
    db.close()


def test_restore_in_second_step():
    print("── Neustart während Schritt 2 ───────────────────")
    db  = make_db()
    pid = add_program(db)
    sm  = start_program(db, pid)

    # Programm vor 50 h gestartet: Schritt 1 lief ab, Schritt 2 läuft
    shift = 50 * 3600
    db.execute(
        "UPDATE riping_archive SET start_time = start_time - ?, end_time = end_time - ?",
        (shift, shift),
    )
    db.execute(
        "UPDATE riping_archive SET time_step_ended = end_time "
        "WHERE riping_pgm = ? AND riping_step = 1",
        (pid,),
    )

    fresh = sm.load_from_db()
    sm.restore_after_poweroff(fresh)
    assert fresh.active_program_step == 2, f"Schritt {fresh.active_program_step} statt 2"
    print("  Schritt 2 wiederhergestellt ✓")
    db.close()


def test_step_change_survives_reload():
    print("── Schrittwechsel bleibt gespeichert ────────────")
    db  = make_db()
    pid = add_program(db)
    start_program(db, pid)

    ctrl, state, _ = make_controller(db)
    state.mode = 1
    sm = StateManager("/tmp/test_restore_state.json", db)
    sm.restore_after_poweroff(state)
    assert state.active_program_step == 1

    state.program_step_end = time.time() - 1   # Schritt 1 abgelaufen
    ctrl._run_cycle()
    assert state.active_program_step == 2
    assert state.target_temp == 9.0

    ctrl._run_cycle()                            # lädt Konfiguration neu
    assert state.target_temp == 9.0, f"Sollwert auf {state.target_temp} zurückgefallen"
    assert db.fetchscalar("SELECT target_temp FROM config WHERE id = 1") == 9.0
    print("  9 °C bleiben nach dem nächsten Zyklus ✓")
    db.close()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    try:
        test_restore_right_after_start()
        test_restore_in_second_step()
        test_step_change_survives_reload()
        print("\n✅  Alle Tests bestanden.\n")
    except AssertionError as e:
        print(f"\n❌  Test fehlgeschlagen: {e}\n")
        sys.exit(1)
