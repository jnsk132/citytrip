import uasyncio as asyncio
import network
import ujson
import machine
import neopixel
import math
import random
import sys
import time
import uselect

try:
    from wifi_config import SSID, PASSWORD
except ImportError:
    SSID = "iPhone von Jonas"
    PASSWORD = "12345678"

# ── LED-Konfiguration ────────────────────────────────────────────
GPIO_PIN = 5
ANZ_LEDS = 150  # LEDs für die Ranking-Animation (Fallback ohne Mapping)
MAX_LEDS = 150  # physische Streifenlänge (LED 0..149)

# Ziel-Framerate der Wetter-Idle-Animation. Höher = flüssiger, aber mehr
# CPU-Last auf dem C3. Schafft der ESP die Rechnung pro Frame nicht in der
# Zeit, läuft die Animation einfach etwas langsamer, aber immer gleichmäßig
# (nie ruckelig). 20-30 sind ein guter Bereich.
WETTER_FPS = 60

# ── Animation-Defaults (werden via /start überschrieben) ────────
_state = {
    "start_rgb": [255, 255, 50],
    "mid_rgb":   [255, 130, 0],
    "end_rgb":   [255, 0,   0],
    "animation": True,
    "led_order": None,
}

np = neopixel.NeoPixel(machine.Pin(GPIO_PIN, machine.Pin.OUT), MAX_LEDS)
_anim_task = None
_wetter_task = None           # laufende Wetter-Animation (oder None)


def _grad(s, m, e, t):
    if t <= 0.5:
        f = t / 0.5 if t > 0 else 0.0
        a, b = s, m
    else:
        f = (t - 0.5) / 0.5
        a, b = m, e
    return (a[0] + (b[0] - a[0]) * f,
            a[1] + (b[1] - a[1]) * f,
            a[2] + (b[2] - a[2]) * f)


def alle_aus():
    for i in range(MAX_LEDS):
        np[i] = (0, 0, 0)
    np.write()


# ── Ranking-Animation (async, cancellable) ───────────────────────
async def run_animation():
    START_PAUSE   = 0.05
    END_PAUSE     = 0.005
    BASE_STOTTER  = 0.30
    STOTTER_DAUER = 0.40
    FADE_SCHRITTE = 20
    HELL_MAX      = 0.5
    HELL_MIN      = 0.20
    TOP_N         = 3
    PULS_PERIODE  = 1.6
    PULS_MIN      = 0.15
    PULS_MAX      = 1.0
    PULS_FPS      = 50

    s = _state["start_rgb"]
    m = _state["mid_rgb"]
    e = _state["end_rgb"]
    animation = _state["animation"]

    leds = _state.get("led_order")
    if not leds:
        leds = list(range(ANZ_LEDS))
    n = len(leds)

    def _fortschritt(pos):
        return pos / (n - 1) if n > 1 else 0.0

    try:
        alle_aus()

        for pos, led in enumerate(leds):
            if not (0 <= led < MAX_LEDS):
                continue
            fortschritt = _fortschritt(pos)
            base_pause  = START_PAUSE - (fortschritt * (START_PAUSE - END_PAUSE))

            abfaktor = (n - 1 - pos) / (n - 1) if n > 1 else 0.0
            if random.random() < (BASE_STOTTER * abfaktor):
                base_pause += STOTTER_DAUER * abfaktor * random.uniform(0.5, 1.5)

            r, g, b = _grad(s, m, e, fortschritt)
            ziel_hell = HELL_MAX * ((HELL_MIN / HELL_MAX) ** fortschritt)

            if not animation:
                np[led] = (int(g * ziel_hell), int(r * ziel_hell), int(b * ziel_hell))
                np.write()
                await asyncio.sleep(0)
                continue

            mikro_pause = base_pause / FADE_SCHRITTE
            for schritt in range(1, FADE_SCHRITTE + 1):
                f = (schritt / FADE_SCHRITTE) * ziel_hell
                np[led] = (int(g * f), int(r * f), int(b * f))
                np.write()
                await asyncio.sleep(mikro_pause)

        top_farben = []
        for pos in range(min(TOP_N, n)):
            led = leds[pos]
            if not (0 <= led < MAX_LEDS):
                continue
            fortschritt = _fortschritt(pos)
            tr, tg, tb = _grad(s, m, e, fortschritt)
            top_farben.append((led, tr, tg, tb))

        dt = 1.0 / PULS_FPS
        t  = 0.0
        while True:
            phase  = 0.5 + 0.5 * math.cos(2 * math.pi * t / PULS_PERIODE)
            faktor = PULS_MIN + (PULS_MAX - PULS_MIN) * phase
            for led, r, g, b in top_farben:
                np[led] = (int(g * faktor), int(r * faktor), int(b * faktor))
            np.write()
            await asyncio.sleep(dt)
            t = (t + dt) % PULS_PERIODE

    except asyncio.CancelledError:
        raise


async def run_fadeout():
    """Blendet alle aktiven LEDs mit zufälligen Geschwindigkeiten schnell aus."""
    TOTAL_STEPS = 25
    DELAY = 0.025  # 25 * 0.025 = ~0.6 s gesamt

    # Zustand nach dem Cancel lesen (run_animation ruft kein alle_aus() mehr)
    base = [(np[i][0], np[i][1], np[i][2]) for i in range(MAX_LEDS)]
    # Jede LED startet das Ausblenden zu einem zufälligen Zeitpunkt
    offsets = [random.randint(0, TOTAL_STEPS // 2) for _ in range(MAX_LEDS)]

    try:
        for step in range(TOTAL_STEPS + 1):
            for i in range(MAX_LEDS):
                r, g, b = base[i]
                if r == 0 and g == 0 and b == 0:
                    continue
                s = offsets[i]
                if step < s:
                    continue
                remaining = TOTAL_STEPS - s
                progress = (step - s) / remaining if remaining > 0 else 1.0
                factor = max(0.0, 1.0 - progress)
                np[i] = (int(r * factor), int(g * factor), int(b * factor))
            np.write()
            await asyncio.sleep(DELAY)
        alle_aus()
    except asyncio.CancelledError:
        alle_aus()
        raise


async def cancel_anim():
    global _anim_task, _wetter_task
    if _wetter_task is not None:
        _wetter_task.cancel()
        try:
            await _wetter_task
        except asyncio.CancelledError:
            pass
        _wetter_task = None
    if _anim_task is not None:
        _anim_task.cancel()
        try:
            await _anim_task
        except asyncio.CancelledError:
            pass
        _anim_task = None


# ── Wetter-Idle-Animation (läuft komplett auf dem ESP) ────────────
# Der Laptop schickt nur die Wetterkategorie je LED (alle paar Minuten);
# die zeitabhängige Animation rechnet der ESP selbst mit eigener Uhr und
# gleichmäßiger Framerate – dadurch flüssig, ohne Serial im Hot-Loop.
#
# Logik gespiegelt aus laptop_app/wetter_idle.py. Kategorie-Codes:
#   0=wolken/unbekannt  1=klar  2=regen  3=schnee  4=gewitter  5=nacht  9=aus
# Code 5 (Nacht) setzt der Laptop dort, wo die Sonne unter dem Horizont steht
# UND es klar oder bewölkt wäre – Regen/Schnee/Gewitter zeigt er auch nachts.
W_WOLKEN, W_KLAR, W_REGEN, W_SCHNEE, W_GEWITTER, W_NACHT, W_AUS = 0, 1, 2, 3, 4, 5, 9

# Farben in normalem RGB; der GRB-Tausch passiert erst beim Schreiben.
WFARBE_REGEN  = (0,   0,   255)
WFARBE_BLITZ  = (255, 240, 0)
WFARBE_SCHNEE = (200, 220, 255)
WFARBE_SONNE  = (255, 100, 0)
WFARBE_WOLKEN = (40,  46,  64)
WFARBE_NACHT  = (18,  4,   46)     # sehr dunkles Blau-Lila

_TWO_PI = 2 * math.pi
_wetter_codes = []            # Kategorie-Code je LED (Index = LED-Nr.)

_w_blitz = {"ende": 0.0, "naechster": 0.0, "staerke": 0.0}


def _w_scale(rgb, f):
    if f < 0.0:
        f = 0.0
    elif f > 1.0:
        f = 1.0
    return (int(rgb[0] * f), int(rgb[1] * f), int(rgb[2] * f))


def _w_lerp(c1, c2, t):
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


def _w_jitter(led, salt=0):
    """Deterministischer Pseudo-Zufall in [0,1) pro LED – jede LED bekommt
    so ihre eigene Phase, statt synchron zu pulsieren."""
    x = math.sin((led + 1) * 12.9898 + salt * 78.233) * 43758.5453
    return x - math.floor(x)


# Die Jitter-Werte hängen nur von LED+Salt ab und ändern sich nie –
# einmal vorrechnen spart pro Frame hunderte sin()-Aufrufe auf dem ESP.
_JIT0 = [_w_jitter(i, 0) for i in range(MAX_LEDS)]
_JIT1 = [_w_jitter(i, 1) for i in range(MAX_LEDS)]
_JIT2 = [_w_jitter(i, 2) for i in range(MAX_LEDS)]
_JIT3 = [_w_jitter(i, 3) for i in range(MAX_LEDS)]
_JIT4 = [_w_jitter(i, 4) for i in range(MAX_LEDS)]
_W_PERIODE_SCHNEE = [2.0 + 2.5 * _JIT2[i] for i in range(MAX_LEDS)]


def _w_blitz_level(t):
    """Globaler Blitz-Pegel 0..1 für Gewitter-LEDs: meist 0, in zufälligen
    Abständen ein kurzer harter Ausschlag mit leichtem Flackern."""
    if t >= _w_blitz["naechster"]:
        _w_blitz["ende"]      = t + random.uniform(0.08, 0.20)
        _w_blitz["naechster"] = t + random.uniform(1.5, 5.0)
        _w_blitz["staerke"]   = random.uniform(0.7, 1.0)
    if t < _w_blitz["ende"]:
        return _w_blitz["staerke"] * random.uniform(0.6, 1.0)
    return 0.0


def _w_farbe(led, code, t, blitz):
    if code == W_REGEN:
        phase = 0.5 + 0.5 * math.sin(_TWO_PI * (t / 1.2 + _JIT0[led]))
        return _w_scale(WFARBE_REGEN, 0.35 + 0.65 * phase)

    if code == W_GEWITTER:
        phase = 0.5 + 0.5 * math.sin(_TWO_PI * (t / 1.0 + _JIT0[led]))
        rgb = _w_scale(WFARBE_REGEN, 0.25 + 0.45 * phase)
        if blitz > 0:
            rgb = _w_lerp(rgb, WFARBE_BLITZ, blitz)
        return rgb

    if code == W_SCHNEE:
        periode = _W_PERIODE_SCHNEE[led]
        phase = 0.5 + 0.5 * math.sin(_TWO_PI * (t / periode + _JIT1[led]))
        return _w_scale(WFARBE_SCHNEE, 0.12 + 0.88 * (phase * phase))

    if code == W_KLAR:
        phase = 0.5 + 0.5 * math.sin(_TWO_PI * (t / 4.0))
        return _w_scale(WFARBE_SONNE, 0.55 + 0.45 * phase)

    if code == W_NACHT:
        # Sehr ruhiges, langsames Atmen im dunklen Blau-Lila
        phase = 0.5 + 0.5 * math.sin(_TWO_PI * (t / 8.0 + _JIT4[led]))
        return _w_scale(WFARBE_NACHT, 0.6 + 0.4 * phase)

    # WOLKEN / unbekannt: schwaches Schimmern
    phase = 0.5 + 0.5 * math.sin(_TWO_PI * (t / 6.0 + _JIT3[led]))
    return _w_scale(WFARBE_WOLKEN, 0.55 + 0.45 * phase)


async def run_wetter():
    """Animiert die Wetter-Weltkarte fortlaufend aus _wetter_codes."""
    frame_ms = max(1, int(1000 / WETTER_FPS))
    try:
        t0 = time.ticks_ms()
        while True:
            t = time.ticks_diff(time.ticks_ms(), t0) / 1000.0
            blitz = _w_blitz_level(t)
            codes = _wetter_codes
            n = len(codes)
            for i in range(MAX_LEDS):
                code = codes[i] if i < n else W_AUS
                if code == W_AUS:
                    np[i] = (0, 0, 0)
                else:
                    r, g, b = _w_farbe(i, code, t, blitz)
                    np[i] = (g, r, b)        # GRB-Reihenfolge des Streifens
            np.write()
            # Takt aus WETTER_FPS; bei langsamerer Rechnung läuft die
            # Animation eben gemächlicher, aber gleichmäßig – nie ruckelig.
            await asyncio.sleep_ms(frame_ms)
    except asyncio.CancelledError:
        alle_aus()
        raise


# ── Gemeinsame Befehls-Logik (HTTP + Serial) ─────────────────────
async def _handle_command(path, incoming):
    """Führt einen Befehl aus und gibt das Antwort-Dict zurück."""
    global _anim_task, _wetter_task, _wetter_codes

    if path == "/ping":
        return {"status": "ok", "pong": True}

    elif path == "/start":
        _state["start_rgb"] = incoming.get("start_rgb", _state["start_rgb"])
        _state["mid_rgb"]   = incoming.get("mid_rgb",   _state["mid_rgb"])
        _state["end_rgb"]   = incoming.get("end_rgb",   _state["end_rgb"])
        _state["animation"] = incoming.get("animation", _state["animation"])
        _state["led_order"] = incoming.get("leds")
        await cancel_anim()
        _anim_task = asyncio.create_task(run_animation())
        return {"status": "ok", "action": "start"}

    elif path == "/frame":
        await cancel_anim()
        pixels = incoming.get("pixels", [])
        for i in range(MAX_LEDS):
            if i < len(pixels):
                c = pixels[i]
                np[i] = (int(c[1]), int(c[0]), int(c[2]))
            else:
                np[i] = (0, 0, 0)
        np.write()
        return {"status": "ok", "action": "frame"}

    elif path == "/wetter":
        # Wetter-Idle: nur die Kategorien je LED kommen vom Laptop, der ESP
        # animiert selbst. Läuft die Animation schon, werden bloß die
        # Kategorien aktualisiert – so läuft die Zeit-Phase nahtlos weiter.
        codes = incoming.get("codes") or []
        _wetter_codes = codes
        if _wetter_task is None:
            await cancel_anim()
            _wetter_task = asyncio.create_task(run_wetter())
        return {"status": "ok", "action": "wetter"}

    elif path == "/stop":
        await cancel_anim()
        alle_aus()
        return {"status": "ok", "action": "stop"}

    elif path == "/fadeout":
        await cancel_anim()
        _anim_task = asyncio.create_task(run_fadeout())
        return {"status": "ok", "action": "fadeout"}

    elif path.startswith("/led/"):
        try:
            idx = int(path.split("/")[-1])
        except ValueError:
            idx = 0
        await cancel_anim()
        rgb = incoming.get("rgb", [255, 255, 255])
        alle_aus()
        if 0 <= idx < MAX_LEDS:
            np[idx] = (int(rgb[1]), int(rgb[0]), int(rgb[2]))
        np.write()
        return {"status": "ok"}

    elif path == "/status":
        ip = "0.0.0.0"
        try:
            wlan = network.WLAN(network.STA_IF)
            if wlan.isconnected():
                ip = wlan.ifconfig()[0]
        except Exception:
            pass
        return {
            "status": "ok",
            "ip": ip,
            "anim_running": _anim_task is not None,
        }

    else:
        return {"error": "not found"}


# ── HTTP-Handler ─────────────────────────────────────────────────
async def handle_client(reader, writer):
    try:
        line = await reader.readline()
        if not line:
            return
        parts = line.decode().strip().split()
        if len(parts) < 2:
            return
        path = parts[1]

        content_length = 0
        while True:
            h = await reader.readline()
            if not h or h == b"\r\n":
                break
            if h.lower().startswith(b"content-length:"):
                try:
                    content_length = int(h.split(b":")[1].strip())
                except Exception:
                    pass

        body = b""
        if content_length > 0:
            body = await reader.read(content_length)

        incoming = {}
        if body:
            try:
                incoming = ujson.loads(body)
            except Exception:
                pass

        result = await _handle_command(path, incoming)
        resp = ujson.dumps(result).encode()

        header = (
            b"HTTP/1.0 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(resp)).encode() + b"\r\n"
            b"\r\n"
        )
        writer.write(header + resp)
        await writer.drain()

    except Exception as ex:
        print("Handler-Fehler:", ex)
    finally:
        writer.close()


# ── Serial-Listener (USB-Kabel-Modus) ───────────────────────────
# Liest eine JSON-Zeile pro Befehl vom Laptop und antwortet mit JSON.
# Läuft immer – auch wenn WLAN verbunden ist. Der Laptop entscheidet,
# welchen Kanal er nutzt.
# Debug-Prints des ESP32 erscheinen ebenfalls auf der seriellen Leitung;
# der Laptop ignoriert Zeilen, die kein gültiges JSON sind.
async def serial_listener():
    poll = uselect.poll()
    poll.register(sys.stdin, uselect.POLLIN)
    buf = ""
    while True:
        if poll.poll(0):
            # Alle gerade verfügbaren Zeichen am Stück abholen, statt nur
            # ein Zeichen pro 10-ms-Tick. Sonst läuft das Einlesen eines
            # großen Befehls (z. B. /frame mit 150 Pixeln) in den Bereich
            # von Sekunden und der Laptop läuft ins Timeout.
            while poll.poll(0):
                try:
                    char = sys.stdin.read(1)
                except Exception:
                    break
                if char in ("\n", "\r"):
                    line = buf.strip()
                    buf = ""
                    if not line:
                        continue
                    try:
                        incoming = ujson.loads(line)
                        cmd  = incoming.get("cmd", "")
                        data = incoming.get("data") or {}
                        resp = await _handle_command(cmd, data)
                        # Antwort mit eindeutigem Präfix, damit der Laptop
                        # sie von Debug-Ausgaben unterscheiden kann.
                        sys.stdout.write(">>>" + ujson.dumps(resp) + "\n")
                    except Exception as ex:
                        sys.stdout.write('>>>{"error":"parse_error"}\n')
                else:
                    buf += char
        await asyncio.sleep(0.005)


# ── WLAN-Verbindung ───────────────────────────────────────────────
_WLAN_STATUS = {
    getattr(network, "STAT_IDLE", 1000):           "IDLE (nichts passiert)",
    getattr(network, "STAT_CONNECTING", 1001):     "CONNECTING (verbinde...)",
    getattr(network, "STAT_WRONG_PASSWORD", 202):  "FALSCHES PASSWORT",
    getattr(network, "STAT_NO_AP_FOUND", 201):     "WLAN NICHT GEFUNDEN (SSID falsch / nicht 2,4 GHz / außer Reichweite)",
    getattr(network, "STAT_GOT_IP", 1010):         "VERBUNDEN (IP erhalten)",
}


async def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    print("=" * 50)
    print("Geladene WLAN-Config:")
    print("  SSID     = '%s'" % SSID)
    print("  PASSWORT = '%s'  (Länge %d)" % (PASSWORD, len(PASSWORD)))
    print("=" * 50)

    try:
        print("Sichtbare 2,4-GHz-Netze:")
        gefunden = False
        for netz in wlan.scan():
            ssid = netz[0].decode("utf-8", "replace")
            rssi = netz[3]
            print("  - '%s'  (Signal %d dBm)" % (ssid, rssi))
            if ssid == SSID:
                gefunden = True
        if gefunden:
            print(">> '%s' ist sichtbar." % SSID)
        else:
            print(">> ACHTUNG: '%s' NICHT in der Liste!" % SSID)
    except Exception as ex:
        print("Scan fehlgeschlagen:", ex)

    try:
        wlan.disconnect()
    except Exception:
        pass
    await asyncio.sleep(0.5)

    print("Verbinde mit '%s' ..." % SSID)
    wlan.connect(SSID, PASSWORD)
    for _ in range(40):
        if wlan.isconnected():
            break
        await asyncio.sleep(0.5)

    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print("WLAN verbunden – IP:", ip)
        if ip.startswith("172.20.10."):
            print(">> iPhone-Hotspot erkannt (172.20.10.x) – passt.")
        return ip

    status = wlan.status()
    print("WLAN-Verbindung fehlgeschlagen! Status:",
          _WLAN_STATUS.get(status, "Code %s" % status))
    return None


async def main():
    # Serial-Listener immer starten – unabhängig vom WLAN-Status.
    asyncio.create_task(serial_listener())
    print("Serial-Listener aktiv (USB-Kabel-Modus verfügbar).")

    ip = await connect_wifi()
    if ip:
        server = await asyncio.start_server(handle_client, "0.0.0.0", 80)
        print("HTTP-Server: http://" + ip + "/")
        await server.wait_closed()
    else:
        print("Kein WLAN – nur Serial-Modus aktiv. Warte auf Befehle …")
        while True:
            await asyncio.sleep(1)


asyncio.run(main())
