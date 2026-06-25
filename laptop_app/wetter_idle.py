# -*- coding: utf-8 -*-
"""Wetter-Idle-Modus: aktuelles Wetter je Stadt -> animierte LED-Farbe.

Reine Logik + Wetterabruf, keine Hardware. qrscanner.py baut daraus die
Frames (Liste von RGB-Werten) und schickt sie per /frame an den ESP32.

Anders als Tag/Nacht ist der Effekt zeitabhaengig animiert:
  * Regen     -> blaue LEDs pulsieren
  * Gewitter  -> blaue Basis + zufaellige gelb-weisse Blitze
  * Schnee    -> weisse LEDs glitzern langsam auf und ab (wie fallende Flocken)
  * Sonne     -> warmgelbes, ruhiges Atmen
  * sonst     -> gedimmtes Grau (Wolken/Nebel/unbekannt)

Die Wetterdaten aendern sich langsam und werden nur alle paar Minuten neu
abgerufen (qrscanner.py steuert den Takt). Die Animation laeuft davon
unabhaengig mit hoher Frequenz: build_frame(...) bekommt die fertigen
Kategorien je LED plus die laufende Zeit t und rechnet daraus den Effekt.

Datenquelle: Open-Meteo (https://open-meteo.com) – kostenlos, ohne API-Key.
"""

import json
import math
import os
import random
import ssl
import urllib.request

from city_coords import CITY_COORDS

# SSL-Context mit gueltigem CA-Bundle. Auf macOS bringt das Python.org-Build
# oft keine System-Zertifikate mit ("CERTIFICATE_VERIFY_FAILED") – certifi
# liefert sie nach. Faellt certifi weg, nutzen wir den Standard-Context.
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CONTEXT = ssl.create_default_context()

JSON_MAPPING_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "led_mapping.json"
)

_NULL_SENTINEL = "NULL"   # LED ist physisch nicht sichtbar (wie im Mapping)

# ── Wetter-Kategorien ────────────────────────────────────────────
KLAR     = "klar"
WOLKEN   = "wolken"
REGEN    = "regen"
SCHNEE   = "schnee"
GEWITTER = "gewitter"

# ── Farben (normales RGB; der GRB-Tausch passiert erst am ESP32) ──
FARBE_REGEN    = (0,  0,  255)    # kraeftiges Blau
FARBE_BLITZ    = (255, 240, 180)    # gelb-weisser Blitz
FARBE_SCHNEE   = (200, 220, 255)    # kuehles Weiss
FARBE_SONNE    = (255, 100, 0)     # warmes Gelb
FARBE_WOLKEN   = (40,  46,  64)     # gedimmtes Blaugrau

_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# Letzter gueltiger Stand (LED -> Kategorie). Wird bei Netzfehlern als
# Rueckfall genutzt, damit die Karte nicht ausgeht.
_cache = {"kategorien": {}}

# Zustand der Blitz-Animation (Gewitter). Globaler Blitz ueber die
# Gewitter-LEDs, in zufaelligen Abstaenden, mit kurzem Flackern.
_blitz = {"ende": 0.0, "naechster": 0.0, "staerke": 0.0}


# ════════════════════════════════════════════════════════════════
#  WMO-Wettercode -> Kategorie
#  Codes laut Open-Meteo (https://open-meteo.com/en/docs)
# ════════════════════════════════════════════════════════════════
def _wmo_kategorie(code):
    if code in (0, 1):            # klar / ueberwiegend klar
        return KLAR
    if 95 <= code <= 99:          # Gewitter
        return GEWITTER
    if 71 <= code <= 77 or 85 <= code <= 86:   # Schnee(-schauer)
        return SCHNEE
    if (51 <= code <= 67) or (80 <= code <= 82):  # Niesel/Regen(-schauer)
        return REGEN
    return WOLKEN                 # 2,3 bewoelkt · 45,48 Nebel · Rest


# ════════════════════════════════════════════════════════════════
#  Mapping (LED -> Stadt) laden
# ════════════════════════════════════════════════════════════════
def _lade_mapping():
    try:
        with open(JSON_MAPPING_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _led_city_map(led_count, fallback=True):
    """{led_index: stadt} fuer alle sichtbar gemappten LEDs.

    Wie in tag_nacht: ist noch nichts gemappt und `fallback=True`, werden
    die verfuegbaren Staedte der Reihe nach auf LED 0..N gelegt, damit der
    Effekt auch ohne fertiges Mapping sofort testbar ist.
    """
    data = _lade_mapping()
    out = {}
    for i in range(led_count):
        city = data.get(str(i))
        if city and city != _NULL_SENTINEL:
            out[i] = city

    if not out and fallback:
        staedte = data.get("_verfuegbare_staedte", [])
        for i in range(min(led_count, len(staedte))):
            out[i] = staedte[i]
    return out


# ════════════════════════════════════════════════════════════════
#  Wetterabruf (Open-Meteo, alle Staedte in einem Request)
# ════════════════════════════════════════════════════════════════
def _fetch_wetter(coords):
    """coords: {stadt: (lat, lon)} -> {stadt: kategorie} oder None bei Fehler."""
    if not coords:
        return {}
    staedte = list(coords.keys())
    lats = ",".join("%.4f" % coords[c][0] for c in staedte)
    lons = ",".join("%.4f" % coords[c][1] for c in staedte)
    url = ("%s?latitude=%s&longitude=%s&current=weather_code&timezone=UTC"
           % (_OPEN_METEO, lats, lons))
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "CityTrip-LED/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CONTEXT) as resp:
            data = json.loads(resp.read().decode())
    except Exception as ex:
        print("[Wetter] Abruf fehlgeschlagen:", ex)
        return None

    # Ein Ort -> Dict, mehrere Orte -> Liste (in Eingabereihenfolge).
    if isinstance(data, dict):
        data = [data]

    out = {}
    for city, eintrag in zip(staedte, data):
        try:
            code = int(eintrag["current"]["weather_code"])
            out[city] = _wmo_kategorie(code)
        except (KeyError, TypeError, ValueError):
            out[city] = WOLKEN
    return out


def kategorien_by_led(led_count):
    """Ruft das Wetter ab und liefert {led_index: kategorie}.

    Bei Netzfehlern wird der letzte gute Stand beibehalten (oder, falls es
    noch keinen gibt, alles als 'wolken' dargestellt). Der Aufruf-Takt
    (z. B. alle 10 Minuten) wird vom Aufrufer bestimmt.
    """
    led_city = _led_city_map(led_count)
    if not led_city:
        return {}

    coords = {}
    for city in set(led_city.values()):
        c = CITY_COORDS.get(city)
        if c:
            coords[city] = c

    wetter = _fetch_wetter(coords)
    if wetter is None:                       # Netzfehler
        if _cache["kategorien"]:
            return _cache["kategorien"]      # alten Stand behalten
        wetter = {city: WOLKEN for city in coords}

    by_led = {led: wetter.get(city, WOLKEN) for led, city in led_city.items()}
    _cache["kategorien"] = by_led
    return by_led


# ════════════════════════════════════════════════════════════════
#  Animation
# ════════════════════════════════════════════════════════════════
def _scale(rgb, f):
    f = max(0.0, min(1.0, f))
    return (int(rgb[0] * f), int(rgb[1] * f), int(rgb[2] * f))


def _lerp(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


def _jitter(led, salt=0):
    """Deterministischer Pseudo-Zufall in [0,1) pro LED – damit die LEDs
    nicht synchron pulsieren, sondern jede ihre eigene Phase hat."""
    x = math.sin((led + 1) * 12.9898 + salt * 78.233) * 43758.5453
    return x - math.floor(x)


def _blitz_level(t):
    """Globaler Blitz-Pegel 0..1 fuer Gewitter-LEDs. Meist 0, in zufaelligen
    Abstaenden ein kurzer, harter Ausschlag mit leichtem Flackern."""
    if t >= _blitz["naechster"]:
        _blitz["ende"]      = t + random.uniform(0.08, 0.20)
        _blitz["naechster"] = t + random.uniform(1.5, 5.0)
        _blitz["staerke"]   = random.uniform(0.7, 1.0)
    if t < _blitz["ende"]:
        return _blitz["staerke"] * random.uniform(0.6, 1.0)
    return 0.0


def _farbe(led, kat, t, blitz):
    if kat == REGEN:
        # Blaues Pulsieren, Periode ~1.2 s, Helligkeit 0.35..1.0
        phase = 0.5 + 0.5 * math.sin(2 * math.pi * (t / 1.2 + _jitter(led)))
        return _scale(FARBE_REGEN, 0.35 + 0.65 * phase)

    if kat == GEWITTER:
        # Etwas dunklere blaue Basis + gemeinsamer Blitz
        phase = 0.5 + 0.5 * math.sin(2 * math.pi * (t / 1.0 + _jitter(led)))
        rgb = _scale(FARBE_REGEN, 0.25 + 0.45 * phase)
        if blitz > 0:
            rgb = _lerp(rgb, FARBE_BLITZ, blitz)
        return rgb

    if kat == SCHNEE:
        # Jede Flocke mit eigener Periode (2..4.5 s); ^2 -> kurz hell, lang dunkel
        periode = 2.0 + 2.5 * _jitter(led, 2)
        phase = 0.5 + 0.5 * math.sin(2 * math.pi * (t / periode + _jitter(led, 1)))
        return _scale(FARBE_SCHNEE, 0.12 + 0.88 * (phase ** 2))

    if kat == KLAR:
        # Ruhiges, langsames Atmen (alle synchron), Periode ~4 s
        phase = 0.5 + 0.5 * math.sin(2 * math.pi * (t / 4.0))
        return _scale(FARBE_SONNE, 0.55 + 0.45 * phase)

    # WOLKEN / unbekannt: schwaches Schimmern
    phase = 0.5 + 0.5 * math.sin(2 * math.pi * (t / 6.0 + _jitter(led, 3)))
    return _scale(FARBE_WOLKEN, 0.55 + 0.45 * phase)


def build_frame(led_count, kategorie_by_led, t):
    """Liefert {led_index: (r, g, b)} fuer den Animationszeitpunkt t (Sekunden)."""
    blitz = _blitz_level(t)
    frame = {}
    for led, kat in kategorie_by_led.items():
        if 0 <= led < led_count:
            frame[led] = _farbe(led, kat, t, blitz)
    return frame


if __name__ == "__main__":
    # Selbsttest: aktuelles Wetter der gemappten Staedte abrufen und zaehlen.
    kats = kategorien_by_led(150)
    if not kats:
        print("Kein Mapping/keine Staedte gefunden.")
    else:
        zaehl = {}
        for kat in kats.values():
            zaehl[kat] = zaehl.get(kat, 0) + 1
        print("Wetter ueber %d LEDs:" % len(kats))
        for kat, n in sorted(zaehl.items()):
            print("  %-9s %d" % (kat, n))
