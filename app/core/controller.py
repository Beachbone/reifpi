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
from collections import deque
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


class ReifpiController:
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
        rate_window_secs: int = 90,
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
        self._rate_window    = rate_window_secs
        # Messverlauf Innensensor für die Steigung: (timestamp, temp, hum)
        self._history: deque[tuple[float, float, float]] = deque()

        self._running  = False
        self._thread:  threading.Thread | None = None
        self._lock     = threading.Lock()   # Schützt State-Schreibzugriffe aus API

    # ── Thread-Management ────────────────────────────────────────────────────

    def start(self) -> None:
        """Startet den Controller-Thread."""
        self._running = True
        self._thread  = threading.Thread(
            target=self._run_loop,
            name="reifpi-controller",
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
            self._update_rates(time.time(), reading.temp_intern, reading.hum_intern)
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
        else:
            # Nur Temperatur: Feuchte-Aktoren aus, Umluft nur mit Heizen/Kühlen
            if self._state.relay_mois:
                self._state.mois_last_off = time.time()
            self._state.relay_mois = False
            self._state.relay_fana = False
            self._state.relay_fanu = self._state.relay_cool or self._state.relay_heat

    def _regulate_temperature(self) -> None:
        """
        Hysterese-Regelung für Temperatur mit Nachlauf-Kompensation.

        Kühlung (COOL):
            Temp > Soll + temp_hyst_cool                       → AN
            Prognose ≤ Soll + temp_offset_cool                 → AUS

        Heizung (HEAT):
            Temp < Soll - temp_hyst_heat                       → AN
            Prognose ≥ Soll - temp_offset_heat                 → AUS

        Prognose = Temp + Steigung × lead_secs (nur in Wirkrichtung des
        Aktors). Mit Offset und Vorhaltzeit 0 schaltet der Aktor wie bisher
        genau beim Soll ab. Der Offset wird auf die Hysterese begrenzt.

        Einschaltsperren:
            Kühlung: Kompressor-Schutzzeit bzw. min_off_secs_cool (das
                     Größere) und lock_secs_cool_after_heat nach Heizende.
            Heizung: min_off_secs_heat und lock_secs_heat_after_cool
                     nach Kühlende.
        """
        s      = self._state
        temp   = s.temp_intern
        target = s.target_temp
        now    = time.time()

        # ── Kühlung ──
        cool_on_at  = target + s.temp_hyst_cool
        cool_off_at = target + min(s.temp_offset_cool, s.temp_hyst_cool)

        if temp > cool_on_at:
            if s.relay_cool:
                s.compressor_protected = False
            elif not self._can_start_compressor():
                s.compressor_protected = True
                logger.debug(
                    f"Kühlung gesperrt (Schutzzeit läuft noch "
                    f"{int(self._compressor_wait_secs() - (now - s.compressor_last_off))}s)"
                )
            elif s.lock_secs_cool_after_heat and now - s.heat_last_off < s.lock_secs_cool_after_heat:
                logger.debug("Kühlung gesperrt (Sperre nach Heizende)")
            else:
                logger.info(f"Kühlung AN: {temp}°C > {cool_on_at}°C")
                s.relay_cool           = True
                s.compressor_protected = False
                self._heat_off()

        elif self._predict(temp, s.temp_rate, s.lead_secs_cool, -1) <= cool_off_at:
            if s.relay_cool:
                logger.info(
                    f"Kühlung AUS: {temp}°C (Prognose ≤ {cool_off_at}°C)"
                )
            self._cool_off()
            s.compressor_protected = False

        # ── Heizung ──
        heat_on_at  = target - s.temp_hyst_heat
        heat_off_at = target - min(s.temp_offset_heat, s.temp_hyst_heat)

        if temp < heat_on_at:
            if s.relay_heat:
                pass
            elif s.min_off_secs_heat and now - s.heat_last_off < s.min_off_secs_heat:
                logger.debug("Heizung gesperrt (Mindestpause)")
            elif s.lock_secs_heat_after_cool and now - s.cool_last_off < s.lock_secs_heat_after_cool:
                logger.debug("Heizung gesperrt (Sperre nach Kühlende)")
            else:
                logger.info(f"Heizung AN: {temp}°C < {heat_on_at}°C")
                s.relay_heat = True
                self._cool_off()   # Kühlung und Heizung nie gleichzeitig

        elif self._predict(temp, s.temp_rate, s.lead_secs_heat, +1) >= heat_off_at:
            if s.relay_heat:
                logger.info(
                    f"Heizung AUS: {temp}°C (Prognose ≥ {heat_off_at}°C)"
                )
            self._heat_off()

    def _regulate_humidity(self) -> None:
        """
        Hysterese-Regelung für Luftfeuchtigkeit mit AHT21-Außensensor-Logik.

        Befeuchter (MOIS):
            Hum < Soll - hum_hyst_mois           → AN (nach min_off_secs_mois)
            Prognose ≥ Soll - hum_offset_mois    → AUS

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

        # ── Befeuchter (mit Nachlauf-Kompensation wie Heizung) ──
        s           = self._state
        mois_on_at  = target - s.hum_hyst_mois
        mois_off_at = target - min(s.hum_offset_mois, s.hum_hyst_mois)

        if hum < mois_on_at:
            if s.relay_mois:
                pass
            elif s.min_off_secs_mois and time.time() - s.mois_last_off < s.min_off_secs_mois:
                logger.debug("Befeuchter gesperrt (Mindestpause)")
            else:
                logger.info(f"Befeuchter AN: {hum}%rF < {mois_on_at}%rF")
                s.relay_mois = True

        elif self._predict(hum, s.hum_rate, s.lead_secs_mois, +1) >= mois_off_at:
            if s.relay_mois:
                logger.info(
                    f"Befeuchter AUS: {hum}%rF (Prognose ≥ {mois_off_at}%rF)"
                )
                s.mois_last_off = time.time()
            s.relay_mois = False

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
        self._heat_off()
        self._cool_off()
        self._state.relay_mois = False
        self._state.relay_fana = False
        self._state.relay_fanu = False

    def _apply_safe_mode(self) -> None:
        """
        Setzt alle Relais in den sicheren Zustand:
        Alles AUS außer Umluft (verhindert Schimmelbildung).
        """
        self._heat_off()
        self._cool_off()
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

    def _compressor_wait_secs(self) -> int:
        """Wirksame Kompressor-Pause: Schutzzeit oder min_off_secs_cool, das Größere."""
        return max(self._comp_protect, self._state.min_off_secs_cool)

    def _can_start_compressor(self) -> bool:
        """Prüft ob die Kompressor-Schutzzeit abgelaufen ist."""
        elapsed = time.time() - self._state.compressor_last_off
        return elapsed >= self._compressor_wait_secs()

    def _cool_off(self) -> None:
        """Kühlung aus; merkt den Zeitpunkt nur bei echtem Abschalten."""
        if self._state.relay_cool:
            now = time.time()
            self._state.compressor_last_off = now
            self._state.cool_last_off       = now
        self._state.relay_cool = False

    def _heat_off(self) -> None:
        """Heizung aus; merkt den Zeitpunkt nur bei echtem Abschalten."""
        if self._state.relay_heat:
            self._state.heat_last_off = time.time()
        self._state.relay_heat = False

    @staticmethod
    def _predict(value: float, rate_per_min: float | None,
                 lead_secs: int, direction: int) -> float:
        """
        Prognostiziert den Messwert nach lead_secs Sekunden.

        Es zählt nur eine Steigung in Wirkrichtung des Aktors
        (direction +1 = steigend, -1 = fallend) — eine Gegenbewegung
        darf das Abschalten nicht verzögern.
        """
        if not lead_secs or rate_per_min is None:
            return value
        rate = max(rate_per_min, 0.0) if direction > 0 else min(rate_per_min, 0.0)
        return value + rate * lead_secs / 60.0

    def _update_rates(self, now: float, temp: float, hum: float) -> None:
        """
        Steigung von Temperatur und Feuchte per linearer Regression über
        das letzte Zeitfenster. Erst ab 3 Messungen und halbem Fenster
        gültig, sonst None (Vorhalt dann wirkungslos).
        """
        hist = self._history
        hist.append((now, temp, hum))
        while hist and now - hist[0][0] > self._rate_window:
            hist.popleft()

        if len(hist) < 3 or hist[-1][0] - hist[0][0] < self._rate_window / 2:
            self._state.temp_rate = None
            self._state.hum_rate  = None
            return

        n      = len(hist)
        mean_t = sum(h[0] for h in hist) / n
        var_t  = sum((h[0] - mean_t) ** 2 for h in hist)

        def slope(idx: int) -> float:
            mean_v = sum(h[idx] for h in hist) / n
            cov    = sum((h[0] - mean_t) * (h[idx] - mean_v) for h in hist)
            return cov / var_t * 60.0   # pro Minute

        self._state.temp_rate = round(slope(1), 3)
        self._state.hum_rate  = round(slope(2), 3)

    @property
    def state(self) -> SystemState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running and (self._thread is not None and self._thread.is_alive())
