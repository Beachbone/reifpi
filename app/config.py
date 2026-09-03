"""
config.py — Konfigurationsklassen für Entwicklung und Produktion.

Alle Hardware-Konstanten (GPIO-Pins, I2C-Adressen) stehen hier als Defaults.
Überschreibbare Werte (Solltemperatur, Hysteresen etc.) kommen aus der DB.
"""

import os


class BaseConfig:
    # ── Verzeichnisse ─────────────────────────────────────────────────────────
    # Per Umgebungsvariable überschreibbar (gesetzt vom .deb-Paket / systemd-Unit
    # auf FHS-konforme Pfade unter /var/lib, /run, /var/log). Ohne Overrides
    # (z.B. beim manuellen Entwickeln) bleiben die alten projektlokalen Defaults.
    BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DB_PATH       = os.environ.get("REIFPI_DB_PATH", os.path.join(BASE_DIR, "reifeschrank.db"))
    STATE_FILE    = os.environ.get("REIFPI_STATE_FILE", "/run/reifeschrank/state.json")
    LOG_FILE      = os.environ.get("REIFPI_LOG_FILE", "/var/log/reifeschrank/reifeschrank2.log")

    # ── Flask ─────────────────────────────────────────────────────────────────
    # Vom Paket-postinst pro Installation zufällig generiert (REIFPI_SECRET_KEY),
    # damit nicht alle Installationen denselben Default-Key teilen.
    SECRET_KEY    = os.environ.get("REIFPI_SECRET_KEY", "reife-pi-change-me-in-production")
    DEBUG         = False
    HOST          = "0.0.0.0"
    PORT          = 5000

    # ── Controller ────────────────────────────────────────────────────────────
    CONTROLLER_INTERVAL_SECS  = 30      # Regelzyklus in Sekunden
    MANUAL_INTERVAL_SECS      = 2       # Zyklus im Manuell-Modus (schnell)
    ARCHIVE_INTERVAL_SECS     = 300     # Archiv-Batching (5 Minuten)
    SENSOR_FAIL_THRESHOLD     = 3       # Fehler in Folge → Safe-Mode
    COMPRESSOR_PROTECT_SECS   = 300     # Kompressor-Schutzzeit (5 Minuten)

    # ── GPIO-Standard-Pins (active-low Relais) ────────────────────────────────
    # Alle Pins können über die Settings-Seite in der DB überschrieben werden.
    # Änderungen erfordern einen Service-Neustart.
    GPIO_PIN_FANU  = 27   # Umluft-Lüfter (intern)
    GPIO_PIN_COOL  = 23   # Kühlkompressor
    GPIO_PIN_FANA  = 17   # Frischluft-Lüfter (extern/Außenluft)
    GPIO_PIN_MOIS  = 22   # Ultraschall-Befeuchter
    GPIO_PIN_HEAT  = 24   # Heizung

    # ── I2C-Sensor-Adressen ───────────────────────────────────────────────────
    I2C_BUS           = 1
    SHT31_ADDRESS     = 0x44   # Interner Sensor
    AHT21_ADDRESS     = 0x38   # Externer Sensor (Außenluft)
    BME280_ADDRESS    = 0x76   # BME280 Sensor (SDO=GND)

    # ── Sensor-Konfiguration ──────────────────────────────────────────────────
    # Welcher Sensor wird intern verwendet? "sht31", "aht21", "bme280" oder "none"
    SENSOR_INTERN         = "sht31"
    SENSOR_INTERN_ADDRESS = 0x44   # I2C-Adresse des Innensensors
    # Welcher Sensor wird extern verwendet? "aht21", "sht31", "bme280" oder "none"
    SENSOR_EXTERN         = "aht21"
    SENSOR_EXTERN_ADDRESS = 0x38   # I2C-Adresse des Außensensors
    # Anzahl Reads pro Messung für gleitenden Mittelwert
    SENSOR_READ_COUNT = 3

    # ── Waage (optional, für spätere Nachrüstung) ─────────────────────────────
    # Unterstützte Typen: None, "hx711"
    WEIGHT_SENSOR_TYPE       = None
    WEIGHT_SENSOR_PIN_DATA   = None   # GPIO-Pin DATA (z.B. HX711)
    WEIGHT_SENSOR_PIN_CLOCK  = None   # GPIO-Pin CLOCK (z.B. HX711)
    WEIGHT_SENSOR_SCALE      = 1.0    # Kalibrierfaktor

    # ── Standard-Regelwerte (werden beim ersten DB-Init gesetzt) ─────────────
    DEFAULT_TARGET_TEMP       = 14.0
    DEFAULT_TARGET_HUM        = 80.0
    DEFAULT_HUM_HYST_MOIS     = 5.0
    DEFAULT_TEMP_HYST_COOL    = 0.5
    DEFAULT_HUM_HYST_FANA     = 2.0
    DEFAULT_TEMP_HYST_HEAT    = 1.0
    DEFAULT_ABS_HUM_THRESHOLD = 0.5   # g/m³ Mindestdifferenz für Frischluft-Entscheid
    DEFAULT_AHT21_HYST_HUMIDIFY = 4.0  # %rF unter Soll → FANA zum Befeuchten
    DEFAULT_AHT21_HYST_PROTECT  = 2.0  # %rF über Soll  → Schutzfunktion
    DEFAULT_REFRESHRATE       = 30    # Sekunden
    DEFAULT_EMAIL_SLEEP       = 60    # Minuten zwischen E-Mails
    DEFAULT_ARCH_HANDLING     = 12000 # Max. Archiv-Einträge (0 = unbegrenzt)

    # ── E-Mail ────────────────────────────────────────────────────────────────
    MAIL_ENABLED   = False
    MAIL_SMTP      = ""
    MAIL_PORT      = 587
    MAIL_USER      = ""
    MAIL_PASSWORD  = ""
    MAIL_RECEIVER  = ""

    # ── ntfy Push-Notifications (optional) ───────────────────────────────────
    NTFY_ENABLED   = False
    NTFY_SERVER    = "https://ntfy.sh"
    NTFY_TOPIC     = ""
    NTFY_USER      = ""
    NTFY_TOKEN     = ""


class ProdConfig(BaseConfig):
    """Produktions-Konfiguration — echte Hardware."""
    GPIO_DUMMY     = False
    SENSOR_DUMMY   = False


class DevConfig(BaseConfig):
    """Entwicklungs-Konfiguration — Simulation ohne Hardware."""
    DEBUG          = True
    DB_PATH        = os.path.join(BaseConfig.BASE_DIR, "reifeschrank_dev.db")
    STATE_FILE     = "/tmp/reifeschrank_dev_state.json"
    LOG_FILE       = "/tmp/reifeschrank2.log"
    GPIO_DUMMY     = True
    SENSOR_DUMMY   = True
    CONTROLLER_INTERVAL_SECS = 5   # Schnellere Zyklen im Dev-Modus


def get_config(env: str = "prod") -> type[BaseConfig]:
    """Gibt die passende Konfigurationsklasse zurück."""
    return {
        "prod": ProdConfig,
        "dev":  DevConfig,
    }.get(env, ProdConfig)
