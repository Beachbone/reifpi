"""
gpio_manager.py — GPIO-Abstraktion über gpiozero.

Einzige Datei im Projekt die gpiozero importiert.
Alle Relais sind active-low: GPIO LOW = Relais AN, GPIO HIGH = Relais AUS.

Im Dummy-Mode (DevConfig oder kein Pi) werden alle Schaltvorgänge
nur geloggt — kein echter GPIO-Zugriff.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

# Relay-Namen — entsprechen den Feldern in SystemState
RELAY_NAMES = ("fanu", "cool", "fana", "mois", "heat")


@dataclass
class RelayState:
    """Zustand eines einzelnen Relais."""
    name:         str
    pin:          int
    active:       bool  = False
    last_changed: float = field(default_factory=time.time)
    reason:       str   = ""


class GpioManager:
    """
    Verwaltet alle 5 Relais-Ausgänge.

    Pins werden beim __init__ aus dem übergebenen Pin-Dict konfiguriert.
    Dadurch können GPIO-Pins aus der DB kommen und bei Neustart neu
    eingelesen werden.

    Args:
        pins:  Dict mit Relay-Namen → GPIO-Pin-Nummer
               z.B. {"fanu": 27, "cool": 23, "fana": 17, "mois": 22, "heat": 24}
        dummy: True = kein echter GPIO-Zugriff (Entwicklung / Test)
    """

    def __init__(self, pins: dict[str, int], dummy: bool = False, active_high: bool = False) -> None:
        self._dummy = dummy
        self._active_high = active_high
        self._devices: dict[str, object] = {}
        self._states: dict[str, RelayState] = {}

        for name in RELAY_NAMES:
            pin = pins.get(name)
            if pin is None:
                raise ValueError(f"GPIO-Pin für Relais '{name}' fehlt.")
            self._states[name] = RelayState(name=name, pin=pin)

        self._init_devices()

    def _init_devices(self) -> None:
        if self._dummy:
            logger.info("GPIO-Dummy-Mode aktiv — keine echten GPIO-Zugriffe.")
            return

        try:
            from gpiozero import OutputDevice
            for name, relay in self._states.items():
                device = OutputDevice(
                    relay.pin,
                    active_high=self._active_high,
                    initial_value=False, # Beim Start alle Relais AUS
                )
                self._devices[name] = device
                logger.info(f"GPIO {relay.pin} ({name}) initialisiert.")
        except Exception as e:
            logger.error(f"GPIO-Initialisierung fehlgeschlagen: {e}")
            logger.warning("Wechsle automatisch in Dummy-Mode.")
            self._dummy = True
            self._devices.clear()

    def set_relay(self, name: str, active: bool, reason: str = "") -> bool:
        """
        Schaltet ein Relais.

        Args:
            name:   Relay-Name (fanu/cool/fana/mois/heat)
            active: True = AN, False = AUS
            reason: Optionaler Grund für das Schalten (für Logging)

        Returns:
            True wenn erfolgreich, False bei Fehler.
        """
        if name not in self._states:
            logger.error(f"Unbekanntes Relais: {name}")
            return False

        relay = self._states[name]
        if relay.active == active:
            return True   # Bereits im gewünschten Zustand — nichts zu tun

        relay.active       = active
        relay.last_changed = time.time()
        relay.reason       = reason

        state_str = "AN" if active else "AUS"
        logger.info(f"Relais {name} (GPIO {relay.pin}) → {state_str}  [{reason}]")

        if self._dummy:
            return True

        try:
            device = self._devices.get(name)
            if device:
                if active:
                    device.on()
                else:
                    device.off()
            return True
        except Exception as e:
            logger.error(f"GPIO-Fehler bei Relais {name}: {e}")
            return False

    def get_state(self, name: str) -> bool:
        """Gibt den aktuellen Zustand eines Relais zurück."""
        return self._states[name].active if name in self._states else False

    def get_all_states(self) -> dict[str, bool]:
        """Gibt alle Relay-Zustände als Dict zurück."""
        return {name: relay.active for name, relay in self._states.items()}

    def all_off(self, reason: str = "all_off") -> None:
        """Schaltet alle Relais aus. Wird für Safe-Mode und Shutdown verwendet."""
        for name in RELAY_NAMES:
            self.set_relay(name, False, reason)
        logger.info(f"Alle Relais AUS ({reason}).")

    def apply_state(self, state: "SystemState") -> None:  # type: ignore[name-defined]
        """
        Setzt alle GPIO-Ausgänge entsprechend den relay_*-Feldern im SystemState.
        Wird vom Controller am Ende jedes Zyklus aufgerufen.
        """
        self.set_relay("heat", state.relay_heat, "controller")
        self.set_relay("cool", state.relay_cool, "controller")
        self.set_relay("mois", state.relay_mois, "controller")
        self.set_relay("fanu", state.relay_fanu, "controller")
        self.set_relay("fana", state.relay_fana, "controller")

    def close(self) -> None:
        """Schließt alle gpiozero-Devices sauber."""
        self.all_off("shutdown")
        if not self._dummy:
            for device in self._devices.values():
                try:
                    device.close()
                except Exception:
                    pass
        logger.info("GPIO-Manager geschlossen.")

    @property
    def is_dummy(self) -> bool:
        return self._dummy
