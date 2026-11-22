"""
test_air_exchange.py — Tests für den geplanten Luftwechsel.

Prüft Start nach Intervall, weiterlaufende Regelung, reguläres Ende,
verkürztes Lüften nur bei feuchterer Außenluft, Abbruch bei starken
Änderungen, Wiederholung nach 1/4 Intervall, Neustart und Moduswechsel.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.archive_writer import ArchiveWriter
from app.db.migrations import apply_migrations, CURRENT_SCHEMA_VERSION
from app.db.database import Database
from app.db.schema import ALL_TABLES, TABLE_CONFIG
from app.core.air_exchange import AirExchange, ACTIVE, ENDED, IDLE, FIRST_RUN_DELAY_SECS
from app.core.controller import ReifpiController, MODE_AUTO, MODE_MANUAL
from app.core.gpio_manager import GpioManager
from app.core.program_runner import ProgramRunner
from app.core.sensors import SensorManager, SimulatedSensor
from app.core.state import StateManager, AIR_FIELDS
from app.config import DevConfig
from tests.test_phase1 import get_default_config
from tests.test_phase3 import make_db

T0 = 1_800_000_000.0   # feste Startzeit für reproduzierbare Abläufe
H  = 3600


def make(interval_h=24):
    db    = make_db()
    sm    = StateManager("/tmp/test_air_state.json", db)
    state = sm.load_from_db()
    ctrl  = ReifpiController(
        state=state,
        state_manager=sm,
        sensor_manager=SensorManager(SimulatedSensor(15.0, 77.0), SimulatedSensor(15.0, 70.0)),
        gpio=GpioManager({"fanu": 27, "cool": 23, "fana": 17, "mois": 22, "heat": 24}, dummy=True),
        archive_writer=ArchiveWriter(db, interval_secs=9999),
        program_runner=ProgramRunner(db),
        compressor_protect_secs=5,
        air_exchange=AirExchange(db),
    )
    s = state
    s.mode, s.target_temp, s.target_hum = MODE_AUTO, 15.0, 77.0
    s.temp_hyst_cool, s.temp_hyst_heat, s.hum_hyst_mois = 1.0, 1.5, 2.0
    s.air_interval_h, s.air_duration_min, s.air_short_pct = interval_h, 5, 50
    s.air_abort_temp_k, s.air_abort_hum_pct = 2.0, 10.0
    s.abs_hum_threshold = 0.5
    set_climate(s, 15.0, 77.0)
    s.air_last_exchange = T0 - 25 * H   # überfällig
    return db, ctrl, s


def set_climate(s, temp, hum, temp_ext=15.0, ah_in=9.8, ah_out=9.8):
    s.temp_intern, s.hum_intern = temp, hum
    s.temp_extern = temp_ext
    s.abs_hum_intern, s.abs_hum_extern = ah_in, ah_out


def tick(ctrl, now):
    """Ein Regelzyklus ohne Sensoren/GPIO (wie _run_cycle Schritt 5)."""
    air = ctrl._update_air_exchange(now)
    ctrl._regulate()
    if air == ACTIVE:
        ctrl._state.relay_fana = True
        ctrl._state.relay_fanu = True


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_disabled_by_default():
    print("── Ohne Intervall kein Luftwechsel ──────────────")
    db, ctrl, s = make(interval_h=0)
    assert ctrl._update_air_exchange(T0) == IDLE
    assert s.air_next_due is None
    fresh = StateManager("/tmp/x.json", db).load_from_db()
    assert fresh.air_interval_h == 0, "Default muss aus sein"
    print("  aus ✓")
    db.close()


def test_full_exchange_keeps_regulating():
    print("── Vollständiger Luftwechsel, Regelung läuft ─────")
    db, ctrl, s = make()
    s.compressor_last_off = 0
    s.temp_intern = 16.5                         # zu warm → Kühlung
    tick(ctrl, T0)
    assert s.air_active and not s.air_short
    assert s.relay_fana and s.relay_fanu
    assert s.relay_cool, "Regelung läuft weiter und kühlt"

    s.temp_intern, s.hum_intern = 15.0, 74.0     # zu trocken → Befeuchter
    tick(ctrl, T0 + 4 * 60)
    assert s.air_active and s.relay_mois and s.relay_fana, "befeuchtet während des Lüftens"

    tick(ctrl, T0 + 5 * 60)
    assert not s.air_active and s.air_last_result == "vollständig"
    assert s.air_next_due == T0 + 5 * 60 + 24 * H
    assert db.fetchscalar("SELECT air_last_exchange FROM config") == T0 + 5 * 60
    assert not s.relay_fana or s.hum_intern > s.target_hum + s.hum_hyst_fana
    print("  5 min, Kühlung/Befeuchter regeln mit, nächster in 24 h ✓")
    db.close()


def test_not_due_yet():
    print("── Noch nicht fällig ────────────────────────────")
    db, ctrl, s = make()
    s.air_last_exchange = T0 - 2 * H
    assert ctrl._update_air_exchange(T0) == IDLE
    assert s.air_next_due == T0 + 22 * H
    print("  wartet bis letzter + 24 h ✓")
    db.close()


def test_first_run_after_enabling():
    print("── Erster Lauf nach Aktivierung ─────────────────")
    db, ctrl, s = make()
    s.air_last_exchange = 0
    assert ctrl._update_air_exchange(T0) == IDLE
    assert s.air_next_due == T0 + FIRST_RUN_DELAY_SECS
    assert ctrl._update_air_exchange(T0 + FIRST_RUN_DELAY_SECS) == ACTIVE
    print("  startet nach 10 min ✓")
    db.close()


def test_shortened_when_outside_wetter():
    print("── Außenluft feuchter → verkürzt ────────────────")
    db, ctrl, s = make()
    set_climate(s, 15.0, 77.5, ah_in=9.8, ah_out=11.0)
    tick(ctrl, T0)
    assert s.air_active and s.air_short
    assert s.air_end == T0 + 150, "50 % von 5 min"
    tick(ctrl, T0 + 150)
    assert s.air_last_result == "verkürzt"
    assert s.air_next_due == T0 + 150 + 6 * H, "Wiederholung nach 1/4 Intervall"
    assert s.air_last_exchange == T0 + 150
    print("  2,5 min, Wiederholung nach 6 h ✓")
    db.close()


def test_wetter_outside_fine_when_too_dry():
    print("── Außenluft feuchter, innen zu trocken → voll ──")
    db, ctrl, s = make()
    set_climate(s, 15.0, 72.0, ah_in=9.0, ah_out=11.0)
    tick(ctrl, T0)
    assert s.air_active and not s.air_short
    print("  volle Dauer ✓")
    db.close()


def test_temperature_difference_is_normal():
    print("── Temperaturdifferenz außen → volle Dauer ──────")
    db, ctrl, s = make()
    s.target_temp = 2.0
    set_climate(s, 2.0, 77.0, temp_ext=18.0)
    tick(ctrl, T0)
    assert s.air_active and not s.air_short
    print("  2 °C Soll, 18 °C außen → voll ✓")
    db.close()


def test_drier_outside_is_fine():
    print("── Außenluft trockener → volle Dauer ────────────")
    db, ctrl, s = make()
    set_climate(s, 15.0, 74.0, ah_in=9.8, ah_out=7.0)
    tick(ctrl, T0)
    assert s.air_active and not s.air_short
    assert s.relay_mois, "Befeuchter gleicht aus"
    print("  voll, Befeuchter läuft ✓")
    db.close()


def test_abort_on_temperature_change():
    print("── Abbruch bei Temperaturänderung ───────────────")
    db, ctrl, s = make()
    tick(ctrl, T0)
    assert s.air_active
    s.temp_intern = 17.2                         # +2,2 K > 2,0 K
    tick(ctrl, T0 + 60)
    assert not s.air_active
    assert s.air_last_result.startswith("abgebrochen (Temperatur")
    assert s.relay_cool, "Regelung arbeitet nach dem Abbruch normal weiter"
    assert not s.relay_fana
    assert s.air_next_due == T0 + 60 + 6 * H
    assert s.air_last_exchange == T0 - 25 * H, "Abbruch zählt nicht als Luftwechsel"
    print("  Abbruch, Kühlung an, Wiederholung nach 6 h ✓")
    db.close()


def test_abort_on_humidity_change():
    print("── Abbruch bei Feuchteänderung ──────────────────")
    db, ctrl, s = make()
    tick(ctrl, T0)
    s.hum_intern = 66.0                          # −11 %rF > 10 %rF
    tick(ctrl, T0 + 60)
    assert not s.air_active and "Feuchte" in s.air_last_result
    assert s.relay_mois, "Befeuchter läuft"
    print("  Abbruch, Befeuchter an ✓")
    db.close()


def test_manual_mode_cancels():
    print("── Moduswechsel bricht ab ───────────────────────")
    db, ctrl, s = make()
    tick(ctrl, T0)
    assert s.air_active
    s.mode = MODE_MANUAL
    ctrl._cancel_air_exchange()
    assert not s.air_active and not s.relay_fana
    assert "Moduswechsel" in s.air_last_result
    print("  abgebrochen ✓")
    db.close()


def test_restart_keeps_schedule():
    print("── Neustart behält Zeitplan ─────────────────────")
    db, ctrl, s = make()
    tick(ctrl, T0)
    tick(ctrl, T0 + 5 * 60)                      # erfolgreich beendet
    fresh = StateManager("/tmp/x.json", db).load_from_db()
    fresh.air_interval_h = 24
    AirExchange(db).update(fresh, T0 + 3 * H)
    assert fresh.air_next_due == T0 + 5 * 60 + 24 * H
    print("  nächster Termin aus DB ✓")
    db.close()


def test_interval_change_reschedules():
    print("── Intervalländerung wirkt sofort ───────────────")
    db, ctrl, s = make()
    s.air_last_exchange = T0 - 2 * H
    ctrl._update_air_exchange(T0)
    assert s.air_next_due == T0 + 22 * H
    ctrl.update_config(air_interval_h=6)
    assert abs(s.air_next_due - (T0 + 4 * H)) < 1, "neu ab letztem Luftwechsel"
    ctrl.update_config(air_interval_h=0)
    assert s.air_next_due is None
    print("  24 h → 6 h: Termin sofort neu ✓")
    db.close()


def test_full_control_cycle():
    print("── Kompletter Regelzyklus mit GPIO ──────────────")
    import time
    db, ctrl, s = make()
    s.air_last_exchange = time.time() - 25 * H
    db.execute("UPDATE config SET air_interval_h = 24, mode = 1 WHERE id = 1")
    ctrl._run_cycle()
    assert s.air_active, "Luftwechsel muss im echten Zyklus starten"
    assert ctrl._gpio.get_state("fana") and ctrl._gpio.get_state("fanu")
    db.execute("UPDATE config SET mode = 3 WHERE id = 1")   # Handbetrieb
    ctrl._run_cycle()
    assert not s.air_active and "Moduswechsel" in s.air_last_result
    print("  Relais folgen, Moduswechsel bricht ab ✓")
    db.close()


def test_settings_persist_and_reload():
    print("── Einstellungen speichern/neu laden ────────────")
    db, ctrl, s = make()
    sm = StateManager("/tmp/x.json", db)
    s.air_interval_h, s.air_duration_min, s.air_abort_temp_k = 12, 8, 1.5
    sm.persist_config(s)
    fresh = sm.load_from_db()
    assert (fresh.air_interval_h, fresh.air_duration_min, fresh.air_abort_temp_k) == (12, 8, 1.5)
    print("  ✓")
    db.close()


def test_migration_v7_to_v8():
    print("── Migration v7 → v8 ────────────────────────────")
    v8 = {a for a, _ in AIR_FIELDS} | {"air_last_exchange"}
    db = Database(":memory:")
    db.connect()
    old = "\n".join(l for l in TABLE_CONFIG.splitlines()
                    if not any(l.strip().startswith(c) for c in v8))
    for st in ALL_TABLES:
        db.execute(old if st is TABLE_CONFIG else st)
    db.execute("INSERT INTO schema_version (version, applied_at) VALUES (7, 0)")
    db.execute("INSERT INTO config (id) VALUES (1)")
    apply_migrations(db, get_default_config(DevConfig))
    cols = {r["name"] for r in db.fetchall("PRAGMA table_info(config)")}
    assert v8 <= cols, v8 - cols
    row = db.fetchone("SELECT * FROM config")
    assert row["air_interval_h"] == 0 and row["air_duration_min"] == 5
    assert db.fetchscalar("SELECT MAX(version) FROM schema_version") == CURRENT_SCHEMA_VERSION == 8
    print("  6 Spalten ergänzt, Luftwechsel aus ✓")
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
