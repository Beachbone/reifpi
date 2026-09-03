"""
test_phase3.py — Tests für Phase 3: Regellogik.

Prüft Controller-Logik, Kompressor-Schutzzeit, Safe-Mode,
Hysterese-Regelung und ProgramRunner — alles ohne Hardware.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import Database
from app.db.migrations import apply_migrations
from app.db.archive_writer import ArchiveWriter
from app.core.state import SystemState, StateManager
from app.core.gpio_manager import GpioManager
from app.core.sensors import SensorManager, SimulatedSensor, NoneSensor
from app.core.controller import ReifpiController, MODE_AUTO, MODE_MANUAL
from app.core.program_runner import ProgramRunner
from app.config import DevConfig
from tests.test_phase1 import get_default_config


def make_db():
    db = Database(":memory:")
    db.connect()
    apply_migrations(db, get_default_config(DevConfig))
    return db


def make_controller(db, state=None, intern_sensor=None, extern_sensor=None):
    if state is None:
        sm = StateManager("/tmp/test_ctrl_state.json", db)
        state = sm.load_from_db()
    else:
        sm = StateManager("/tmp/test_ctrl_state.json", db)

    gpio    = GpioManager(
        {"fanu": 27, "cool": 23, "fana": 17, "mois": 22, "heat": 24},
        dummy=True,
    )
    archive = ArchiveWriter(db, interval_secs=9999)
    sensors = SensorManager(
        intern_sensor=intern_sensor or SimulatedSensor(14.0, 80.0),
        extern_sensor=extern_sensor or SimulatedSensor(18.0, 65.0),
    )
    runner  = ProgramRunner(db)
    ctrl    = ReifpiController(
        state=state,
        state_manager=sm,
        sensor_manager=sensors,
        gpio=gpio,
        archive_writer=archive,
        program_runner=runner,
        compressor_protect_secs=5,   # Kurze Schutzzeit für Tests
    )
    return ctrl, state, gpio


# ── Temperaturregelung ────────────────────────────────────────────────────────

def test_cooling_activates():
    print("── Kühlung aktiviert sich ───────────────────────")
    db    = make_db()
    ctrl, state, gpio = make_controller(db)

    state.target_temp      = 14.0
    state.temp_hyst_cool   = 0.5
    state.temp_hyst_heat   = 1.0
    state.temp_intern      = 15.0   # Über Soll + Hysterese
    state.compressor_last_off = 0   # Schutzzeit abgelaufen

    ctrl._regulate_temperature()

    assert state.relay_cool is True,  "Kühlung sollte AN sein"
    assert state.relay_heat is False, "Heizung sollte AUS sein"
    print(f"  Temp {state.temp_intern}°C > {state.target_temp + state.temp_hyst_cool}°C → Kühlung AN ✓")
    db.close()


def test_cooling_blocked_by_protection():
    print("── Kompressor-Schutzzeit ────────────────────────")
    db    = make_db()
    ctrl, state, gpio = make_controller(db)

    state.target_temp       = 14.0
    state.temp_hyst_cool    = 0.5
    state.temp_intern       = 15.0
    state.compressor_last_off = time.time()   # Gerade erst ausgeschaltet

    ctrl._regulate_temperature()

    assert state.relay_cool is False,        "Kühlung sollte durch Schutzzeit gesperrt sein"
    assert state.compressor_protected is True
    print("  Schutzzeit aktiv → Kühlung gesperrt ✓")

    # Nach Ablauf der Schutzzeit
    state.compressor_last_off = time.time() - 10   # 10s > 5s Schutzzeit
    ctrl._regulate_temperature()
    assert state.relay_cool is True, "Kühlung sollte nach Schutzzeit freigegeben sein"
    print("  Schutzzeit abgelaufen → Kühlung freigegeben ✓")
    db.close()


def test_heating_activates():
    print("── Heizung aktiviert sich ───────────────────────")
    db    = make_db()
    ctrl, state, gpio = make_controller(db)

    state.target_temp    = 14.0
    state.temp_hyst_heat = 1.0
    state.temp_intern    = 12.5   # Unter Soll - Hysterese

    ctrl._regulate_temperature()

    assert state.relay_heat is True,  "Heizung sollte AN sein"
    assert state.relay_cool is False, "Kühlung sollte AUS sein"
    print(f"  Temp {state.temp_intern}°C < {state.target_temp - state.temp_hyst_heat}°C → Heizung AN ✓")
    db.close()


def test_no_simultaneous_heat_cool():
    print("── Heizung und Kühlung nie gleichzeitig ─────────")
    db    = make_db()
    ctrl, state, gpio = make_controller(db)

    state.target_temp       = 14.0
    state.temp_hyst_cool    = 0.5
    state.temp_hyst_heat    = 1.0
    state.temp_intern       = 12.5
    state.relay_cool        = True   # Bereits AN (Fehlerfall simulieren)
    state.compressor_last_off = 0

    ctrl._regulate_temperature()

    assert not (state.relay_heat and state.relay_cool), \
        "Heizung und Kühlung dürfen nie gleichzeitig AN sein"
    print("  Niemals Heat+Cool gleichzeitig ✓")
    db.close()


# ── Feuchtigkeitsregelung ────────────────────────────────────────────────────

def test_humidifier_activates():
    print("── Befeuchter aktiviert sich ────────────────────")
    db    = make_db()
    ctrl, state, gpio = make_controller(db)

    state.target_hum    = 80.0
    state.hum_hyst_mois = 5.0
    state.hum_intern    = 73.0   # Unter Soll - Hysterese

    ctrl._regulate_humidity()

    assert state.relay_mois is True, "Befeuchter sollte AN sein"
    assert state.relay_fanu is True, "Umluft sollte mit Befeuchter AN sein"
    print(f"  Hum {state.hum_intern}% < {state.target_hum - state.hum_hyst_mois}% → Befeuchter AN ✓")
    db.close()


def test_fana_activates_for_drying():
    print("── FANA aktiviert sich zum Trocknen ─────────────")
    db    = make_db()
    ctrl, state, gpio = make_controller(db)

    state.target_hum    = 80.0
    state.hum_hyst_fana = 2.0
    state.hum_intern    = 83.0   # Über Soll + Hysterese
    state.abs_hum_intern = None  # Kein Außensensor → Basis-Logik
    state.abs_hum_extern = None

    ctrl._regulate_humidity()

    assert state.relay_fana is True, "FANA sollte zum Trocknen AN sein"
    print(f"  Hum {state.hum_intern}% > {state.target_hum + state.hum_hyst_fana}% → FANA AN ✓")
    db.close()


def test_aht21_protection():
    print("── AHT21 Schutzfunktion ─────────────────────────")
    db    = make_db()
    ctrl, state, gpio = make_controller(db)

    state.target_hum          = 80.0
    state.hum_hyst_fana       = 2.0
    state.aht21_hyst_protect  = 2.0
    state.abs_hum_threshold   = 0.5
    state.hum_intern          = 83.5  # Über Soll → Basis würde FANA aktivieren
    state.abs_hum_intern      = 8.0
    state.abs_hum_extern      = 9.0   # Außen feuchter → Trocknen wäre kontraproduktiv

    ctrl._regulate_humidity()

    # Basis aktiviert FANA, AHT21-Korrektur muss es wieder deaktivieren
    assert state.relay_fana is False, \
        "FANA sollte durch AHT21-Schutz deaktiviert worden sein"
    print("  FANA durch AHT21-Schutz blockiert (Außen feuchter) ✓")
    db.close()


def test_aht21_humidify():
    print("── AHT21 Befeuchten via Frischluft ──────────────")
    db    = make_db()
    ctrl, state, gpio = make_controller(db)

    state.target_hum           = 80.0
    state.hum_hyst_mois        = 5.0
    state.hum_hyst_fana        = 2.0
    state.aht21_hyst_humidify  = 4.0
    state.abs_hum_threshold    = 0.5
    state.hum_intern           = 74.0  # Unter Soll - aht21_hyst_humidify (76)
    state.abs_hum_intern       = 7.0
    state.abs_hum_extern       = 9.0   # Außen deutlich feuchter

    ctrl._regulate_humidity()

    assert state.relay_fana is True, "FANA sollte zum Befeuchten AN sein"
    assert state.relay_fanu is True, "Umluft sollte AN sein"
    print("  FANA via AHT21 zum Befeuchten aktiviert ✓")
    db.close()


# ── Safe-Mode ─────────────────────────────────────────────────────────────────

def test_safe_mode_activation():
    print("── Safe-Mode ────────────────────────────────────")
    db    = make_db()
    ctrl, state, gpio = make_controller(
        db, intern_sensor=NoneSensor()
    )
    ctrl._fail_threshold = 3

    # 3x Sensor-Fehler simulieren
    for i in range(3):
        ctrl._read_sensors()

    assert state.safe_mode is True, "Safe-Mode sollte aktiv sein"
    assert state.sensor_fail_count >= 3

    # Safe-Mode anwenden
    state.relay_cool = True   # War AN
    state.relay_heat = True
    ctrl._apply_safe_mode()

    assert state.relay_heat is False, "Heizung muss im Safe-Mode AUS sein"
    assert state.relay_cool is False, "Kühlung muss im Safe-Mode AUS sein"
    assert state.relay_fanu is True,  "Umluft muss im Safe-Mode AN bleiben"

    # Safe-Mode zurücksetzen
    ctrl.reset_safe_mode()
    assert state.safe_mode is False
    assert state.sensor_fail_count == 0
    print("  Safe-Mode: Aktivierung, Zustand und Reset ✓")
    db.close()


# ── ProgramRunner ─────────────────────────────────────────────────────────────

def test_program_activate_and_advance():
    print("── ProgramRunner — Start und Schritt-Wechsel ────")
    db = make_db()

    # Programm anlegen
    db.execute(
        "INSERT INTO riping_pgms (name, description) VALUES (?, ?)",
        ("Testkulen", "Testreifeprogramm"),
    )
    pgm_id = db.fetchscalar("SELECT id FROM riping_pgms WHERE name='Testkulen'")

    db.execute(
        "INSERT INTO riping_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pgm_id, 1, 48, 18.0, 70.0, "Antrocknen", 1, None),
    )
    db.execute(
        "INSERT INTO riping_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pgm_id, 2, 720, 16.0, 75.0, "Reifung", 1, None),
    )

    sm      = StateManager("/tmp/test_pgm_state.json", db)
    state   = sm.load_from_db()
    runner  = ProgramRunner(db)

    # Programm starten
    ok = runner.activate_program(pgm_id, state)
    assert ok, "activate_program sollte True zurückgeben"
    assert state.active_program_id == pgm_id
    assert state.active_program_step == 1
    assert state.target_temp == 18.0
    assert state.target_hum == 70.0
    print(f"  Schritt 1: {state.target_temp}°C / {state.target_hum}%rF ✓")

    # Schritt 1 als abgelaufen markieren (end_time in Vergangenheit setzen)
    db.execute(
        "UPDATE riping_archive SET end_time = ? WHERE riping_pgm = ? AND riping_step = 1",
        (time.time() - 1, pgm_id),
    )
    state.program_step_end = time.time() - 1

    changed = runner.check_and_advance(state)
    assert changed, "check_and_advance sollte True zurückgeben"
    assert state.active_program_step == 2
    assert state.target_temp == 16.0
    assert state.target_hum == 75.0
    print(f"  Schritt 2: {state.target_temp}°C / {state.target_hum}%rF ✓")

    # Programm-Status
    status = runner.get_status(state)
    assert status["active"] is True
    assert status["total_steps"] == 2
    assert status["current_step"] == 2
    print(f"  Status: {status['program_name']}, Schritt {status['current_step']}/{status['total_steps']} ✓")

    # Programm stoppen
    runner.stop_program(state)
    assert state.active_program_id is None
    print("  Programm gestoppt ✓")

    db.close()


def test_program_completion():
    print("── ProgramRunner — Programmende ─────────────────")
    db = make_db()

    db.execute(
        "INSERT INTO riping_pgms (name, description) VALUES (?, ?)",
        ("Kurzprogramm", "Nur 1 Schritt"),
    )
    pgm_id = db.fetchscalar("SELECT id FROM riping_pgms WHERE name='Kurzprogramm'")
    db.execute(
        "INSERT INTO riping_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pgm_id, 1, 1, 14.0, 80.0, "Einziger Schritt", 1, None),
    )

    sm    = StateManager("/tmp/test_pgm2_state.json", db)
    state = sm.load_from_db()
    runner = ProgramRunner(db)

    runner.activate_program(pgm_id, state)

    # Schritt als abgelaufen markieren
    db.execute(
        "UPDATE riping_archive SET end_time = ? WHERE riping_pgm = ?",
        (time.time() - 1, pgm_id),
    )
    state.program_step_end = time.time() - 1

    changed = runner.check_and_advance(state)
    assert changed
    assert state.active_program_id is None, "Programm sollte abgeschlossen sein"
    print("  Programm nach letztem Schritt abgeschlossen ✓")

    db.close()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)

    print("\n╔══════════════════════════════════════════╗")
    print("║  Reife-Pi v2 — Phase 3 Smoke-Tests      ║")
    print("╚══════════════════════════════════════════╝\n")

    try:
        test_cooling_activates()
        test_cooling_blocked_by_protection()
        test_heating_activates()
        test_no_simultaneous_heat_cool()
        test_humidifier_activates()
        test_fana_activates_for_drying()
        test_aht21_protection()
        test_aht21_humidify()
        test_safe_mode_activation()
        test_program_activate_and_advance()
        test_program_completion()
        print("\n✅  Alle Tests bestanden.\n")
    except AssertionError as e:
        print(f"\n❌  Test fehlgeschlagen: {e}\n")
        sys.exit(1)
