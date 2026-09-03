"""
physics.py — Physikalische Berechnungen für die Feuchtigkeitsregelung.

Absolute Luftfeuchtigkeit nach Magnus-Formel:
    AH = (6.112 · e^(17.67·T / (T+243.5)) · rF · 2.1674) / (273.15 + T)
Ergebnis in g/m³.
"""

import math


# Magnus-Konstanten (nach Alduchov & Eskridge, 1996)
_A = 6.112
_B = 17.67
_C = 243.5
_D = 2.1674


def calc_absolute_humidity(temp_c: float, rel_hum_pct: float) -> float | None:
    """
    Berechnet den absoluten Wassergehalt der Luft in g/m³.

    Args:
        temp_c:       Temperatur in °C
        rel_hum_pct:  Relative Luftfeuchtigkeit in % (0–100)

    Returns:
        Absoluter Wassergehalt in g/m³, oder None bei ungültigen Eingaben.
    """
    if temp_c is None or rel_hum_pct is None:
        return None
    if not (-50.0 <= temp_c <= 60.0):
        return None
    if not (0.0 <= rel_hum_pct <= 100.0):
        return None

    es = _A * math.exp((_B * temp_c) / (temp_c + _C))
    ah = (es * rel_hum_pct * _D) / (273.15 + temp_c)
    return round(ah, 3)


def outside_is_wetter(
    abs_hum_inside: float | None,
    abs_hum_outside: float | None,
    threshold_g_m3: float = 0.5,
) -> bool:
    """
    Gibt True zurück wenn Außenluft absolut feuchter als Innenluft ist
    (unter Berücksichtigung eines Mindestschwellwerts gegen Flattern).

    Args:
        abs_hum_inside:   Absoluter Wassergehalt Innen [g/m³]
        abs_hum_outside:  Absoluter Wassergehalt Außen [g/m³]
        threshold_g_m3:   Mindestdifferenz [g/m³] damit das Ergebnis True wird

    Returns:
        True wenn Außen um mindestens threshold_g_m3 feuchter als Innen.
    """
    if abs_hum_inside is None or abs_hum_outside is None:
        return False
    return abs_hum_outside > abs_hum_inside + threshold_g_m3


def outside_is_drier(
    abs_hum_inside: float | None,
    abs_hum_outside: float | None,
    threshold_g_m3: float = 0.5,
) -> bool:
    """
    Gibt True zurück wenn Außenluft absolut trockener als Innenluft ist.
    """
    if abs_hum_inside is None or abs_hum_outside is None:
        return False
    return abs_hum_inside > abs_hum_outside + threshold_g_m3
