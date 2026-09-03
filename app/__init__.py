"""
app/__init__.py — Flask Application Factory.

Verwendung:
    from app import create_app
    app = create_app()          # Produktion (ProdConfig)
    app = create_app("dev")     # Entwicklung mit Dummy-Hardware
"""

import logging
import os

from flask import Flask

from app.config           import DevConfig, ProdConfig
from app.db.database      import Database
from app.db.migrations    import apply_migrations
from app.db.archive_writer import ArchiveWriter
from app.core.state       import StateManager
from app.core.gpio_manager import GpioManager
from app.core.sensors     import build_sensor_manager
from app.core.program_runner import ProgramRunner
from app.core.controller  import ReifpiController


def create_app(env: str | None = None) -> Flask:
    """
    Flask Application Factory.

    env:
        None / "prod"  → ProdConfig (echte Hardware)
        "dev"          → DevConfig  (Dummy-Hardware, SQLite in /tmp)
    """
    if env is None:
        env = os.environ.get("REIFE_ENV", "prod")

    cfg = DevConfig() if env == "dev" else ProdConfig()

    # ── Logging ──────────────────────────────────────────────────────────────
    logging.basicConfig(
        level   = logging.DEBUG if cfg.DEBUG else logging.INFO,
        format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger(__name__)
    logger.info(f"Starte Reifeschrank ({env}) — DB: {cfg.DB_PATH}")

    # ── Flask ─────────────────────────────────────────────────────────────────
    app = Flask(
        __name__,
        template_folder = "templates",
        static_folder   = "static",
    )
    app.config.from_object(cfg)

    # ── Datenbank ─────────────────────────────────────────────────────────────
    import time as _time
    db = Database(cfg.DB_PATH)
    db.connect()
    apply_migrations(db, {
        "target_temp":            cfg.DEFAULT_TARGET_TEMP,
        "target_hum":             cfg.DEFAULT_TARGET_HUM,
        "hum_hyst_mois":          cfg.DEFAULT_HUM_HYST_MOIS,
        "temp_hyst_cool":         cfg.DEFAULT_TEMP_HYST_COOL,
        "hum_hyst_fana":          cfg.DEFAULT_HUM_HYST_FANA,
        "temp_hyst_heat":         cfg.DEFAULT_TEMP_HYST_HEAT,
        "abs_hum_threshold":      cfg.DEFAULT_ABS_HUM_THRESHOLD,
        "aht21_hyst_humidify":    cfg.DEFAULT_AHT21_HYST_HUMIDIFY,
        "aht21_hyst_protect":     cfg.DEFAULT_AHT21_HYST_PROTECT,
        "refreshrate":            cfg.DEFAULT_REFRESHRATE,
        "email_sleep":            cfg.DEFAULT_EMAIL_SLEEP,
        "arch_handling":          cfg.DEFAULT_ARCH_HANDLING,
        "gpio_pin_fanu":          cfg.GPIO_PIN_FANU,
        "gpio_pin_cool":          cfg.GPIO_PIN_COOL,
        "gpio_pin_fana":          cfg.GPIO_PIN_FANA,
        "gpio_pin_mois":          cfg.GPIO_PIN_MOIS,
        "gpio_pin_heat":          cfg.GPIO_PIN_HEAT,
        "i2c_bus":                cfg.I2C_BUS,
        "sht31_address":          cfg.SHT31_ADDRESS,
        "aht21_address":          cfg.AHT21_ADDRESS,
        "bme280_address":         cfg.BME280_ADDRESS,
        "sensor_intern":          cfg.SENSOR_INTERN,
        "sensor_intern_address":  cfg.SENSOR_INTERN_ADDRESS,
        "sensor_extern":          cfg.SENSOR_EXTERN,
        "sensor_extern_address":  cfg.SENSOR_EXTERN_ADDRESS,
        "weight_sensor_type":     cfg.WEIGHT_SENSOR_TYPE,
        "weight_sensor_pin_data": cfg.WEIGHT_SENSOR_PIN_DATA,
        "weight_sensor_pin_clock":cfg.WEIGHT_SENSOR_PIN_CLOCK,
        "weight_sensor_scale":    cfg.WEIGHT_SENSOR_SCALE,
        "mail_enabled":           cfg.MAIL_ENABLED,
        "mail_smtp":              cfg.MAIL_SMTP,
        "mail_port":              cfg.MAIL_PORT,
        "mail_user":              cfg.MAIL_USER,
        "mail_password":          cfg.MAIL_PASSWORD,
        "mail_receiver":          cfg.MAIL_RECEIVER,
        "ntfy_enabled":           cfg.NTFY_ENABLED,
        "ntfy_server":            cfg.NTFY_SERVER,
        "ntfy_topic":             cfg.NTFY_TOPIC,
        "ntfy_user":              cfg.NTFY_USER,
        "ntfy_token":             cfg.NTFY_TOKEN,
        "updated_at":             _time.time(),
    })
    app.db = db  # type: ignore[attr-defined]

    # ── State Manager ─────────────────────────────────────────────────────────
    state_manager = StateManager(cfg.STATE_FILE, db)
    state         = state_manager.load_from_db()
    state_manager.restore_after_poweroff(state)
    app.state_manager = state_manager  # type: ignore[attr-defined]

    # ── Hardware ─────────────────────────────────────────────────────────────
    pins = {
        "fanu": state.gpio_pin_fanu,
        "cool": state.gpio_pin_cool,
        "fana": state.gpio_pin_fana,
        "mois": state.gpio_pin_mois,
        "heat": state.gpio_pin_heat,
    }
    gpio_manager    = GpioManager(pins, dummy=cfg.GPIO_DUMMY, active_high=state.relay_active_high)

    # Sensor-Konfiguration aus DB lesen (überschreibt Python-Defaults)
    _db_row = db.fetchone("SELECT * FROM config WHERE id = 1")
    _sensor_cfg: dict = {}
    if _db_row:
        _row = dict(_db_row)
        _sensor_cfg = {
            "sensor_intern":         _row.get("sensor_intern"),
            "sensor_intern_address": _row.get("sensor_intern_address"),
            "sensor_extern":         _row.get("sensor_extern"),
            "sensor_extern_address": _row.get("sensor_extern_address"),
            "i2c_bus":               _row.get("i2c_bus"),
        }
    sensor_manager  = build_sensor_manager(cfg, dummy=cfg.SENSOR_DUMMY, db_config=_sensor_cfg)

    # ── Archiv-Writer ─────────────────────────────────────────────────────────
    archive_writer = ArchiveWriter(db, interval_secs=cfg.ARCHIVE_INTERVAL_SECS)

    # ── Programm-Runner ───────────────────────────────────────────────────────
    program_runner = ProgramRunner(db)
    app.program_runner = program_runner  # type: ignore[attr-defined]

    # ── Controller ────────────────────────────────────────────────────────────
    controller = ReifpiController(
        state          = state,
        state_manager  = state_manager,
        sensor_manager = sensor_manager,
        gpio            = gpio_manager,
        archive_writer = archive_writer,
        program_runner = program_runner,
        sensor_fail_threshold    = cfg.SENSOR_FAIL_THRESHOLD,
        compressor_protect_secs  = cfg.COMPRESSOR_PROTECT_SECS,
    )
    app.controller = controller  # type: ignore[attr-defined]

    # ── Blueprints ────────────────────────────────────────────────────────────
    from app.api.status          import bp as status_bp
    from app.api.control         import bp as control_bp
    from app.api.programs        import bp as programs_bp
    from app.api.archive         import bp as archive_bp
    from app.api.settings        import bp as settings_bp
    from app.api.import_recipe   import bp as import_bp
    from app.api.hardware_setup  import bp as hardware_setup_bp
    from app.web.views           import bp as web_bp

    for blueprint in (
        status_bp, control_bp, programs_bp,
        archive_bp, settings_bp, import_bp,
        hardware_setup_bp, web_bp,
    ):
        app.register_blueprint(blueprint)

    # ── Controller starten ────────────────────────────────────────────────────
    controller.start()
    logger.info("Controller gestartet.")

    # ── Teardown: GPIO + Controller beim Beenden sauber schließen ─────────────
    @app.teardown_appcontext
    def _teardown(exc):
        pass  # Teardown per appcontext nicht geeignet für Threads

    import atexit
    atexit.register(_shutdown, controller, gpio_manager, archive_writer, db)

    return app


def _shutdown(
    controller:    "ReifpiController",
    gpio_manager:  "GpioManager",
    archive_writer: "ArchiveWriter",
    db:            "Database",
) -> None:
    """Geordnetes Herunterfahren beim Prozessende."""
    logger = logging.getLogger(__name__)
    logger.info("Shutdown: Controller stoppen …")
    try:
        controller.stop()
    except Exception as e:
        logger.error(f"Controller stop: {e}")

    logger.info("Shutdown: Archiv flushen …")
    try:
        archive_writer.flush()
    except Exception as e:
        logger.error(f"Archive flush: {e}")

    logger.info("Shutdown: GPIO deaktivieren …")
    try:
        gpio_manager.all_off("shutdown")
        gpio_manager.close()
    except Exception as e:
        logger.error(f"GPIO close: {e}")

    logger.info("Shutdown: DB schließen …")
    try:
        db.close()
    except Exception as e:
        logger.error(f"DB close: {e}")

    logger.info("Shutdown abgeschlossen.")
