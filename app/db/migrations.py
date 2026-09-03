"""
migrations.py — Schema-Versionierung und Initialisierung.

Beim Start prüft apply_migrations() die aktuelle DB-Version und führt
fehlende Migrationen aus. Neue Migrationen als migrate_vX_to_vY() hinzufügen.
"""

import logging
import time

from app.db.database import Database
from app.db.schema import ALL_TABLES

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 6


def apply_migrations(db: Database, default_config: dict) -> None:
    """
    Initialisiert das Schema und führt fehlende Migrationen aus.

    Args:
        db:             Geöffnete Datenbankverbindung
        default_config: Default-Werte für den ersten Config-Eintrag
    """
    _create_tables(db)
    current_version = _get_schema_version(db)

    if current_version == 0:
        logger.info("Neue Datenbank — initialisiere Schema v1.")
        _init_config(db, default_config)
        _set_schema_version(db, 1)
        logger.info("Schema v1 initialisiert.")
        return

    if current_version < CURRENT_SCHEMA_VERSION:
        logger.info(f"Schema-Version {current_version} → {CURRENT_SCHEMA_VERSION} — starte Migration.")
        if current_version < 2:
            _migrate_v1_to_v2(db)
            _set_schema_version(db, 2)
        if current_version < 3:
            _migrate_v2_to_v3(db)
            _set_schema_version(db, 3)
        if current_version < 4:
            _migrate_v3_to_v4(db)
            _set_schema_version(db, 4)
        if current_version < 5:
            _migrate_v4_to_v5(db)
            _set_schema_version(db, 5)
        if current_version < 6:
            _migrate_v5_to_v6(db)
            _set_schema_version(db, 6)
        logger.info("Migration abgeschlossen.")

    logger.info(f"Datenbankschema Version {CURRENT_SCHEMA_VERSION} — OK.")


def _create_tables(db: Database) -> None:
    """Erstellt alle Tabellen (IF NOT EXISTS — idempotent)."""
    for statement in ALL_TABLES:
        db.execute(statement)


def _get_schema_version(db: Database) -> int:
    """Gibt die aktuelle Schema-Version zurück (0 = neu/leer)."""
    row = db.fetchone("SELECT MAX(version) as v FROM schema_version")
    if row is None or row["v"] is None:
        return 0
    return int(row["v"])


def _set_schema_version(db: Database, version: int) -> None:
    db.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
        (version, time.time()),
    )


def _migrate_v1_to_v2(db: Database) -> None:
    """
    v1 → v2: bme280_address Spalte hinzufügen.
    """
    try:
        db.execute(
            "ALTER TABLE config ADD COLUMN bme280_address INTEGER NOT NULL DEFAULT 118"
        )
        logger.info("Migration v2: bme280_address Spalte hinzugefügt.")
    except Exception as e:
        # Spalte existiert bereits (z.B. bei erneuter Migration)
        logger.warning(f"Migration v2 übersprungen (Spalte existiert?): {e}")


def _migrate_v2_to_v3(db: Database) -> None:
    """
    v2 → v3: Per-Rolle-Adressen sensor_intern_address / sensor_extern_address.
    Initialisiert mit sht31_address bzw. aht21_address wenn vorhanden.
    """
    for col, default in (
        ("sensor_intern_address", 68),   # 0x44
        ("sensor_extern_address", 56),   # 0x38
    ):
        try:
            db.execute(
                f"ALTER TABLE config ADD COLUMN {col} INTEGER NOT NULL DEFAULT {default}"
            )
            logger.info(f"Migration v3: {col} Spalte hinzugefügt.")
        except Exception as e:
            logger.warning(f"Migration v3 ({col}) übersprungen: {e}")

    # Vorhandene Typ-Adressen als Startwert übernehmen
    try:
        db.execute(
            "UPDATE config SET "
            "sensor_intern_address = COALESCE(sht31_address, 68), "
            "sensor_extern_address = COALESCE(aht21_address, 56) "
            "WHERE id = 1"
        )
    except Exception as e:
        logger.warning(f"Migration v3: Adressen-Init übersprungen: {e}")


def _migrate_v3_to_v4(db: Database) -> None:
    """v3 → v4: ntfy_token Spalte für Bearer-Authentifizierung."""
    try:
        db.execute("ALTER TABLE config ADD COLUMN ntfy_token TEXT NOT NULL DEFAULT ''")
        logger.info("Migration v4: ntfy_token Spalte hinzugefügt.")
    except Exception as e:
        logger.warning(f"Migration v4 übersprungen: {e}")


def _migrate_v4_to_v5(db: Database) -> None:
    """v4 → v5: ntfy_user Spalte für Basic-Auth."""
    try:
        db.execute("ALTER TABLE config ADD COLUMN ntfy_user TEXT NOT NULL DEFAULT ''")
        logger.info("Migration v5: ntfy_user Spalte hinzugefügt.")
    except Exception as e:
        logger.warning(f"Migration v5 übersprungen: {e}")


def _migrate_v5_to_v6(db: Database) -> None:
    """v5 → v6: relay_active_high für active-high Relais (0 = active-low, Standard)."""
    try:
        db.execute("ALTER TABLE config ADD COLUMN relay_active_high INTEGER NOT NULL DEFAULT 0")
        logger.info("Migration v6: relay_active_high Spalte hinzugefügt.")
    except Exception as e:
        logger.warning(f"Migration v6 übersprungen: {e}")


def _init_config(db: Database, defaults: dict) -> None:
    """
    Legt den initialen Config-Eintrag (id=1) an.
    Wird nur beim ersten Start einer leeren DB aufgerufen.
    """
    db.execute(
        """
        INSERT OR IGNORE INTO config (
            id, power, mode,
            target_temp, target_hum,
            hum_hyst_mois, temp_hyst_cool, hum_hyst_fana, temp_hyst_heat,
            abs_hum_threshold, aht21_hyst_humidify, aht21_hyst_protect,
            refreshrate, email_sleep, arch_handling,
            gpio_pin_fanu, gpio_pin_cool, gpio_pin_fana,
            gpio_pin_mois, gpio_pin_heat,
            i2c_bus, sht31_address, aht21_address, bme280_address,
            sensor_intern, sensor_intern_address,
            sensor_extern, sensor_extern_address,
            weight_sensor_type, weight_sensor_pin_data,
            weight_sensor_pin_clock, weight_sensor_scale,
            mail_enabled, mail_smtp, mail_port,
            mail_user, mail_password, mail_receiver,
            ntfy_enabled, ntfy_server, ntfy_topic, ntfy_user, ntfy_token,
            updated_at
        ) VALUES (
            1, 1, 1,
            :target_temp, :target_hum,
            :hum_hyst_mois, :temp_hyst_cool, :hum_hyst_fana, :temp_hyst_heat,
            :abs_hum_threshold, :aht21_hyst_humidify, :aht21_hyst_protect,
            :refreshrate, :email_sleep, :arch_handling,
            :gpio_pin_fanu, :gpio_pin_cool, :gpio_pin_fana,
            :gpio_pin_mois, :gpio_pin_heat,
            :i2c_bus, :sht31_address, :aht21_address, :bme280_address,
            :sensor_intern, :sensor_intern_address,
            :sensor_extern, :sensor_extern_address,
            :weight_sensor_type, :weight_sensor_pin_data,
            :weight_sensor_pin_clock, :weight_sensor_scale,
            :mail_enabled, :mail_smtp, :mail_port,
            :mail_user, :mail_password, :mail_receiver,
            :ntfy_enabled, :ntfy_server, :ntfy_topic, :ntfy_user, :ntfy_token,
            :updated_at
        )
        """,
        defaults,
    )
    logger.info("Standard-Konfiguration geschrieben.")
