import uasyncio as asyncio
import network
import ujson
import machine
import neopixel
import math
import random

try:
    from wifi_config import SSID, PASSWORD
except ImportError:
    SSID = "iPhone von Jonas"
    PASSWORD = "12345678"

# ── LED-Konfiguration ────────────────────────────────────────────
GPIO_PIN = 4
ANZ_LEDS = 50   # LEDs für die Ranking-Animation
MAX_LEDS = 103  # max. Streifenlänge für LED-Mapping-Test

# ── Animation-Defaults (werden via /start überschrieben) ────────
# led_order = None  -> generische Demo (LED 0..ANZ_LEDS-1 der Reihe nach).
# led_order = [..]  -> echte Karten-Positionen in Rang-Reihenfolge (bestes zuerst).
_state = {
    "start_rgb": [255, 255, 50],
    "mid_rgb":   [255, 130, 0],
    "end_rgb":   [255, 0,   0],
    "animation": True,
    "led_order": None,
}

np = neopixel.NeoPixel(machine.Pin(GPIO_PIN, machine.Pin.OUT), MAX_LEDS)
_anim_task = None


def _grad(s, m, e, t):
    """Farbe an Position t (0..1) auf dem 3-Farben-Verlauf start->mid->end.

    Erste Hälfte (t<=0.5) blendet start->mid, zweite Hälfte mid->end.
    Liefert (r, g, b) in normalem RGB.
    """
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
    START_PAUSE   = 0.10
    END_PAUSE     = 0.0125
    BASE_STOTTER  = 0.60
    STOTTER_DAUER = 0.40
    FADE_SCHRITTE = 20
    HELL_MAX      = 1.0
    HELL_MIN      = 0.30
    TOP_N         = 3
    PULS_PERIODE  = 1.6
    PULS_MIN      = 0.15
    PULS_MAX      = 1.0
    PULS_FPS      = 50

    s = _state["start_rgb"]
    m = _state["mid_rgb"]
    e = _state["end_rgb"]
    animation = _state["animation"]

    # LED-Reihenfolge: echte Karten-Positionen (Rang-Reihenfolge) oder Demo 0..N-1.
    leds = _state.get("led_order")
    if not leds:
        leds = list(range(ANZ_LEDS))
    n = len(leds)

    def _fortschritt(pos):
        # 0.0 = bestes (Platz 1), 1.0 = schlechtestes. Bei nur 1 LED -> 0.
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
                await asyncio.sleep(0)  # yield, damit der Server weiter laufen kann
                continue

            mikro_pause = base_pause / FADE_SCHRITTE
            for schritt in range(1, FADE_SCHRITTE + 1):
                f = (schritt / FADE_SCHRITTE) * ziel_hell
                np[led] = (int(g * f), int(r * f), int(b * f))
                np.write()
                await asyncio.sleep(mikro_pause)

        # Top-N pulsieren (die ersten N LEDs der Rang-Reihenfolge)
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
        alle_aus()
        raise


async def cancel_anim():
    """Bricht eine laufende Animation ab und wartet bis sie beendet ist."""
    global _anim_task
    if _anim_task is not None:
        _anim_task.cancel()
        try:
            await _anim_task
        except asyncio.CancelledError:
            pass
        _anim_task = None


# ── HTTP-Handler ─────────────────────────────────────────────────
async def handle_client(reader, writer):
    global _anim_task
    try:
        # Request-Zeile lesen
        line = await reader.readline()
        if not line:
            return
        parts = line.decode().strip().split()
        if len(parts) < 2:
            return
        path = parts[1]

        # Headers lesen (Content-Length merken, Rest verwerfen)
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

        # Body lesen
        body = b""
        if content_length > 0:
            body = await reader.read(content_length)

        # Routing
        if path == "/start":
            incoming = {}
            if body:
                try:
                    incoming = ujson.loads(body)
                except Exception:
                    incoming = {}
            _state["start_rgb"] = incoming.get("start_rgb", _state["start_rgb"])
            _state["mid_rgb"]   = incoming.get("mid_rgb",   _state["mid_rgb"])
            _state["end_rgb"]   = incoming.get("end_rgb",   _state["end_rgb"])
            _state["animation"] = incoming.get("animation", _state["animation"])
            # leds NUR setzen, wenn mitgeschickt – sonst zurück auf Demo (None),
            # damit ein manueller Neustart nicht das alte Ranking wiederholt.
            _state["led_order"] = incoming.get("leds")
            await cancel_anim()
            _anim_task = asyncio.create_task(run_animation())
            resp = b'{"status":"ok","action":"start"}'

        elif path == "/frame":
            # Statischer Frame: feste Farbe je LED (für Idle-/Wetter-Modi).
            # Body: {"pixels": [[r,g,b], [r,g,b], ...]} – Index = LED-Nummer.
            # Der Laptop rechnet, der ESP32 zeigt nur an.
            await cancel_anim()
            incoming = {}
            if body:
                try:
                    incoming = ujson.loads(body)
                except Exception:
                    incoming = {}
            pixels = incoming.get("pixels", [])
            for i in range(MAX_LEDS):
                if i < len(pixels):
                    c = pixels[i]
                    # Eingang ist normales RGB -> Streifen erwartet GRB.
                    np[i] = (int(c[1]), int(c[0]), int(c[2]))
                else:
                    np[i] = (0, 0, 0)
            np.write()
            resp = b'{"status":"ok","action":"frame"}'

        elif path == "/stop":
            # Erst eine evtl. laufende Animation abbrechen, dann IMMER die Kette
            # löschen. Wichtig: Im Idle-/Mapping-Modus läuft KEIN _anim_task
            # (dort werden nur statische Frames/Einzel-LEDs geschrieben), daher
            # würde cancel_anim() allein die LEDs nicht ausschalten.
            await cancel_anim()
            alle_aus()
            resp = b'{"status":"ok","action":"stop"}'

        elif path.startswith("/led/"):
            try:
                idx = int(path.split("/")[-1])
            except ValueError:
                idx = 0
            await cancel_anim()
            n = min(max(idx + 1, ANZ_LEDS), MAX_LEDS)
            for i in range(n):
                np[i] = (0, 0, 0)
            if 0 <= idx < MAX_LEDS:
                np[idx] = (255, 255, 255)
            np.write()
            resp = b'{"status":"ok"}'

        elif path == "/status":
            ip = network.WLAN(network.STA_IF).ifconfig()[0]
            resp = ujson.dumps({
                "status": "ok",
                "ip": ip,
                "anim_running": _anim_task is not None,
            }).encode()

        else:
            resp = b'{"error":"not found"}'

        # Antwort senden
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


# ── WLAN-Verbindung ───────────────────────────────────────────────
# Klartext-Namen der WLAN-Status-Codes (für verständliche Fehlermeldungen).
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

    # Lautes Banner: zeigt EXAKT, welche Zugangsdaten geladen wurden.
    # Steht hier die ALTE SSID, wurde wifi_config.py nicht auf den ESP32 gespeichert!
    print("=" * 50)
    print("Geladene WLAN-Config:")
    print("  SSID     = '%s'" % SSID)
    print("  PASSWORT = '%s'  (Länge %d)" % (PASSWORD, len(PASSWORD)))
    print("=" * 50)

    # Sichtbare Netze scannen – ist der iPhone-Hotspot überhaupt da (2,4 GHz)?
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
            print(">> ACHTUNG: '%s' NICHT in der Liste! "
                  "iPhone-Hotspot? -> 'Kompatibilität maximieren' EIN (2,4 GHz)." % SSID)
    except Exception as ex:
        print("Scan fehlgeschlagen:", ex)

    # WICHTIG: MicroPython reconnectet beim Boot automatisch ins zuletzt
    # gespeicherte WLAN. Diese alte Verbindung erst trennen, sonst bleibt der
    # ESP32 dort hängen und ignoriert die neue Config (IP ändert sich nie).
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
    ip = await connect_wifi()
    if not ip:
        return
    server = await asyncio.start_server(handle_client, "0.0.0.0", 80)
    print("HTTP-Server: http://" + ip + "/")
    await server.wait_closed()


asyncio.run(main())
