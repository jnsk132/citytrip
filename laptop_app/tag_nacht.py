# -*- coding: utf-8 -*-
"""Tag/Nacht-Idle-Modus: Sonnenstand pro Stadt -> LED-Farbe.

Reine Logik, keine Hardware/HTTP. qrscanner.py baut daraus den Frame
(Liste von RGB-Werten) und schickt ihn per /frame an den ESP32.

Idee:
  * Stadt im hellen Tageslicht  -> warmweiß (hell)
  * Stadt bei Sonnenauf-/untergang (nahe Horizont) -> orange
  * Stadt in der Nacht          -> gedimmtes Dunkelblau

Die Hell/Dunkel-Grenze (Terminator) wandert übers Jahr und über den Tag
sichtbar über die Weltkarte.
"""

import json
import math
import os
from datetime import datetime, timezone

from city_coords import CITY_COORDS

JSON_MAPPING_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "led_mapping.json"
)

# ── Farben (normales RGB; der GRB-Tausch passiert erst am ESP32) ──
FARBE_TAG    = (255, 236, 196)   # heller, warmer Tag
FARBE_ORANGE = (255, 92, 10)     # Sonnenauf-/untergang
FARBE_NACHT  = (3, 9, 42)        # gedimmtes Dunkelblau

# Globaler Helligkeits-Regler (0..1) – falls die Kette zu hell/grell ist
# oder das Netzteil schwächelt, hier herunterdrehen.
HELLIGKEIT = 1.0

# Höhenwinkel-Schwellen (Grad über Horizont):
#   >= ELEV_TAG          -> voller Tag
#   ELEV_TAG .. 0        -> Tag  -> Orange
#   0 .. ELEV_NACHT      -> Orange -> Nacht (bürgerliche Dämmerung)
#   <= ELEV_NACHT        -> volle Nacht
ELEV_TAG   = 8.0
ELEV_NACHT = -8.0


def set_idle_farben(tag=None, orange=None, nacht=None, helligkeit=None):
    """Setzt die Idle-Farben/Helligkeit zur Laufzeit (von der App steuerbar).

    Nur übergebene Werte werden geändert; `elevation_to_color`/`_dimmen`
    lesen diese Modul-Globals, daher wirkt eine Änderung ab dem nächsten
    berechneten Frame.
    """
    global FARBE_TAG, FARBE_ORANGE, FARBE_NACHT, HELLIGKEIT
    if tag is not None:
        FARBE_TAG = tuple(int(c) for c in tag)
    if orange is not None:
        FARBE_ORANGE = tuple(int(c) for c in orange)
    if nacht is not None:
        FARBE_NACHT = tuple(int(c) for c in nacht)
    if helligkeit is not None:
        HELLIGKEIT = max(0.0, min(1.0, float(helligkeit)))


def get_idle_farben():
    """Aktuelle Idle-Farben/Helligkeit als Dict (für die App-Initialisierung)."""
    return {
        "tag": FARBE_TAG,
        "orange": FARBE_ORANGE,
        "nacht": FARBE_NACHT,
        "helligkeit": HELLIGKEIT,
    }


def sonnen_elevation(lat, lon, when=None):
    """Sonnenhöhe in Grad über dem Horizont für lat/lon zur UTC-Zeit `when`.

    Vereinfachte Astronomie (ohne Zeitgleichung) – für eine Lichtanzeige
    mehr als genau genug (Fehler < ~15 min Sonnenzeit).
    """
    if when is None:
        when = datetime.now(timezone.utc)

    n = when.timetuple().tm_yday                       # Tag im Jahr (1..366)
    utc_h = when.hour + when.minute / 60 + when.second / 3600

    # Sonnendeklination (in Radiant; radians() ist linear, daher direkt nutzbar)
    decl = math.radians(23.45) * math.sin(math.radians(360 / 365 * (284 + n)))

    # Stundenwinkel: 0 = lokaler Sonnenmittag. 15°/h, +lon verschiebt die
    # lokale Sonnenzeit, -180° bringt UTC-Mittag auf Länge 0.
    H = math.radians(15 * utc_h + lon - 180)

    lat_r = math.radians(lat)
    sin_elev = (math.sin(lat_r) * math.sin(decl)
                + math.cos(lat_r) * math.cos(decl) * math.cos(H))
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_elev))))


def _lerp(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _dimmen(rgb):
    if HELLIGKEIT >= 1.0:
        return rgb
    return tuple(round(c * HELLIGKEIT) for c in rgb)


def elevation_to_color(elev):
    """Höhenwinkel der Sonne -> (r, g, b) im normalen RGB-Raum."""
    if elev >= ELEV_TAG:
        color = FARBE_TAG
    elif elev >= 0:
        # Tag -> Orange, je tiefer die Sonne desto oranger
        color = _lerp(FARBE_TAG, FARBE_ORANGE, (ELEV_TAG - elev) / ELEV_TAG)
    elif elev >= ELEV_NACHT:
        # Orange -> Nacht
        color = _lerp(FARBE_ORANGE, FARBE_NACHT, elev / ELEV_NACHT)
    else:
        color = FARBE_NACHT
    return _dimmen(color)


def _lade_mapping():
    try:
        with open(JSON_MAPPING_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def build_frame(led_count, when=None, fallback=True):
    """Liefert {led_index: (r, g, b)} für den aktuellen Sonnenstand.

    Nutzt led_mapping.json (LED -> Stadt). Ist das Mapping noch komplett
    leer und `fallback=True`, werden die Städte der Reihe nach auf
    LED 0..N gelegt, damit man den Effekt auch ohne fertiges Mapping
    sofort testen kann.
    """
    data = _lade_mapping()

    mapping = {}
    for i in range(led_count):
        city = data.get(str(i))
        if city:
            mapping[i] = city

    if not mapping and fallback:
        staedte = data.get("_verfuegbare_staedte", [])
        for i in range(min(led_count, len(staedte))):
            mapping[i] = staedte[i]

    frame = {}
    for led, city in mapping.items():
        coords = CITY_COORDS.get(city)
        if not coords:
            continue
        elev = sonnen_elevation(coords[0], coords[1], when)
        frame[led] = elevation_to_color(elev)
    return frame


if __name__ == "__main__":
    # Kleiner Selbsttest: aktuellen Sonnenstand einiger Städte anzeigen.
    jetzt = datetime.now(timezone.utc)
    print("Sonnenstand (UTC %s):" % jetzt.strftime("%H:%M"))
    for stadt in ("Berlin", "New York", "Sydney", "Tokio", "Anchorage", "Nuuk"):
        coords = CITY_COORDS.get(stadt)
        if not coords:
            continue
        e = sonnen_elevation(coords[0], coords[1], jetzt)
        print("  %-16s elev=%6.1f°  -> %s" % (stadt, e, elevation_to_color(e)))
