"""
sensors.py — Sensor-Abstraktion für SHT31 (intern) und AHT21 (extern).

Drei Implementierungen pro Sensor:
  - Echte Hardware (I2C via smbus2)
  - Simulation (für Entwicklung ohne Pi)
  - None-Sensor (Außensensor nicht vorhanden)

SensorManager liest beide Sensoren und liefert ein SensorReading-Objekt.
Fehler werden gezählt — der Controller entscheidet ab wann Safe-Mode.
"""

import logging
import math
import random
import time
from dataclasses import dataclass
from typing import Protocol

from app.utils.physics import calc_absolute_humidity

logger = logging.getLogger(__name__)


@dataclass
class SensorReading:
    """Ergebnis einer Sensor-Messung."""
    temp_intern:    float | None = None
    hum_intern:     float | None = None
    temp_extern:    float | None = None
    hum_extern:     float | None = None
    abs_hum_intern: float | None = None
    abs_hum_extern: float | None = None
    intern_ok:      bool = False
    extern_ok:      bool = False


class SensorBase(Protocol):
    """Interface für alle Sensor-Implementierungen."""
    def read(self) -> tuple[float, float] | tuple[None, None]:
        """Gibt (temp_c, rel_hum_pct) zurück oder (None, None) bei Fehler."""
        ...


# ── SHT31 ────────────────────────────────────────────────────────────────────

class SHT31:
    """
    SHT31 Temperatur/Feuchtigkeitssensor via I2C.
    I2C-Adresse typisch 0x44 (intern) oder 0x45.
    """

    CMD_SINGLE_SHOT = [0x24, 0x00]   # High Repeatability, Clock Stretching disabled

    def __init__(self, bus: int = 1, address: int = 0x44) -> None:
        self._bus     = bus
        self._address = address

    def read(self) -> tuple[float, float] | tuple[None, None]:
        try:
            from smbus2 import SMBus, i2c_msg

            with SMBus(self._bus) as bus:
                bus.write_i2c_block_data(
                    self._address, self.CMD_SINGLE_SHOT[0], [self.CMD_SINGLE_SHOT[1]]
                )
                time.sleep(0.02)   # Messung abwarten (High Repeatability: ~15ms)

                msg = i2c_msg.read(self._address, 6)
                bus.i2c_rdwr(msg)
                data = list(msg)

            temp_raw = data[0] * 256 + data[1]
            hum_raw  = data[3] * 256 + data[4]
            temp = round(-45.0 + (175.0 * temp_raw / 65535.0), 2)
            hum  = round(100.0 * hum_raw / 65535.0, 2)

            if not (-40 <= temp <= 125) or not (0 <= hum <= 100):
                logger.warning(f"SHT31: Wert außerhalb Bereich: {temp}°C / {hum}%")
                return None, None

            return temp, hum

        except Exception as e:
            logger.warning(f"SHT31 (0x{self._address:02X}) Lesefehler: {e}")
            return None, None


# ── AHT21 ────────────────────────────────────────────────────────────────────

class AHT21:
    """
    AHT21 Temperatur/Feuchtigkeitssensor via I2C.
    I2C-Adresse 0x38.
    """

    CMD_INIT    = [0xBE, 0x08, 0x00]
    CMD_MEASURE = [0xAC, 0x33, 0x00]
    STATUS_BUSY = 0x80
    STATUS_CALIBRATED = 0x08

    def __init__(self, bus: int = 1, address: int = 0x38) -> None:
        self._bus     = bus
        self._address = address

    def read(self) -> tuple[float, float] | tuple[None, None]:
        try:
            import smbus2

            with smbus2.SMBus(self._bus) as bus:
                # Kalibrierung prüfen
                status = bus.read_byte(self._address)
                if not (status & self.STATUS_CALIBRATED):
                    bus.write_i2c_block_data(self._address, 0x00, self.CMD_INIT)
                    time.sleep(0.01)

                # Messung starten
                bus.write_i2c_block_data(self._address, 0x00, self.CMD_MEASURE)
                time.sleep(0.08)

                # Auf Bereitschaft warten (max. 100ms)
                for _ in range(10):
                    if not (bus.read_byte(self._address) & self.STATUS_BUSY):
                        break
                    time.sleep(0.01)

                data = bus.read_i2c_block_data(self._address, 0x00, 6)

            raw_hum  = ((data[1] << 12) | (data[2] << 4) | (data[3] >> 4))
            raw_temp = (((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5])
            temp = round((raw_temp / 1048576.0) * 200.0 - 50.0, 2)
            hum  = round((raw_hum  / 1048576.0) * 100.0, 2)

            if not (-40 <= temp <= 85) or not (0 <= hum <= 100):
                logger.warning(f"AHT21: Wert außerhalb Bereich: {temp}°C / {hum}%")
                return None, None

            return temp, hum

        except Exception as e:
            logger.warning(f"AHT21 (0x{self._address:02X}) Lesefehler: {e}")
            return None, None


# ── BME280 ───────────────────────────────────────────────────────────────────

class BME280:
    """
    BME280 Temperatur/Feuchtigkeitssensor via I2C.
    I2C-Adresse 0x76 (SDO=GND) oder 0x77 (SDO=VCC).
    Chip-ID 0x60. (BMP280 = 0x58, liefert keine Feuchte.)
    """

    REG_ID        = 0xD0
    REG_CTRL_HUM  = 0xF2
    REG_CTRL_MEAS = 0xF4
    REG_DATA      = 0xF7
    REG_CALIB1    = 0x88
    REG_CALIB_H1  = 0xA1
    REG_CALIB2    = 0xE1
    CHIP_ID_BME   = 0x60

    def __init__(self, bus: int = 1, address: int = 0x76) -> None:
        self._bus     = bus
        self._address = address
        self._calib   = None

    def _read_calibration(self, bus) -> dict:
        import struct
        raw1 = bus.read_i2c_block_data(self._address, self.REG_CALIB1, 24)
        dig_T1, dig_T2, dig_T3 = struct.unpack_from('<Hhh', bytes(raw1), 0)
        dig_H1 = bus.read_byte_data(self._address, self.REG_CALIB_H1)
        raw2 = bus.read_i2c_block_data(self._address, self.REG_CALIB2, 7)
        dig_H2, dig_H3 = struct.unpack_from('<hB', bytes(raw2), 0)
        dig_H4 = (raw2[3] << 4) | (raw2[4] & 0x0F)
        if dig_H4 > 2047:
            dig_H4 -= 4096
        dig_H5 = (raw2[5] << 4) | (raw2[4] >> 4)
        if dig_H5 > 2047:
            dig_H5 -= 4096
        dig_H6 = struct.unpack_from('<b', bytes(raw2), 6)[0]
        return {
            'T1': dig_T1, 'T2': dig_T2, 'T3': dig_T3,
            'H1': dig_H1, 'H2': dig_H2, 'H3': dig_H3,
            'H4': dig_H4, 'H5': dig_H5, 'H6': dig_H6,
        }

    def _compensate(self, adc_T: int, adc_H: int, c: dict) -> tuple[float, float]:
        var1 = (adc_T / 16384.0 - c['T1'] / 1024.0) * c['T2']
        var2 = (adc_T / 131072.0 - c['T1'] / 8192.0) ** 2 * c['T3']
        t_fine = var1 + var2
        temp = t_fine / 5120.0
        h = t_fine - 76800.0
        h = ((adc_H - (c['H4'] * 64.0 + c['H5'] / 16384.0 * h)) *
             (c['H2'] / 65536.0 * (1.0 + c['H6'] / 67108864.0 * h *
             (1.0 + c['H3'] / 67108864.0 * h))))
        h = h * (1.0 - c['H1'] * h / 524288.0)
        return round(temp, 2), round(max(0.0, min(100.0, h)), 2)

    def read(self) -> tuple[float, float] | tuple[None, None]:
        try:
            import smbus2

            with smbus2.SMBus(self._bus) as bus:
                chip_id = bus.read_byte_data(self._address, self.REG_ID)
                if chip_id != self.CHIP_ID_BME:
                    logger.warning(
                        f"BME280: Chip-ID 0x{chip_id:02X} (erwartet 0x60 — "
                        "BMP280 0x58 liefert keine Feuchte)"
                    )
                    return None, None

                if self._calib is None:
                    self._calib = self._read_calibration(bus)

                bus.write_byte_data(self._address, self.REG_CTRL_HUM, 0x01)
                bus.write_byte_data(self._address, self.REG_CTRL_MEAS, 0x25)
                time.sleep(0.01)

                data = bus.read_i2c_block_data(self._address, self.REG_DATA, 8)

            adc_T = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
            adc_H = (data[6] << 8) | data[7]
            temp, hum = self._compensate(adc_T, adc_H, self._calib)

            if not (-40 <= temp <= 85) or not (0 <= hum <= 100):
                logger.warning(f"BME280: Wert außerhalb Bereich: {temp}°C / {hum}%")
                return None, None

            return temp, hum

        except Exception as e:
            logger.warning(f"BME280 (0x{self._address:02X}) Lesefehler: {e}")
            self._calib = None
            return None, None


# ── Simulation ────────────────────────────────────────────────────────────────

class SimulatedSensor:
    """
    Simulierter Sensor für Entwicklung und Tests ohne Hardware.
    Erzeugt realistische Schwankungen um einen Basiswert.
    """

    def __init__(
        self,
        base_temp: float = 14.0,
        base_hum:  float = 80.0,
        temp_noise: float = 0.3,
        hum_noise:  float = 1.5,
        name: str = "sim",
    ) -> None:
        self._base_temp  = base_temp
        self._base_hum   = base_hum
        self._temp_noise = temp_noise
        self._hum_noise  = hum_noise
        self._name       = name
        self._t          = 0.0   # Zeitvariable für sinus-basierte Drift

    def read(self) -> tuple[float, float]:
        self._t += 0.1
        # Langsame sinusförmige Drift + Rauschen
        temp = round(
            self._base_temp
            + math.sin(self._t * 0.3) * 0.5
            + random.gauss(0, self._temp_noise),
            2,
        )
        hum = round(
            self._base_hum
            + math.sin(self._t * 0.2) * 2.0
            + random.gauss(0, self._hum_noise),
            2,
        )
        hum = max(0.0, min(100.0, hum))
        return temp, hum


class NoneSensor:
    """Platzhalter wenn kein Sensor konfiguriert ist."""

    def read(self) -> tuple[None, None]:
        return None, None


# ── Waagen-Basis ──────────────────────────────────────────────────────────────

class WeightSensorBase(Protocol):
    """Interface für Waagen-Sensoren (für spätere HX711-Integration)."""
    def read_weight(self) -> float | None:
        """Gibt Gewicht in Gramm zurück oder None bei Fehler."""
        ...


class NoWeightSensor:
    """Platzhalter wenn keine Waage vorhanden."""
    def read_weight(self) -> None:
        return None


# ── SensorManager ─────────────────────────────────────────────────────────────

class SensorManager:
    """
    Koordiniert alle Sensoren und liefert ein vollständiges SensorReading.

    Liest jeden Sensor READ_COUNT-mal und bildet den Mittelwert
    (verwirft None-Werte). Dadurch ist kein separates 10x-Loop
    im Hauptscript mehr nötig.
    """

    READ_COUNT = 3   # Anzahl Reads für Mittelwert

    def __init__(
        self,
        intern_sensor: SensorBase,
        extern_sensor: SensorBase,
        weight_sensor: WeightSensorBase | None = None,
    ) -> None:
        self._intern = intern_sensor
        self._extern = extern_sensor
        self._weight = weight_sensor or NoWeightSensor()

    def read_all(self) -> SensorReading:
        """
        Liest alle Sensoren, bildet Mittelwert, berechnet absolute Feuchte.
        """
        reading = SensorReading()

        t_i, h_i = self._read_averaged(self._intern, "intern")
        reading.temp_intern = t_i
        reading.hum_intern  = h_i
        reading.intern_ok   = t_i is not None

        t_e, h_e = self._read_averaged(self._extern, "extern")
        reading.temp_extern = t_e
        reading.hum_extern  = h_e
        reading.extern_ok   = t_e is not None

        # Absolute Feuchtigkeit berechnen
        if reading.intern_ok:
            reading.abs_hum_intern = calc_absolute_humidity(t_i, h_i)
        if reading.extern_ok:
            reading.abs_hum_extern = calc_absolute_humidity(t_e, h_e)

        # Gewicht (optional)
        try:
            reading.weight = self._weight.read_weight()  # type: ignore[union-attr]
        except Exception as e:
            logger.warning(f"Waage Lesefehler: {e}")

        return reading

    def _read_averaged(
        self, sensor: SensorBase, label: str
    ) -> tuple[float, float] | tuple[None, None]:
        """
        Liest den Sensor READ_COUNT-mal und gibt den Mittelwert zurück.
        Ungültige Lesungen (None) werden übersprungen.
        """
        temps, hums = [], []

        for _ in range(self.READ_COUNT):
            t, h = sensor.read()
            if t is not None and h is not None:
                temps.append(t)
                hums.append(h)

        if not temps:
            logger.warning(f"Sensor {label}: alle {self.READ_COUNT} Lesungen fehlgeschlagen.")
            return None, None

        avg_temp = round(sum(temps) / len(temps), 2)
        avg_hum  = round(sum(hums)  / len(hums),  2)
        logger.debug(f"Sensor {label}: {avg_temp}°C / {avg_hum}%rF ({len(temps)}/{self.READ_COUNT} OK)")
        return avg_temp, avg_hum


def build_sensor_manager(
    config: type,
    dummy: bool = False,
    db_config: dict | None = None,
) -> SensorManager:
    """
    Factory: Erzeugt den SensorManager passend zur Konfiguration.

    Args:
        config:    Konfigurationsklasse (ProdConfig oder DevConfig)
        dummy:     True = Simulation verwenden (überschreibt config.SENSOR_DUMMY)
        db_config: Optionale DB-Werte (sensor_intern, sensor_extern, i2c_bus,
                   sht31_address, aht21_address, bme280_address) — überschreiben
                   die Defaults aus der Konfigurationsklasse.
    """
    use_dummy = dummy or getattr(config, "SENSOR_DUMMY", False)

    if use_dummy:
        logger.info("Sensor-Simulation aktiv.")
        intern_sensor = SimulatedSensor(base_temp=14.0, base_hum=80.0, name="intern")
        extern_sensor = SimulatedSensor(base_temp=17.0, base_hum=65.0, name="extern")
    else:
        sc = db_config or {}
        sensor_intern        = sc.get("sensor_intern")         or getattr(config, "SENSOR_INTERN",         "sht31")
        sensor_intern_address= sc.get("sensor_intern_address") or getattr(config, "SENSOR_INTERN_ADDRESS", 0x44)
        sensor_extern        = sc.get("sensor_extern")         or getattr(config, "SENSOR_EXTERN",         "aht21")
        sensor_extern_address= sc.get("sensor_extern_address") or getattr(config, "SENSOR_EXTERN_ADDRESS", 0x38)
        i2c_bus              = sc.get("i2c_bus")               or config.I2C_BUS

        logger.info(
            f"Sensoren: intern={sensor_intern} @ 0x{sensor_intern_address:02X}, "
            f"extern={sensor_extern} @ 0x{sensor_extern_address:02X}, "
            f"I2C-Bus={i2c_bus}"
        )

        intern_sensor = _create_sensor(sensor_intern, i2c_bus, sensor_intern_address)
        extern_sensor = _create_sensor(sensor_extern, i2c_bus, sensor_extern_address)

    weight_sensor = _create_weight_sensor(config)
    return SensorManager(intern_sensor, extern_sensor, weight_sensor)


def _create_sensor(
    sensor_type: str, bus: int, address: int
) -> SensorBase:
    """Erstellt eine Sensor-Instanz anhand des Typs."""
    if sensor_type == "sht31":
        return SHT31(bus=bus, address=address)
    elif sensor_type == "aht21":
        return AHT21(bus=bus, address=address)
    elif sensor_type == "bme280":
        return BME280(bus=bus, address=address)
    elif sensor_type in ("none", None, ""):
        return NoneSensor()
    else:
        logger.warning(f"Unbekannter Sensor-Typ '{sensor_type}' — verwende None-Sensor.")
        return NoneSensor()


def _create_weight_sensor(config: type) -> WeightSensorBase:
    """Erstellt den Waagen-Sensor anhand der Konfiguration."""
    sensor_type = getattr(config, "WEIGHT_SENSOR_TYPE", None)
    if not sensor_type:
        return NoWeightSensor()

    if sensor_type == "hx711":
        # Platzhalter — wird implementiert wenn Hardware bekannt
        logger.info("HX711-Waage konfiguriert (noch nicht implementiert — NoWeightSensor).")
        return NoWeightSensor()

    logger.warning(f"Unbekannter Waagen-Typ '{sensor_type}'.")
    return NoWeightSensor()
