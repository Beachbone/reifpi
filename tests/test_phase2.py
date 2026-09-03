"""
test_phase2.py — Smoke-Tests für Phase 2 (ohne Hardware).

Prüft: GPIO-Manager (Dummy), Sensoren (Simulation), SensorManager.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.gpio_manager import GpioManager, RELAY_NAMES
from app.core.sensors import (
    SimulatedSensor, NoneSensor, NoWeightSensor,
    SensorManager, build_sensor_manager,
)
from app.config import DevConfig


def test_gpio_dummy():
    print("── GPIO Manager (Dummy) ──────────────────────────")
    pins = {
        "fanu": 27, "cool": 23, "fana": 17, "mois": 22, "heat": 24
    }
    gpio = GpioManager(pins=pins, dummy=True)
    assert gpio.is_dummy

    # Alle initial AUS
    for name in RELAY_NAMES:
        assert gpio.get_state(name) is False

    # Einzeln schalten
    gpio.set_relay("cool", True, "test")
    assert gpio.get_state("cool") is True
    assert gpio.get_state("heat") is False

    gpio.set_relay("cool", False, "test")
    assert gpio.get_state("cool") is False

    # all_off
    gpio.set_relay("heat", True, "test")
    gpio.set_relay("mois", True, "test")
    gpio.all_off("test")
    states = gpio.get_all_states()
    assert all(v is False for v in states.values()), f"Nicht alle AUS: {states}"

    # Unbekanntes Relais gibt False zurück
    result = gpio.set_relay("unknown", True)
    assert result is False

    gpio.close()
    print("  ✓ GPIO Dummy-Mode OK")


def test_gpio_apply_state():
    print("── GPIO apply_state ─────────────────────────────")
    from app.core.state import SystemState
    pins = {"fanu": 27, "cool": 23, "fana": 17, "mois": 22, "heat": 24}
    gpio = GpioManager(pins=pins, dummy=True)

    state = SystemState()
    state.relay_cool = True
    state.relay_fanu = True
    state.relay_heat = False
    state.relay_mois = False
    state.relay_fana = False

    gpio.apply_state(state)

    assert gpio.get_state("cool") is True
    assert gpio.get_state("fanu") is True
    assert gpio.get_state("heat") is False
    assert gpio.get_state("mois") is False

    gpio.close()
    print("  ✓ apply_state OK")


def test_simulated_sensor():
    print("── Simulierter Sensor ───────────────────────────")
    sensor = SimulatedSensor(base_temp=14.0, base_hum=80.0)

    readings = [sensor.read() for _ in range(10)]
    temps = [r[0] for r in readings]
    hums  = [r[1] for r in readings]

    assert all(t is not None for t in temps)
    assert all(h is not None for h in hums)
    assert all(10 < t < 18 for t in temps), f"Temp außerhalb Bereich: {temps}"
    assert all(0 < h < 100 for h in hums),  f"Hum außerhalb Bereich: {hums}"

    print(f"  10 Messungen — Temp: {min(temps):.1f}–{max(temps):.1f}°C, "
          f"Hum: {min(hums):.1f}–{max(hums):.1f}%")
    print("  ✓ Simulation OK")


def test_none_sensor():
    print("── None-Sensor ──────────────────────────────────")
    sensor = NoneSensor()
    t, h = sensor.read()
    assert t is None and h is None
    print("  ✓ None-Sensor OK")


def test_sensor_manager():
    print("── SensorManager ────────────────────────────────")
    intern = SimulatedSensor(base_temp=14.0, base_hum=80.0)
    extern = SimulatedSensor(base_temp=18.0, base_hum=65.0)

    mgr = SensorManager(intern_sensor=intern, extern_sensor=extern)
    reading = mgr.read_all()

    assert reading.intern_ok, "Interner Sensor fehlgeschlagen"
    assert reading.extern_ok, "Externer Sensor fehlgeschlagen"
    assert reading.temp_intern is not None
    assert reading.hum_intern  is not None
    assert reading.temp_extern is not None
    assert reading.abs_hum_intern is not None
    assert reading.abs_hum_extern is not None

    print(f"  Intern: {reading.temp_intern}°C / {reading.hum_intern}%rF "
          f"→ {reading.abs_hum_intern} g/m³")
    print(f"  Extern: {reading.temp_extern}°C / {reading.hum_extern}%rF "
          f"→ {reading.abs_hum_extern} g/m³")
    print("  ✓ SensorManager OK")


def test_sensor_manager_with_failed_extern():
    print("── SensorManager — Außensensor ausgefallen ──────")
    intern = SimulatedSensor(base_temp=14.0, base_hum=80.0)
    extern = NoneSensor()

    mgr = SensorManager(intern_sensor=intern, extern_sensor=extern)
    reading = mgr.read_all()

    assert reading.intern_ok,      "Interner Sensor sollte OK sein"
    assert not reading.extern_ok,  "Externer Sensor sollte fehlgeschlagen sein"
    assert reading.abs_hum_intern is not None
    assert reading.abs_hum_extern is None
    assert reading.temp_extern    is None

    print(f"  Intern OK: {reading.temp_intern}°C, Extern: None (erwartet)")
    print("  ✓ Graceful degradation bei fehlendem Außensensor OK")


def test_build_sensor_manager():
    print("── build_sensor_manager (Factory) ───────────────")
    mgr = build_sensor_manager(DevConfig, dummy=True)
    reading = mgr.read_all()

    assert reading.intern_ok
    assert reading.extern_ok
    print(f"  Factory-Build: {reading.temp_intern}°C / {reading.hum_intern}%")
    print("  ✓ Factory OK")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)

    print("\n╔══════════════════════════════════════════╗")
    print("║  Reife-Pi v2 — Phase 2 Smoke-Tests      ║")
    print("╚══════════════════════════════════════════╝\n")

    try:
        test_gpio_dummy()
        test_gpio_apply_state()
        test_simulated_sensor()
        test_none_sensor()
        test_sensor_manager()
        test_sensor_manager_with_failed_extern()
        test_build_sensor_manager()
        print("\n✅  Alle Tests bestanden.\n")
    except AssertionError as e:
        print(f"\n❌  Test fehlgeschlagen: {e}\n")
        sys.exit(1)
