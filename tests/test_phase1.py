"""
test_phase1.py — Smoke-Tests für Phase 1 (ohne Hardware).

Prüft: DB-Verbindung, Schema, Migrations, State-Dataclass,
       StateManager, ArchiveWriter, Magnus-Formel.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import Database
from app.db.migrations import apply_migrations
from app.db.archive_writer import ArchiveWriter
from app.core.state import SystemState, StateManager
from app.config import DevConfig
from app.utils.physics import calc_absolute_humidity, outside_is_wetter, outside_is_drier


def get_default_config(cfg) -> dict:
    return {
        "target_temp":         cfg.DEFAULT_TARGET_TEMP,
        "target_hum":          cfg.DEFAULT_TARGET_HUM,
        "hum_hyst_mois":       cfg.DEFAULT_HUM_HYST_MOIS,
        "temp_hyst_cool":      cfg.DEFAULT_TEMP_HYST_COOL,
        "hum_hyst_fana":       cfg.DEFAULT_HUM_HYST_FANA,
        "temp_hyst_heat":      cfg.DEFAULT_TEMP_HYST_HEAT,
        "abs_hum_threshold":   cfg.DEFAULT_ABS_HUM_THRESHOLD,
        "aht21_hyst_humidify": cfg.DEFAULT_AHT21_HYST_HUMIDIFY,
        "aht21_hyst_protect":  cfg.DEFAULT_AHT21_HYST_PROTECT,
        "refreshrate":         cfg.DEFAULT_REFRESHRATE,
        "email_sleep":         cfg.DEFAULT_EMAIL_SLEEP,
        "arch_handling":       cfg.DEFAULT_ARCH_HANDLING,
        "gpio_pin_fanu":       cfg.GPIO_PIN_FANU,
        "gpio_pin_cool":       cfg.GPIO_PIN_COOL,
        "gpio_pin_fana":       cfg.GPIO_PIN_FANA,
        "gpio_pin_mois":       cfg.GPIO_PIN_MOIS,
        "gpio_pin_heat":       cfg.GPIO_PIN_HEAT,
        "i2c_bus":             cfg.I2C_BUS,
        "sht31_address":       cfg.SHT31_ADDRESS,
        "aht21_address":       cfg.AHT21_ADDRESS,
        "sensor_intern":       cfg.SENSOR_INTERN,
        "sensor_extern":       cfg.SENSOR_EXTERN,
        "weight_sensor_type":  cfg.WEIGHT_SENSOR_TYPE,
        "weight_sensor_pin_data":  cfg.WEIGHT_SENSOR_PIN_DATA,
        "weight_sensor_pin_clock": cfg.WEIGHT_SENSOR_PIN_CLOCK,
        "weight_sensor_scale": cfg.WEIGHT_SENSOR_SCALE,
        "mail_enabled":        int(cfg.MAIL_ENABLED),
        "mail_smtp":           cfg.MAIL_SMTP,
        "mail_port":           cfg.MAIL_PORT,
        "mail_user":           cfg.MAIL_USER,
        "mail_password":       cfg.MAIL_PASSWORD,
        "mail_receiver":       cfg.MAIL_RECEIVER,
        "ntfy_enabled":        int(cfg.NTFY_ENABLED),
        "ntfy_server":         cfg.NTFY_SERVER,
        "ntfy_topic":          cfg.NTFY_TOPIC,
        "updated_at":          time.time(),
    }


def test_physics():
    print("── Magnus-Formel ────────────────────────────────")
    ah = calc_absolute_humidity(20.0, 60.0)
    print(f"  20°C / 60%rF → {ah} g/m³  (erwartet ~10.4)")
    assert ah is not None and 10.0 < ah < 11.0, f"Fehler: {ah}"

    ah2 = calc_absolute_humidity(12.0, 85.0)
    print(f"  12°C / 85%rF → {ah2} g/m³  (erwartet ~8.8)")
    assert ah2 is not None and 8.0 < ah2 < 9.5, f"Fehler: {ah2}"

    # 20°C/60% muss mehr Wasser enthalten als 12°C/85%
    assert ah > ah2, "Physik-Fehler: 20°C/60% sollte mehr Wasser haben als 12°C/85%"

    assert outside_is_wetter(8.0, 9.0, 0.5) is True
    assert outside_is_wetter(8.0, 8.4, 0.5) is False   # unter Schwellwert
    assert outside_is_drier(9.0, 8.0, 0.5) is True
    assert outside_is_drier(None, 8.0) is False
    print("  ✓ Magnus-Formel korrekt")


def test_database():
    print("── Datenbank ────────────────────────────────────")
    db = Database(":memory:")
    db.connect()
    assert db.is_connected

    cfg = DevConfig
    apply_migrations(db, get_default_config(cfg))

    # Config vorhanden?
    row = db.fetchone("SELECT * FROM config WHERE id = 1")
    assert row is not None, "Config-Eintrag fehlt"
    assert row["target_temp"] == cfg.DEFAULT_TARGET_TEMP
    assert row["gpio_pin_fanu"] == cfg.GPIO_PIN_FANU
    print(f"  Config geladen: {row['target_temp']}°C / {row['target_hum']}%rF")

    # Schema-Version
    version = db.fetchscalar("SELECT MAX(version) FROM schema_version")
    assert version == 1, f"Falsche Schema-Version: {version}"
    print(f"  Schema-Version: {version}")

    # Zweiter apply_migrations-Aufruf darf nicht doppelt einfügen (idempotent)
    apply_migrations(db, get_default_config(cfg))
    count = db.fetchscalar("SELECT COUNT(*) FROM config")
    assert count == 1, f"Doppelter Config-Eintrag: {count}"
    print("  ✓ Migration idempotent")

    db.close()


def test_state_manager():
    print("── StateManager ─────────────────────────────────")
    db = Database(":memory:")
    db.connect()
    apply_migrations(db, get_default_config(DevConfig))

    sm = StateManager(state_file="/tmp/test_state.json", db=db)
    state = sm.load_from_db()

    assert state.target_temp == DevConfig.DEFAULT_TARGET_TEMP
    assert state.gpio_pin_fanu == DevConfig.GPIO_PIN_FANU
    assert state.relay_cool is False
    print(f"  State geladen: Soll={state.target_temp}°C, Modus={state.mode}")

    # State-Datei schreiben
    state.temp_intern = 13.5
    state.hum_intern  = 81.2
    state.relay_cool  = True
    sm.write_state_file(state)

    # State-Datei lesen
    data = sm.read_state_file()
    assert data is not None
    assert data["temp_intern"] == 13.5
    assert data["relay_cool"] is True
    assert "uptime_secs" in data
    print(f"  State-Datei: temp={data['temp_intern']}°C, relay_cool={data['relay_cool']}")
    print("  ✓ StateManager OK")

    db.close()


def test_archive_writer():
    print("── ArchiveWriter ────────────────────────────────")
    db = Database(":memory:")
    db.connect()
    apply_migrations(db, get_default_config(DevConfig))

    writer = ArchiveWriter(db, interval_secs=1)

    state = SystemState()
    state.temp_intern  = 13.5
    state.hum_intern   = 81.0
    state.temp_extern  = 17.2
    state.hum_extern   = 65.0
    state.target_temp  = 14.0
    state.target_hum   = 80.0
    state.timestamp    = time.time()

    # 3 Einträge puffern
    writer.add(state)
    writer.add(state)
    writer.add(state)
    assert writer.buffer_size == 3

    # Noch kein Flush (Intervall nicht abgelaufen wenn > 1s nicht gewartet)
    writer.maybe_flush()   # Intervall = 1s, sollte noch nicht flushen bei sofortigem Aufruf

    # Expliziter Flush
    writer.flush()
    assert writer.buffer_size == 0

    count = db.fetchscalar("SELECT COUNT(*) FROM state_archive")
    assert count == 3, f"Erwartet 3 Einträge, gefunden: {count}"
    print(f"  {count} Einträge in state_archive geschrieben")

    # maybe_flush nach Intervall
    writer.add(state)
    time.sleep(1.1)
    writer.maybe_flush()   # Jetzt sollte Flush ausgelöst werden
    count2 = db.fetchscalar("SELECT COUNT(*) FROM state_archive")
    assert count2 == 4, f"Erwartet 4 Einträge, gefunden: {count2}"
    print("  ✓ ArchiveWriter / Batching OK")

    db.close()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)

    print("\n╔══════════════════════════════════════════╗")
    print("║  Reife-Pi v2 — Phase 1 Smoke-Tests      ║")
    print("╚══════════════════════════════════════════╝\n")

    try:
        test_physics()
        test_database()
        test_state_manager()
        test_archive_writer()
        print("\n✅  Alle Tests bestanden.\n")
    except AssertionError as e:
        print(f"\n❌  Test fehlgeschlagen: {e}\n")
        sys.exit(1)
