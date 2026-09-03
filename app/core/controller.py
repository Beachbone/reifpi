"""
controller.py — Regelschleife, Hysterese-Regelung, Safe-Mode.

Der Controller läuft als Daemon-Thread neben dem Flask-Webserver.
Er liest Sensoren, berechnet Schaltzustände, setzt GPIO-Ausgänge
und schreibt den State in /run/reifeschrank/state.json (RAM).

Archiv-Writes erfolgen gebatcht alle 5 Minuten — kein SD-Write
pro Regelzyklus mehr.
"""

import logging
import threading
import time
from typing import TYPE_CHECKING

from app.core.state import SystemState, StateManager
from app.core.sensors import SensorManager
from app.core.gpio_manager import GpioManager
from app.core.program_runner import ProgramRunner
from app.db.archive_writer import ArchiveWriter
from app.utils.physics import outside_is_wetter, outside_is_drier

if TYPE_CHECKING:
    from app.notifications.email_notifier import EmailNotifier

logger = logging.getLogger(__name__)

# Betriebsmodi (entsprechen config.mode in der DB)
MODE_MEASURE_ONLY  = 0   # Nur messen, nichts schalten
MODE_AUTO          = 1   # Volle Regelung (Temp + Hum)
MODE_TEMP_ONLY     = 2   # Nur Temperaturregelung
MODE_MANUAL        = 3   # Manuell — kein automatisches Schalten


class ReifeschrankController:
    """
    Hauptregelkreis der Steuerung.

    Wird als Daemon-Thread gestartet. Flask-API kommuniziert über
    den gemeinsamen SystemState (thread-safe durch Lock).
    """

    def __init__(
        self,
        state:          SystemState,
        state_manager:  StateManager,
        sensor_manager: SensorManager,
        gpio:           GpioManager,
        archive_writer: ArchiveWriter,
        program_runner: ProgramRunner,
        notifier:       "EmailNotifier | None" = None,
        sensor_fail_threshold: int = 3,
        compressor_protect_secs: int = 300,
    ) -> None:
        self._state          = state
        self._state_mgr      = state_manager
        self._sensors        = sensor_manager
        self._gpio           = gpio
        self._archive        = archive_writer
        self._programs       = program_runner
        self._notifier       = notifier
        self._fail_threshold = sensor_fail_threshold
        self._comp_protect   = compressor_protect_secs

        self._running  = False
        self._thread:  threading.Thread | None = None
        self._lock     = threading.Lock()   # Schützt State-Schreibzugriffe aus API

    # ── Thread-Management ────────────────────────────────────────────────────

    def start(self) -> None:
        """Startet den Controller-Thread."""
        self._running = True
        self._thread  = threading.Thread(
            target=self._run_loop,
            name="reifeschrank-controller",
            daemon=True,
        )
        self._thread.start()
        logger.info("Controller gestartet.")

    def stop(self) -> None:
        """Beendet den Controller-Thread sauber."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        self._gpio.all_off("shutdown")
        self._archive.flush()
        logger.info("Controller gestoppt.")

    # ── Hauptschleife ─────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Haupt-Loop — läuft bis stop() aufgerufen wird."""
        logger.info("Regelschleife gestartet.")
        self._state.uptime_start = time.time()

        while self._running:
            cycle_start = time.time()

            try:
                self._run_cycle()
            except Exception as e:
                logger.error(f"Unbehandelter Fehler im Regelzyklus: {e}", exc_info=True)
                self._state.last_error = str(e)

            # Intervall einhalten (abzüglich Zykluszeit)
            interval = (
                2 if self._state.mode == MODE_MANUAL
                else self._state.refreshrate
            )
            elapsed  = time.time() - cycle_start
            sleep_time = max(0.1, interval - elapsed)
            time.sleep(sleep_time)

    def _run_cycle(self) -> None:
        """Ein vollständiger Regelzyklus."""
        self._state.cycle_count += 1

        # 1. Konfiguration neu laden (aus DB, falls UI etwas geändert hat)
        self._state_mgr.reload_config(self._state)

        # 2. Sensoren lesen
        self._read_sensors()

        # 3. Regelung oder Safe-Mode
        if self._state.safe_mode:
            self._apply_safe_mode()
        elif self._state.mode == MODE_MEASURE_ONLY:
            self._all_relays_off()
        elif self._state.mode == MODE_MANUAL:
            pass   # Im Manuell-Modus keine automatische Regelung
        else:
            # 4. Reifeprogramm prüfen
            self._programs.check_and_advance(self._state)
            # 5. Regellogik
            self._regulate()

        # 6. GPIO-Ausgänge setzen
        self._gpio.apply_state(self._state)

        # 7. State-Datei schreiben (RAM, kein SD)
        self._state_mgr.write_state_file(self._state)

        # 8. Archiv-Buffer befüllen + ggf. flushen (SD, gebatcht)
        self._archive.add(self._state)
        self._archive.maybe_flush()

    # ── Sensor-Lesen ─────────────────────────────────────────────────────────

    def _read_sensors(self) -> None:
        """Liest alle Sensoren und schreibt Werte in den State."""
        reading = self._sensors.read_all()

        if reading.intern_ok:
            self._state.temp_intern    = reading.temp_intern
            self._state.hum_intern     = reading.hum_intern
            self._state.abs_hum_intern = reading.abs_hum_intern
            self._state.sensor_fail_count = 0
        else:
            self._state.sensor_fail_count += 1
            logger.warning(
                f"Interner Sensor fehlgeschlagen "
                f"({self._state.sensor_fail_count}/{self._fail_threshold})"
            )
            if self._state.sensor_fail_count >= self._fail_threshold:
                self._enter_safe_mode(
                    f"Interner Sensor {self._fail_threshold}× fehlgeschlagen"
                )

        # Außensensor — kein Safe-Mode bei Ausfall (optional)
        self._state.temp_extern    = reading.temp_extern
        self._state.hum_extern     = reading.hum_extern
        self._state.abs_hum_extern = reading.abs_hum_extern

        # Waage (optional)
        if reading.weight is not None:
            self._state.weight = reading.weight

    # ── Regellogik ────────────────────────────────────────────────────────────

    def _regulate(self) -> None:
        """Dispatcht auf Temperatur- und/oder Feuchtigkeitsregelung."""
        if self._state.temp_intern is None:
            return

        self._regulate_temperature()

        if self._state.mode == MODE_AUTO:
            self._regulate_humidity()

    def _regulate_temperature(self) -> None:
        """
        Hysterese-Regelung für Temperatur.

        Kühlung (COOL):
            Temp > Soll + temp_hyst_cool → AN
            Temp < Soll                  → AUS

        Heizung (HEAT):
            Temp < Soll - temp_hyst_heat → AN
            Temp > Soll                  → AUS

        Kompressor-Schutzzeit: Kühlung darf nicht starten wenn
        < compressor_protect_secs seit dem letzten Ausschalten.
        """
        temp   = self._state.temp_intern
        target = self._state.target_temp

        # ── Kühlung ──
        if temp > target + self._state.temp_hyst_cool:
            if self._can_start_compressor():
                if not self._state.relay_cool:
                    logger.info(
                        f"Kühlung AN: {temp}°C > {target + self._state.temp_hyst_cool}°C"
                    )
                self._state.relay_cool        = True
                self._state.relay_heat        = False
                self._state.compressor_protected = False
            else:
                self._state.compressor_protected = True
                logger.debug(
                    f"Kühlung gesperrt (Schutzzeit läuft noch "
                    f"{int(self._comp_protect - (time.time() - self._state.compressor_last_off))}s)"
                )

        elif temp <= target:
            if self._state.relay_cool:
                logger.info(f"Kühlung AUS: {temp}°C ≤ {target}°C")
                self._state.compressor_last_off = time.time()
            self._state.relay_cool           = False
            self._state.compressor_protected = False

        # ── Heizung ──
        if temp < target - self._state.temp_hyst_heat:
            if not self._state.relay_heat:
                logger.info(
                    f"Heizung AN: {temp}°C < {target - self._state.temp_hyst_heat}°C"
                )
            self._state.relay_heat = True
            self._state.relay_cool = False   # Kühlung und Heizung nie gleichzeitig

        elif temp >= target:
            if self._state.relay_heat:
                logger.info(f"Heizung AUS: {temp}°C ≥ {target}°C")
            self._state.relay_heat = False

    def _regulate_humidity(self) -> None:
        """
        Hysterese-Regelung für Luftfeuchtigkeit mit AHT21-Außensensor-Logik.

        Befeuchter (MOIS):
            Hum < Soll - hum_hyst_mois → AN
            Hum > Soll                 → AUS

        Frischluft (FANA) — Basis:
            Hum > Soll + hum_hyst_fana → AN (Entfeuchten)
            Hum < Soll                 → AUS

        AHT21-Korrektur (wenn Außensensor verfügbar):
            Außen feuchter + Hum < Soll - aht21_hyst_humidify → FANA AN (Befeuchten)
            FANA aktiv zum Trocknen + Außen feuchter          → FANA AUS (Schutz)

        Umluft (FANU):
            Läuft wenn Kühlung, Heizung, Befeuchter oder FANA aktiv ist.
        """
        hum    = self._state.hum_intern
        target = self._state.target_hum

        # ── Befeuchter ──
        if hum < target - self._state.hum_hyst_mois:
            if not self._state.relay_mois:
                logger.info(
                    f"Befeuchter AN: {hum}%rF < {target - self._state.hum_hyst_mois}%rF"
                )
            self._state.relay_mois = True

        elif hum >= target:
            if self._state.relay_mois:
                logger.info(f"Befeuchter AUS: {hum}%rF ≥ {target}%rF")
            self._state.relay_mois = False

        # ── Frischluft Basis-Logik ──
        if hum > target + self._state.hum_hyst_fana:
            if not self._state.relay_fana:
                logger.info(
                    f"FANA AN (Trocknen): {hum}%rF > {target + self._state.hum_hyst_fana}%rF"
                )
            self._state.relay_fana = True

        elif hum <= target:
            if self._state.relay_fana:
                logger.info(f"FANA AUS: {hum}%rF ≤ {target}%rF")
            self._state.relay_fana = False

        # ── AHT21-Außensensor-Korrektur ──
        self._apply_aht21_correction()

        # ── Umluft ──
        # Läuft mit wenn irgendein anderes Gerät aktiv ist
        any_active = (
            self._state.relay_cool or
            self._state.relay_heat or
            self._state.relay_mois or
            self._state.relay_fana
        )
        self._state.relay_fanu = any_active

    def _apply_aht21_correction(self) -> None:
        """
        Korrigiert FANA-Entscheidung anhand des absoluten Wassergehalts
        der Außenluft (AHT21-Sensor).

        Wird nur ausgeführt wenn Außensensor-Daten vorhanden sind.
        """
        ah_in  = self._state.abs_hum_intern
        ah_out = self._state.abs_hum_extern

        if ah_in is None or ah_out is None:
            return   # Kein Außensensor → Basis-Logik unverändert

        hum    = self._state.hum_intern
        target = self._state.target_hum
        thresh = self._state.abs_hum_threshold

        # Außen feuchter: FANA zum Befeuchten nutzen
        if (
            outside_is_wetter(ah_in, ah_out, thresh)
            and hum < target - self._state.aht21_hyst_humidify
        ):
            if not self._state.relay_fana:
                logger.info(
                    f"AHT21: FANA AN (Befeuchten) — Außen {ah_out:.2f} g/m³ > "
                    f"Innen {ah_in:.2f} g/m³, Hum {hum}% < {target - self._state.aht21_hyst_humidify}%"
                )
            self._state.relay_fana = True
            self._state.relay_fanu = True

        # Schutzfunktion: FANA war zum Trocknen aktiv, aber Außen ist feuchter
        if (
            self._state.relay_fana
            and outside_is_wetter(ah_in, ah_out, thresh)
            and hum > target + self._state.aht21_hyst_protect
        ):
            logger.info(
                f"AHT21: FANA AUS (Schutz) — Außen feuchter ({ah_out:.2f} > "
                f"{ah_in:.2f} g/m³), Trocknen wäre kontraproduktiv"
            )
            self._state.relay_fana = False
            if not (self._state.relay_cool or self._state.relay_heat or self._state.relay_mois):
                self._state.relay_fanu = False

    # ── Safe-Mode ─────────────────────────────────────────────────────────────

    def _enter_safe_mode(self, reason: str) -> None:
        """Wechselt in den Safe-Mode und benachrichtigt."""
        if self._state.safe_mode:
            return   # Bereits im Safe-Mode

        self._state.safe_mode        = True
        self._state.safe_mode_reason = reason
        logger.error(f"SAFE-MODE aktiviert: {reason}")

        if self._notifier:
            try:
                self._notifier.send(
                    subject="Reifeschrank: Safe-Mode aktiviert",
                    body=(
                        f"Der Reifeschrank ist in den Safe-Mode gewechselt.\n"
                        f"Grund: {reason}\n\n"
                        f"Alle Aktoren sind ausgeschaltet (außer Umluft).\n"
                        f"Bitte prüfen und Service neu starten."
                    ),
                )
            except Exception as e:
                logger.warning(f"Safe-Mode-Benachrichtigung fehlgeschlagen: {e}")

    def _all_relays_off(self) -> None:
        """Schaltet alle Relais ab (MODE_MEASURE_ONLY)."""
        self._state.relay_heat = False
        self._state.relay_cool = False
        self._state.relay_mois = False
        self._state.relay_fana = False
        self._state.relay_fanu = False

    def _apply_safe_mode(self) -> None:
        """
        Setzt alle Relais in den sicheren Zustand:
        Alles AUS außer Umluft (verhindert Schimmelbildung).
        """
        self._state.relay_heat = False
        self._state.relay_cool = False
        self._state.relay_mois = False
        self._state.relay_fana = False
        self._state.relay_fanu = True   # Umluft bleibt an

    # ── API-Interface ─────────────────────────────────────────────────────────

    def set_relay_manual(self, name: str, active: bool) -> bool:
        """
        Schaltet ein Relais manuell (nur im Modus MODE_MANUAL).
        Wird von der API aufgerufen — thread-safe über Lock.
        """
        with self._lock:
            if self._state.mode != MODE_MANUAL:
                logger.warning("Manuelles Schalten nur im Manuell-Modus möglich.")
                return False
            setattr(self._state, f"relay_{name}", active)
            self._gpio.set_relay(name, active, "manual")
            self._state_mgr.write_state_file(self._state)
            return True

    def all_off_immediate(self) -> None:
        """Schaltet sofort alle Relais aus (vor Shutdown)."""
        with self._lock:
            self._gpio.all_off("shutdown")

    def reset_safe_mode(self) -> None:
        """Setzt den Safe-Mode zurück (für API-Aufruf nach manueller Prüfung)."""
        with self._lock:
            self._state.safe_mode        = False
            self._state.safe_mode_reason = ""
            self._state.sensor_fail_count = 0
            logger.info("Safe-Mode manuell zurückgesetzt.")

    def update_config(self, **kwargs) -> None:
        """
        Aktualisiert Konfigurationsfelder im State (thread-safe).
        Wird von der API aufgerufen — persistiert sofort in DB.
        """
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)
            self._state_mgr.persist_config(self._state)
            self._state_mgr.write_state_file(self._state)

    # ── Hilfsfunktionen ───────────────────────────────────────────────────────

    def _can_start_compressor(self) -> bool:
        """Prüft ob die Kompressor-Schutzzeit abgelaufen ist."""
        elapsed = time.time() - self._state.compressor_last_off
        return elapsed >= self._comp_protect

    @property
    def state(self) -> SystemState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running and (self._thread is not None and self._thread.is_alive())
