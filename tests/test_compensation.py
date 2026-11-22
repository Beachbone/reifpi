"""
test_compensation.py — Tests für die Nachlauf-Kompensation.

Prüft Offset, Vorhaltzeit (Steigungs-Prognose), Mindestpausen,
gegenseitige Sperren Heizen/Kühlen, Migration v6 → v7 und den
Modus "nur Temperatur" — alles ohne Hardware.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import Database
from app.db.migrations import apply_migrations, CURRENT_SCHEMA_VERSION
from app.db.schema import ALL_TABLES, TABLE_CONFIG
from app.core.state import StateManager, COMPENSATION_FIELDS
from app.core.controller import MODE_TEMP_ONLY
from app.config import DevConfig
from tests.test_phase1 import get_default_config
from tests.test_phase3 import make_db, make_controller


def setup_temp(state, temp, rate=None):
    """Sollwert 15 °C, Hysteresen 1,0 / 1,5 wie auf dem Live-System."""
    state.target_temp         = 15.0
    state.temp_hyst_cool      = 1.0
    state.temp_hyst_heat      = 1.5
    state.temp_intern         = temp
    state.temp_rate           = rate
    state.compressor_last_off = 0


# ── Defaults ──────────────────────────────────────────────────────────────────

def test_defaults_keep_old_behaviour():
    print("── Defaults 0 = altes Verhalten ─────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)

    for attr, _cast in COMPENSATION_FIELDS:
        assert getattr(state, attr) == 0, f"{attr} sollte 0 sein"

    setup_temp(state, 16.1, rate=-1.0)
    ctrl._regulate_temperature()
    assert state.relay_cool is True

    state.temp_intern = 15.1   # Steigung wirkt ohne Vorhaltzeit nicht
    ctrl._regulate_temperature()
    assert state.relay_cool is True, "Kühlung erst bei Soll aus"

    state.temp_intern = 15.0
    ctrl._regulate_temperature()
    assert state.relay_cool is False
    print("  Ohne Parameter Abschaltung genau bei Soll ✓")
    db.close()


# ── Offset ────────────────────────────────────────────────────────────────────

def test_cool_offset():
    print("── Offset Kühlen ────────────────────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 16.1)
    state.temp_offset_cool = 0.5

    ctrl._regulate_temperature()
    assert state.relay_cool is True

    state.temp_intern = 15.6
    ctrl._regulate_temperature()
    assert state.relay_cool is True, "Über Soll + Offset weiter kühlen"

    state.temp_intern = 15.5
    ctrl._regulate_temperature()
    assert state.relay_cool is False, "Bei Soll + Offset aus"
    print("  Kühlung aus bei 15,5 °C statt 15,0 °C ✓")
    db.close()


def test_offset_clamped_to_hysteresis():
    print("── Offset auf Hysterese begrenzt ────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 16.1)
    state.temp_offset_cool = 3.0   # größer als Hysterese 1,0

    ctrl._regulate_temperature()
    assert state.relay_cool is True, "Einschalten muss weiter möglich sein"

    state.temp_intern = 16.05
    ctrl._regulate_temperature()
    assert state.relay_cool is True

    state.temp_intern = 16.0
    ctrl._regulate_temperature()
    assert state.relay_cool is False
    print("  Offset 3,0 wirkt wie 1,0 (Hysterese) ✓")
    db.close()


def test_heat_and_mois_offset():
    print("── Offset Heizen / Befeuchten ───────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 13.4)
    state.temp_offset_heat = 0.8

    ctrl._regulate_temperature()
    assert state.relay_heat is True
    state.temp_intern = 14.2
    ctrl._regulate_temperature()
    assert state.relay_heat is False, "Heizung aus bei Soll − Offset"

    state.target_hum      = 77.0
    state.hum_hyst_mois   = 2.0
    state.hum_offset_mois = 1.0
    state.hum_intern      = 74.9
    ctrl._regulate_humidity()
    assert state.relay_mois is True
    state.hum_intern = 76.0
    ctrl._regulate_humidity()
    assert state.relay_mois is False, "Befeuchter aus bei Soll − Offset"
    print("  Heizung aus bei 14,2 °C, Befeuchter bei 76 % ✓")
    db.close()


# ── Vorhaltzeit ───────────────────────────────────────────────────────────────

def test_cool_lead():
    print("── Vorhaltzeit Kühlen ───────────────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 16.1)
    state.lead_secs_cool = 120
    ctrl._regulate_temperature()
    assert state.relay_cool is True

    # Fällt mit 0,4 K/min → Prognose in 2 min: 15,6 − 0,8 = 14,8 ≤ 15,0
    state.temp_intern = 15.6
    state.temp_rate   = -0.4
    ctrl._regulate_temperature()
    assert state.relay_cool is False, "Prognose unter Soll → aus"

    # Steigende Temperatur darf das Abschalten nicht verschieben
    state.temp_intern = 15.0
    state.temp_rate   = 0.5
    state.relay_cool  = True
    ctrl._regulate_temperature()
    assert state.relay_cool is False
    print("  Kühlung aus bei 15,6 °C und −0,4 K/min ✓")
    db.close()


def test_heat_lead():
    print("── Vorhaltzeit Heizen ───────────────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 13.4)
    state.lead_secs_heat = 140
    ctrl._regulate_temperature()
    assert state.relay_heat is True

    # Steigt mit 0,6 K/min → Prognose 13,7 + 1,4 = 15,1 ≥ 15,0
    state.temp_intern = 13.7
    state.temp_rate   = 0.6
    ctrl._regulate_temperature()
    assert state.relay_heat is False
    print("  Heizung aus bei 13,7 °C und +0,6 K/min ✓")
    db.close()


def test_mois_lead():
    print("── Vorhaltzeit Befeuchten ───────────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    state.target_hum     = 77.0
    state.hum_hyst_mois  = 2.0
    state.lead_secs_mois = 60
    state.hum_intern     = 74.5
    ctrl._regulate_humidity()
    assert state.relay_mois is True

    state.hum_intern = 75.5
    state.hum_rate   = 1.0       # Prognose 76,5 < 77 → bleibt an
    ctrl._regulate_humidity()
    assert state.relay_mois is True
    state.hum_rate   = 2.0       # Prognose 77,5 ≥ 77 → aus
    ctrl._regulate_humidity()
    assert state.relay_mois is False
    assert state.mois_last_off > 0
    print("  Befeuchter aus bei 75,5 % und +2 %/min ✓")
    db.close()


# ── Mindestpausen und Sperren ────────────────────────────────────────────────

def test_min_off_heat_and_mois():
    print("── Mindestpause Heizen / Befeuchten ─────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 13.0)
    state.min_off_secs_heat = 60
    state.heat_last_off     = time.time()
    ctrl._regulate_temperature()
    assert state.relay_heat is False, "Mindestpause läuft"
    state.heat_last_off = time.time() - 61
    ctrl._regulate_temperature()
    assert state.relay_heat is True

    state.target_hum        = 77.0
    state.hum_hyst_mois     = 2.0
    state.min_off_secs_mois = 30
    state.mois_last_off     = time.time()
    state.hum_intern        = 74.0
    ctrl._regulate_humidity()
    assert state.relay_mois is False
    state.mois_last_off = time.time() - 31
    ctrl._regulate_humidity()
    assert state.relay_mois is True
    print("  Pausen werden eingehalten ✓")
    db.close()


def test_min_off_cool_extends_protection():
    print("── Mindestpause Kühlen > Schutzzeit ─────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)   # Schutzzeit im Test: 5 s
    setup_temp(state, 16.1)
    state.min_off_secs_cool   = 60
    state.compressor_last_off = time.time() - 10
    ctrl._regulate_temperature()
    assert state.relay_cool is False
    assert state.compressor_protected is True

    state.compressor_last_off = time.time() - 61
    ctrl._regulate_temperature()
    assert state.relay_cool is True
    print("  60 s statt 5 s Pause ✓")
    db.close()


def test_lock_heat_after_cool():
    print("── Heizsperre nach Kühlende ─────────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 16.1)
    state.lock_secs_heat_after_cool = 600
    ctrl._regulate_temperature()
    assert state.relay_cool is True

    state.temp_intern = 15.0
    ctrl._regulate_temperature()
    assert state.relay_cool is False
    assert state.cool_last_off > 0

    state.temp_intern = 13.0      # Unterschwinger
    ctrl._regulate_temperature()
    assert state.relay_heat is False, "Heizung während Sperre aus"

    state.cool_last_off = time.time() - 601
    ctrl._regulate_temperature()
    assert state.relay_heat is True
    print("  Heizung erst nach 10 min Sperre ✓")
    db.close()


def test_lock_cool_after_heat():
    print("── Kühlsperre nach Heizende ─────────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 13.0)
    state.lock_secs_cool_after_heat = 300
    ctrl._regulate_temperature()
    assert state.relay_heat is True

    state.temp_intern = 15.0
    ctrl._regulate_temperature()
    assert state.relay_heat is False

    state.temp_intern = 16.2      # Überschwinger
    ctrl._regulate_temperature()
    assert state.relay_cool is False, "Kühlung während Sperre aus"

    state.heat_last_off = time.time() - 301
    ctrl._regulate_temperature()
    assert state.relay_cool is True
    print("  Kühlung erst nach 5 min Sperre ✓")
    db.close()


def test_restart_does_not_lock_heater():
    print("── Neustart sperrt Heizung nicht ────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 13.0)
    state.lock_secs_heat_after_cool = 600
    state.compressor_last_off = time.time()   # wie restore_after_poweroff()
    ctrl._regulate_temperature()
    assert state.relay_heat is True
    print("  Sperre nur nach echtem Kühlende ✓")
    db.close()


def test_heater_start_records_compressor_off():
    print("── Heizstart setzt Kompressor-Schutzzeit ────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 13.0)
    state.relay_cool = True       # z.B. Sollwert-Sprung nach oben
    ctrl._regulate_temperature()
    assert state.relay_heat is True
    assert state.relay_cool is False
    assert time.time() - state.compressor_last_off < 2
    print("  compressor_last_off gesetzt ✓")
    db.close()


# ── Modus "nur Temperatur" ────────────────────────────────────────────────────

def test_temp_only_mode_switches_humidity_off():
    print("── Modus nur Temperatur ─────────────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    setup_temp(state, 16.1)
    state.mode       = MODE_TEMP_ONLY
    state.relay_mois = True        # aus vorherigem Automatik-Betrieb
    state.relay_fana = True
    state.relay_fanu = False
    ctrl._regulate()
    assert state.relay_mois is False
    assert state.relay_fana is False
    assert state.relay_fanu is True, "Umluft läuft mit der Kühlung"
    print("  Befeuchter/Frischluft aus, Umluft folgt Kühlung ✓")
    db.close()


# ── Steigung ──────────────────────────────────────────────────────────────────

def test_rate_calculation():
    print("── Steigung per Regression ──────────────────────")
    db = make_db()
    ctrl, state, _ = make_controller(db)
    t0 = 1000.0

    ctrl._update_rates(t0, 15.0, 75.0)
    ctrl._update_rates(t0 + 10, 15.1, 75.5)
    assert state.temp_rate is None, "Zu kurzes Fenster → keine Steigung"

    for i in range(2, 10):   # 10-s-Takt, +0,1 K und +0,5 % je Messung
        ctrl._update_rates(t0 + 10 * i, 15.0 + 0.1 * i, 75.0 + 0.5 * i)
    assert abs(state.temp_rate - 0.6) < 1e-6, state.temp_rate
    assert abs(state.hum_rate - 3.0) < 1e-6, state.hum_rate

    # Alte Werte fallen aus dem 90-s-Fenster
    ctrl._update_rates(t0 + 300, 15.9, 79.5)
    assert len(ctrl._history) == 1
    assert state.temp_rate is None
    print("  0,6 K/min und 3 %/min erkannt, Fenster begrenzt ✓")
    db.close()


# ── Datenbank ─────────────────────────────────────────────────────────────────

def test_migration_v6_to_v7():
    print("── Migration v6 → v7 ────────────────────────────")
    v7_cols = {attr for attr, _ in COMPENSATION_FIELDS}

    db = Database(":memory:")
    db.connect()
    old_config = "\n".join(
        line for line in TABLE_CONFIG.splitlines()
        if not any(line.strip().startswith(col) for col in v7_cols)
    )
    for statement in ALL_TABLES:
        db.execute(old_config if statement is TABLE_CONFIG else statement)
    db.execute("INSERT INTO schema_version (version, applied_at) VALUES (6, 0)")
    db.execute("INSERT INTO config (id, target_temp) VALUES (1, 15.0)")

    cols = {r["name"] for r in db.fetchall("PRAGMA table_info(config)")}
    assert not (v7_cols & cols), "Testaufbau: v6 ohne neue Spalten"

    apply_migrations(db, get_default_config(DevConfig))

    cols = {r["name"] for r in db.fetchall("PRAGMA table_info(config)")}
    assert v7_cols <= cols, f"Fehlende Spalten: {v7_cols - cols}"
    version = db.fetchscalar("SELECT MAX(version) FROM schema_version")
    assert version == CURRENT_SCHEMA_VERSION == 7
    row = db.fetchone("SELECT * FROM config WHERE id = 1")
    assert all(row[c] == 0 for c in v7_cols)
    assert row["target_temp"] == 15.0, "Bestehende Werte bleiben erhalten"
    print("  11 Spalten ergänzt, Werte erhalten ✓")
    db.close()


def test_persist_and_reload():
    print("── Speichern und Neuladen ───────────────────────")
    db = make_db()
    sm = StateManager("/tmp/test_comp_state.json", db)
    state = sm.load_from_db()
    state.temp_offset_cool          = 0.4
    state.lead_secs_heat            = 140
    state.lock_secs_heat_after_cool = 600
    sm.persist_config(state)

    fresh = sm.load_from_db()
    assert fresh.temp_offset_cool == 0.4
    assert fresh.lead_secs_heat == 140
    assert fresh.lock_secs_heat_after_cool == 600

    db.execute("UPDATE config SET min_off_secs_mois = 45 WHERE id = 1")
    assert sm.reload_config(fresh) is True
    assert fresh.min_off_secs_mois == 45
    print("  Werte überleben persist/load/reload ✓")
    db.close()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)

    print("\n╔══════════════════════════════════════════╗")
    print("║  Reifpi — Nachlauf-Kompensation          ║")
    print("╚══════════════════════════════════════════╝\n")

    try:
        test_defaults_keep_old_behaviour()
        test_cool_offset()
        test_offset_clamped_to_hysteresis()
        test_heat_and_mois_offset()
        test_cool_lead()
        test_heat_lead()
        test_mois_lead()
        test_min_off_heat_and_mois()
        test_min_off_cool_extends_protection()
        test_lock_heat_after_cool()
        test_lock_cool_after_heat()
        test_restart_does_not_lock_heater()
        test_heater_start_records_compressor_off()
        test_temp_only_mode_switches_humidity_off()
        test_rate_calculation()
        test_migration_v6_to_v7()
        test_persist_and_reload()
        print("\n✅  Alle Tests bestanden.\n")
    except AssertionError as e:
        print(f"\n❌  Test fehlgeschlagen: {e}\n")
        sys.exit(1)
