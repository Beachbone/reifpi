"""
schema.py — SQLite-Tabellendefinitionen als SQL-Strings.

Keine Logik, nur DDL. Wird von migrations.py verwendet.
"""

# ── Konfiguration (Singleton, id=1) ──────────────────────────────────────────
# Enthält alle Regelparameter und Hardware-Konfiguration.
# Wird beim Start geladen, bei Änderungen über die Settings-Seite geschrieben.
TABLE_CONFIG = """
CREATE TABLE IF NOT EXISTS config (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),

    -- Betrieb
    power                   INTEGER NOT NULL DEFAULT 1,
    mode                    INTEGER NOT NULL DEFAULT 1,
    -- Modi: 0=nur messen, 1=voll-auto (Temp+Hum), 2=nur Temp, 3=manuell

    -- Sollwerte
    target_temp             REAL    NOT NULL DEFAULT 14.0,
    target_hum              REAL    NOT NULL DEFAULT 80.0,

    -- Hysteresen Regelung
    hum_hyst_mois           REAL    NOT NULL DEFAULT 5.0,
    temp_hyst_cool          REAL    NOT NULL DEFAULT 0.5,
    hum_hyst_fana           REAL    NOT NULL DEFAULT 2.0,
    temp_hyst_heat          REAL    NOT NULL DEFAULT 1.0,

    -- AHT21 Außensensor-Regelung
    abs_hum_threshold       REAL    NOT NULL DEFAULT 0.5,
    -- Mindestdifferenz absolute Feuchte [g/m³] für Frischluft-Entscheid
    aht21_hyst_humidify     REAL    NOT NULL DEFAULT 4.0,
    -- %rF unter Soll: FANA zum Befeuchten erst ab dieser Abweichung
    aht21_hyst_protect      REAL    NOT NULL DEFAULT 2.0,
    -- %rF über Soll: Schutzfunktion greift ab dieser Abweichung

    -- Steuerung
    refreshrate             INTEGER NOT NULL DEFAULT 30,
    -- Regelzyklus in Sekunden
    email_sleep             INTEGER NOT NULL DEFAULT 60,
    -- Minuten Mindestabstand zwischen E-Mails
    arch_handling           INTEGER NOT NULL DEFAULT 12000,
    -- Max. Archiv-Einträge (0 = unbegrenzt)

    -- GPIO-Pins
    gpio_pin_fanu           INTEGER NOT NULL DEFAULT 27,
    gpio_pin_cool           INTEGER NOT NULL DEFAULT 23,
    gpio_pin_fana           INTEGER NOT NULL DEFAULT 17,
    gpio_pin_mois           INTEGER NOT NULL DEFAULT 22,
    gpio_pin_heat           INTEGER NOT NULL DEFAULT 24,
    relay_active_high       INTEGER NOT NULL DEFAULT 0,

    -- I2C-Konfiguration
    i2c_bus                 INTEGER NOT NULL DEFAULT 1,
    sht31_address           INTEGER NOT NULL DEFAULT 68,
    -- 0x44 = 68
    aht21_address           INTEGER NOT NULL DEFAULT 56,
    -- 0x38 = 56
    sensor_intern           TEXT    NOT NULL DEFAULT 'sht31',
    sensor_intern_address   INTEGER NOT NULL DEFAULT 68,
    -- 0x44 = 68
    sensor_extern           TEXT    NOT NULL DEFAULT 'aht21',
    sensor_extern_address   INTEGER NOT NULL DEFAULT 56,
    -- 0x38 = 56
    -- Mögliche Sensor-Typen: 'sht31', 'aht21', 'bme280', 'none'
    bme280_address          INTEGER NOT NULL DEFAULT 118,
    -- 0x76 = 118  (Fallback-Adresse für BME280, wird durch sensor_*_address überschrieben)

    -- Waage (optionale Nachrüstung)
    weight_sensor_type      TEXT    DEFAULT NULL,
    -- Mögliche Werte: NULL, 'hx711'
    weight_sensor_pin_data  INTEGER DEFAULT NULL,
    weight_sensor_pin_clock INTEGER DEFAULT NULL,
    weight_sensor_scale     REAL    NOT NULL DEFAULT 1.0,

    -- E-Mail
    mail_enabled            INTEGER NOT NULL DEFAULT 0,
    mail_smtp               TEXT    NOT NULL DEFAULT '',
    mail_port               INTEGER NOT NULL DEFAULT 587,
    mail_user               TEXT    NOT NULL DEFAULT '',
    mail_password           TEXT    NOT NULL DEFAULT '',
    mail_receiver           TEXT    NOT NULL DEFAULT '',

    -- ntfy Push-Notifications
    ntfy_enabled            INTEGER NOT NULL DEFAULT 0,
    ntfy_server             TEXT    NOT NULL DEFAULT 'https://ntfy.sh',
    ntfy_topic              TEXT    NOT NULL DEFAULT '',
    ntfy_user               TEXT    NOT NULL DEFAULT '',
    -- Benutzername für Basic-Auth (leer = kein Basic-Auth)
    ntfy_token              TEXT    NOT NULL DEFAULT '',
    -- Bearer-Token ODER Passwort (wenn ntfy_user gesetzt → Basic-Auth, sonst Bearer)

    updated_at              REAL    NOT NULL DEFAULT 0
)
"""

# ── Archiv (Messwert-Zeitreihen) ──────────────────────────────────────────────
# Wird gebatcht geschrieben (alle 5 Minuten). Kein Live-State mehr.
TABLE_STATE_ARCHIVE = """
CREATE TABLE IF NOT EXISTS state_archive (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       REAL    NOT NULL,

    mode            INTEGER NOT NULL,
    riping_pgm      INTEGER NOT NULL DEFAULT 0,
    riping_step     INTEGER NOT NULL DEFAULT 0,

    -- Sensordaten
    temp_intern     REAL    NOT NULL,
    hum_intern      REAL    NOT NULL,
    temp_extern     REAL,
    hum_extern      REAL,
    abs_hum_intern  REAL,
    abs_hum_extern  REAL,

    -- Waage (NULL wenn kein Sensor vorhanden)
    weight          REAL,

    -- Relais-Zustände
    relay_heat      INTEGER NOT NULL DEFAULT 0,
    relay_cool      INTEGER NOT NULL DEFAULT 0,
    relay_mois      INTEGER NOT NULL DEFAULT 0,
    relay_fanu      INTEGER NOT NULL DEFAULT 0,
    relay_fana      INTEGER NOT NULL DEFAULT 0,

    -- Sollwerte zum Zeitpunkt der Messung
    target_temp     REAL    NOT NULL,
    target_hum      REAL    NOT NULL
)
"""

TABLE_STATE_ARCHIVE_IDX = """
CREATE INDEX IF NOT EXISTS idx_state_archive_timestamp
    ON state_archive (timestamp)
"""

# ── Reifeprogramme ────────────────────────────────────────────────────────────
TABLE_RIPING_PGMS = """
CREATE TABLE IF NOT EXISTS riping_pgms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT    NOT NULL DEFAULT '',
    created_at  REAL    NOT NULL DEFAULT (unixepoch())
)
"""

TABLE_RIPING_STEPS = """
CREATE TABLE IF NOT EXISTS riping_steps (
    riping_pgms_id  INTEGER NOT NULL REFERENCES riping_pgms(id) ON DELETE CASCADE,
    step            INTEGER NOT NULL,
    duration_h      INTEGER NOT NULL,
    temp            REAL    NOT NULL,
    moist           REAL    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    modes_id        INTEGER NOT NULL DEFAULT 1,
    -- Modi: 1=voll-auto, 2=nur Temp
    target_weight_loss_pct  REAL DEFAULT NULL,
    -- Optionaler Ziel-Gewichtsverlust in % (für spätere Waagen-Integration)
    PRIMARY KEY (riping_pgms_id, step)
)
"""

# ── Reifeprogramm-Laufarchiv ──────────────────────────────────────────────────
TABLE_RIPING_ARCHIVE = """
CREATE TABLE IF NOT EXISTS riping_archive (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    riping_pgm      INTEGER NOT NULL,
    riping_step     INTEGER NOT NULL,
    start_time      REAL    NOT NULL,
    end_time        REAL    NOT NULL,
    -- Geplantes Ende: start_time + duration_h * 3600
    time_step_ended REAL    DEFAULT NULL
    -- Tatsächliches Ende (NULL = Schritt läuft noch)
)
"""

# ── Schema-Versionierung ──────────────────────────────────────────────────────
TABLE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  REAL    NOT NULL
)
"""

# ── Alle Tabellen in Reihenfolge ──────────────────────────────────────────────
ALL_TABLES = [
    TABLE_SCHEMA_VERSION,
    TABLE_CONFIG,
    TABLE_STATE_ARCHIVE,
    TABLE_STATE_ARCHIVE_IDX,
    TABLE_RIPING_PGMS,
    TABLE_RIPING_STEPS,
    TABLE_RIPING_ARCHIVE,
]
