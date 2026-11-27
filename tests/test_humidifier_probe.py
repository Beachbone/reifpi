"""
test_humidifier_probe.py — Aktiver Befeuchtertest (Wassertank leer?).

Simuliert den Schrank zyklusweise: Läuft der Befeuchter mit Wasser, steigt
die absolute Feuchte, sonst nur die natürliche Drift. Prüft Start nur in
ruhigen Phasen, bestanden/nicht bestanden, Wiederholung, Sperre erst nach
zwei Fehlschlägen, Abbruch durch Kühlung, Freigabe, Neustart, Migration.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.archive_writer import ArchiveWriter
from app.db.database import Database
from app.db.migrations import apply_migrations, CURRENT_SCHEMA_VERSION
from app.db.schema import ALL_TABLES, TABLE_CONFIG
from app.core.controller import ReifpiController, MODE_AUTO, MODE_MANUAL, MODE_TEMP_ONLY
from app.core.gpio_manager import GpioManager
from app.core.humidifier_probe import (
    HumidifierProbe, FIRST_DELAY_SECS, QUIET_SECS, RETRY_SECS, AFTER_RESET_SECS,
)
from app.core.program_runner import ProgramRunner
from app.core.sensors import SensorManager, SimulatedSensor, NoneSensor
from app.core.state import StateManager
from app.config import DevConfig
from tests.test_phase1 import get_default_config
from tests.test_phase3 import make_db

T0   = 1_800_000_000.0
TICK = 10
H    = 3600


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, subject, body, priority="high", tags=None):
        self.sent.append((subject, body))


def make():
    db    = make_db()
    sm    = StateManager("/tmp/test_probe_state.json", db)
    state = sm.load_from_db()
    note  = FakeNotifier()
    ctrl  = ReifpiController(
        state=state, state_manager=sm,
        sensor_manager=SensorManager(SimulatedSensor(15.0, 78.0), NoneSensor()),
        gpio=GpioManager({"fanu": 27, "cool": 23, "fana": 17, "mois": 22, "heat": 24}, dummy=True),
        archive_writer=ArchiveWriter(db, interval_secs=9999),
        program_runner=ProgramRunner(db),
        notifier=note,
        humidifier_probe=HumidifierProbe(db),
    )
    s = state
    s.mode, s.target_temp, s.target_hum = MODE_AUTO, 15.0, 78.0
    s.temp_intern, s.hum_intern, s.abs_hum_intern = 15.0, 78.0, 10.0
    s.abs_hum_extern = None
    s.mois_probe_last = T0 - 7 * H            # Test überfällig
    s.mois_probe_secs = 60                    # Zeitraster der Tests
    return db, ctrl, s, note, sm


def run(ctrl, s, start, secs, water=True, drift=0.0):
    """
    Regelt zyklusweise. Mit Wasser hebt der Befeuchter die absolute Feuchte
    um 0,1 g/m³ pro Zyklus (≈ 0,6 g/m³/min wie gemessen), sonst nur Drift.
    Gibt die Zeit nach dem letzten Zyklus zurück.
    """
    t = start
    while t < start + secs:
        if s.relay_mois and water:
            s.abs_hum_intern += 0.1
        s.abs_hum_intern += drift
        ctrl._regulate_step(t)
        t += TICK
    return t


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_defaults():
    print("── Standardwerte ────────────────────────────────")
    db, ctrl, s, note, sm = make()
    fresh = sm.load_from_db()
    assert (fresh.mois_probe_interval_h, fresh.mois_probe_secs, fresh.mois_probe_min_rise) == (6, 40, 0.3)
    print("  alle 6 h, 40 s, 0,3 g/m³ ✓")
    db.close()


def test_full_tank_passes():
    print("── Voller Tank → bestanden ──────────────────────")
    db, ctrl, s, note, sm = make()
    ctrl._regulate_step(T0)
    assert s.mois_probe_active and s.relay_mois and not s.relay_fana and s.relay_fanu
    t = run(ctrl, s, T0 + TICK, 50)
    assert s.relay_mois, "Puls läuft 60 s"
    t = run(ctrl, s, t, 20)
    assert s.mois_probe_active and not s.relay_mois, "Nachmessen ohne Befeuchter"
    t = run(ctrl, s, t, 60)
    assert not s.mois_probe_active and s.mois_probe_result.startswith("OK")
    assert not s.mois_fault and not note.sent
    assert db.fetchscalar("SELECT mois_probe_last FROM config") == s.mois_probe_last > T0
    assert s.mois_probe_next_due == s.mois_probe_last + 6 * H
    print(f"  {s.mois_probe_result}, nächster in 6 h ✓")
    db.close()


def test_empty_tank_locks_after_second_fail():
    print("── Leerer Tank → Sperre nach 2. Fehlschlag ──────")
    db, ctrl, s, note, sm = make()
    t = run(ctrl, s, T0, 130, water=False, drift=0.01)    # natürlich +0,13 in 2 min
    assert not s.mois_probe_active and s.mois_probe_fails == 1
    assert not s.mois_fault and not note.sent, "erster Fehlschlag sperrt noch nicht"
    assert s.mois_probe_next_due == t - TICK + RETRY_SECS
    assert "Wiederholung" in s.mois_probe_result

    run(ctrl, s, t, RETRY_SECS - 2 * TICK, water=False)
    assert not s.mois_probe_active, "vor Ablauf der Wartezeit kein neuer Test"
    run(ctrl, s, s.mois_probe_next_due, 130, water=False)
    assert s.mois_fault and not s.relay_mois
    assert db.fetchscalar("SELECT mois_fault_since FROM config") > 0
    assert len(note.sent) == 1 and note.sent[0][0] == "Befeuchter abgeschaltet"
    assert "Befeuchtertest" in note.sent[0][1] and "Wassertank" in note.sent[0][1]
    print(f"  gesperrt, gemeldet: {s.mois_fault_reason} ✓")
    db.close()


def test_one_fail_then_pass_resets():
    print("── Fehlschlag, dann bestanden → Zähler 0 ────────")
    db, ctrl, s, note, sm = make()
    t = run(ctrl, s, T0, 130, water=False)
    assert s.mois_probe_fails == 1
    run(ctrl, s, s.mois_probe_next_due, 130, water=True)
    assert s.mois_probe_fails == 0 and not s.mois_fault
    print("  kein Fehlalarm durch einzelnen Ausreißer ✓")
    db.close()


def test_waits_for_quiet_phase():
    print("── Start nur in ruhiger Phase ───────────────────")
    db, ctrl, s, note, sm = make()
    s.cool_last_off = T0 - QUIET_SECS + 60               # Kühlung erst vor 19 min aus
    ctrl._regulate_step(T0)
    assert not s.mois_probe_active, "kurz nach dem Kühlen"
    ctrl._regulate_step(T0 + 60)
    assert s.mois_probe_active, "nach 20 min ruhig"
    db.close()

    db, ctrl, s, note, sm = make()
    s.hum_intern = 79.5                                  # mehr als 1 %rF über Soll
    ctrl._regulate_step(T0)
    assert not s.mois_probe_active, "zu feucht"
    s.hum_intern = 81.0                                  # Frischluft trocknet
    ctrl._regulate_step(T0 + 10)
    assert s.relay_fana
    s.hum_intern = 77.9
    ctrl._regulate_step(T0 + 20)                         # Frischluft aus
    ctrl._regulate_step(T0 + 30)
    assert not s.mois_probe_active, "Frischluft gerade erst aus"
    ctrl._regulate_step(T0 + 220)
    assert s.mois_probe_active, "3 min nach der Frischluft"
    db.close()

    db, ctrl, s, note, sm = make()
    s.hum_intern, s.relay_mois = 70.0, True              # Befeuchter läuft schon
    ctrl._regulate_step(T0)
    assert s.relay_mois and not s.mois_probe_active
    print("  nicht nach Kühlen, Frischluft, bei laufendem Befeuchter oder zu feucht ✓")
    db.close()


def test_cooling_aborts_without_counting():
    print("── Kühlung springt an → Abbruch ohne Wertung ────")
    db, ctrl, s, note, sm = make()
    run(ctrl, s, T0, 30, water=False)
    assert s.mois_probe_active
    s.temp_intern = 16.5                                 # Kühlung schaltet ein
    run(ctrl, s, T0 + 30, 20, water=False)
    assert not s.mois_probe_active and s.mois_probe_fails == 0
    assert s.mois_probe_result == "abgebrochen (Kühlung)"
    assert s.relay_cool
    print("  abgebrochen, zählt nicht ✓")
    db.close()


def test_reset_tests_soon():
    print("── Freigabe → Test nach 10 min ──────────────────")
    db, ctrl, s, note, sm = make()
    run(ctrl, s, T0, 130, water=False)
    run(ctrl, s, s.mois_probe_next_due, 130, water=False)
    assert s.mois_fault
    ctrl.reset_humidifier_fault()
    assert not s.mois_fault and s.mois_probe_fails == 0
    assert s.mois_probe_next_due is not None
    print("  Termin kurz nach Freigabe ✓")
    db.close()


def test_other_modes_cancel():
    print("── Andere Modi brechen ab ───────────────────────")
    for mode in (MODE_MANUAL, MODE_TEMP_ONLY):
        db, ctrl, s, note, sm = make()
        ctrl._regulate_step(T0)
        assert s.mois_probe_active
        s.mode = mode
        if mode == MODE_TEMP_ONLY:
            ctrl._regulate_step(T0 + TICK)
        else:
            ctrl._cancel_probe()
        assert not s.mois_probe_active and s.mois_probe_fails == 0
        db.close()
    print("  Manuell und Nur-Temperatur ✓")


def test_disabled_and_schedule_after_restart():
    print("── Aus / Termin nach Neustart ───────────────────")
    db, ctrl, s, note, sm = make()
    s.mois_probe_interval_h = 0
    ctrl._regulate_step(T0)
    assert not s.mois_probe_active and s.mois_probe_next_due is None
    db.close()

    db, ctrl, s, note, sm = make()
    db.execute("UPDATE config SET mois_probe_last = ? WHERE id = 1", (T0 - 2 * H,))
    fresh = sm.load_from_db()
    HumidifierProbe(db).schedule(fresh, T0)
    assert fresh.mois_probe_next_due == T0 + 4 * H
    fresh.mois_probe_last, fresh.mois_probe_next_due = 0.0, None
    HumidifierProbe(db).schedule(fresh, T0)
    assert fresh.mois_probe_next_due == T0 + FIRST_DELAY_SECS
    print("  aus = nichts; Termin ab letztem Test bzw. 30 min nach Start ✓")
    db.close()


def test_migration_v9_to_v10():
    print("── Migration v9 → v10 ───────────────────────────")
    v10 = {"mois_probe_interval_h", "mois_probe_secs", "mois_probe_min_rise", "mois_probe_last"}
    db = Database(":memory:")
    db.connect()
    old = "\n".join(l for l in TABLE_CONFIG.splitlines()
                    if not any(l.strip().startswith(c) for c in v10))
    for st in ALL_TABLES:
        db.execute(old if st is TABLE_CONFIG else st)
    db.execute("INSERT INTO schema_version (version, applied_at) VALUES (9, 0)")
    db.execute("INSERT INTO config (id) VALUES (1)")
    apply_migrations(db, get_default_config(DevConfig))
    row = db.fetchone("SELECT * FROM config")
    assert row["mois_probe_interval_h"] == 6 and row["mois_probe_min_rise"] == 0.3
    assert db.fetchscalar("SELECT MAX(version) FROM schema_version") == CURRENT_SCHEMA_VERSION >= 10
    print("  4 Spalten, Test an ✓")
    db.close()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    try:
        for t in tests:
            t()
        print("\n✅  Alle Tests bestanden.\n")
    except AssertionError as e:
        print(f"\n❌  Test fehlgeschlagen: {e}\n")
        sys.exit(1)
