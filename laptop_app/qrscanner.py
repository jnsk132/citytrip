import csv
import cv2
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from PIL import Image, ImageTk, ImageDraw
import os
import json
import random
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.parse

try:
    import serial
    import serial.tools.list_ports
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False
    print("[Serial] pyserial nicht installiert – nur WLAN-Modus. "
          "Installation: pip install pyserial")

from tag_nacht import build_frame, set_idle_farben, get_idle_farben
import wetter_idle


# ════════════════════════════════════════════════════════════════
#  ESP32-Lichterkette: Kommunikation ausschließlich über USB-Serial.
# ════════════════════════════════════════════════════════════════

SERIAL_BAUD     = 115200
SERIAL_TIMEOUT  = 5       # Sekunden Wartezeit auf Antwort
# Fester Port (z. B. "/dev/cu.usbmodem1101" oder "COM5"). None = alle Ports scannen.
SERIAL_PORT          = None
SERIAL_PROBE_WINDOW  = 8     # Sekunden pro Port: gibt dem ESP nach dem Öffnen Zeit zu booten
SERIAL_PING_INTERVAL = 0.5   # in diesem Takt wird während des Wartens neu gepingt
SCAN_COOLDOWN_SEC    = 10    # Sekunden Sperre nach einem erkannten QR-Code
FADEOUT_DAUER_SEC    = 0.7   # muss zur Fadeout-Dauer in der ESP32-Firmware passen
# Antworten des ESP32 beginnen mit diesem Präfix (trennt JSON von Debug-Prints).
_SERIAL_PREFIX  = b">>>"

_serial_conn    = None    # offene serial.Serial-Instanz oder None
_serial_lock    = threading.Lock()

# Steuerzustand der Lichterkette (wird von der UI gesetzt, an den ESP32 geschickt).
LICHT_STATE = {
    "start_rgb": (0, 255, 0),
    "mid_rgb":   (255, 130, 0),
    "end_rgb":   (255, 0,   0),
    "animation": True,
}


def _candidate_ports():
    """Liefert die Liste der zu prüfenden Ports (fester Port oder USB-Serial-Kandidaten)."""
    ports = list(serial.tools.list_ports.comports())
    if SERIAL_PORT:
        return [SERIAL_PORT]
    # Bevorzugt offensichtliche USB-Serial-Geräte; Bluetooth o. Ä. werden übersprungen.
    likely = [p.device for p in ports
              if any(k in p.device.lower() for k in ("usb", "acm", "wch", "modem"))]
    return likely or [p.device for p in ports]


def _probe_port(device):
    """Öffnet <device>, pingt wiederholt und gibt die offene Verbindung zurück,
    sobald der ESP32 mit '>>>{"pong":true}' antwortet – sonst None."""
    try:
        conn = serial.Serial()
        conn.port = device
        conn.baudrate = SERIAL_BAUD
        conn.timeout = 0.3
        # Reset über DTR/RTS unterdrücken: eine bereits laufende Firmware soll
        # nicht neu booten (und der ESP32-C3 nicht in den Bootloader fallen).
        conn.dtr = False
        conn.rts = False
        conn.open()
    except Exception:
        return None
    try:
        conn.reset_input_buffer()
        deadline = time.time() + SERIAL_PROBE_WINDOW
        next_ping = 0.0
        buf = b""
        while time.time() < deadline:
            now = time.time()
            if now >= next_ping:
                try:
                    conn.write(b'{"cmd":"/ping"}\n')
                except Exception:
                    break
                next_ping = now + SERIAL_PING_INTERVAL
            chunk = conn.read(conn.in_waiting or 1)
            if chunk:
                buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if line.startswith(_SERIAL_PREFIX):
                    try:
                        resp = json.loads(line[len(_SERIAL_PREFIX):].decode())
                        if resp.get("pong"):
                            print(f"[Serial] ESP32 gefunden: {device}")
                            return conn
                    except Exception:
                        pass
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass
    return None


def _find_esp32_serial():
    """Sucht den ESP32 auf den seriellen Ports und liefert eine offene Verbindung (oder None)."""
    if not _SERIAL_AVAILABLE:
        return None
    for device in _candidate_ports():
        print(f"[Serial] Prüfe Port {device} …")
        conn = _probe_port(device)
        if conn:
            return conn
    return None


def _serial_request(path, data=None):
    """Sendet einen Befehl via USB-Serial und wartet auf die JSON-Antwort.

    Rückgabe: True bei Erfolg, sonst False.
    """
    global _serial_conn
    with _serial_lock:
        if _serial_conn is None or not _serial_conn.is_open:
            print("[Serial] Keine Verbindung – suche ESP32 …")
            conn = _find_esp32_serial()
            if conn:
                _serial_conn = conn
            else:
                print("[Serial] Kein ESP32 per Kabel gefunden.")
                return False
        try:
            payload = {"cmd": path}
            if data is not None:
                payload["data"] = data
            line = (json.dumps(payload) + "\n").encode()
            _serial_conn.reset_input_buffer()
            _serial_conn.write(line)
            # Antwort lesen (ignoriert Debug-Prints ohne Präfix)
            deadline = time.time() + SERIAL_TIMEOUT
            buf = b""
            while time.time() < deadline:
                chunk = _serial_conn.read(_serial_conn.in_waiting or 1)
                if chunk:
                    buf += chunk
                while b"\n" in buf:
                    ln, buf = buf.split(b"\n", 1)
                    ln = ln.strip()
                    if ln.startswith(_SERIAL_PREFIX):
                        try:
                            resp = json.loads(ln[len(_SERIAL_PREFIX):].decode())
                            return resp.get("status") == "ok"
                        except Exception:
                            pass
            print(f"[Serial] Timeout bei {path}")
            return False
        except Exception as e:
            print(f"[Serial] Fehler: {e}")
            try:
                _serial_conn.close()
            except Exception:
                pass
            _serial_conn = None
            return False


def _init_connection():
    """Stellt beim Start die USB-Serial-Verbindung zum ESP32 her."""
    global _serial_conn
    print("[ESP32] Suche ESP32 per USB-Serial …")
    conn = _find_esp32_serial()
    if conn:
        _serial_conn = conn
        print("[ESP32] Kabel-Modus aktiv.")
    else:
        print("[ESP32] Kein ESP32 per USB gefunden – "
              "beim ersten Befehl wird erneut gesucht.")


threading.Thread(target=_init_connection, daemon=True).start()


def _esp_request(path, data=None):
    """Schickt einen Befehl per USB-Serial an den ESP32. True bei Erfolg, sonst False."""
    return bool(_serial_request(path, data))


def start_lichterkette():
    """Startet die Animation auf dem ESP32 (im Hintergrund)."""
    def worker():
        s = LICHT_STATE["start_rgb"]
        m = LICHT_STATE["mid_rgb"]
        e = LICHT_STATE["end_rgb"]
        ok = _esp_request("/start", {
            "start_rgb": list(s),
            "mid_rgb":   list(m),
            "end_rgb":   list(e),
            "animation": LICHT_STATE["animation"],
        })
        print("[ESP32] Lichterkette gestartet." if ok else "[ESP32] Start fehlgeschlagen.")
    threading.Thread(target=worker, daemon=True).start()


def stop_lichterkette():
    """Schaltet die Lichterkette aus (im Hintergrund)."""
    def worker():
        ok = _esp_request("/stop")
        print("[ESP32] Lichterkette ausgeschaltet." if ok else "[ESP32] Stop fehlgeschlagen.")
    threading.Thread(target=worker, daemon=True).start()


# ════════════════════════════════════════════════════════════════
#  Idle-Modus: Tag/Nacht-Weltkarte
#  Der Laptop berechnet aus dem Sonnenstand je Stadt eine Farbe und
#  schickt den fertigen Frame an den ESP32. Wird alle paar Sekunden
#  neu gerechnet, damit der Tag/Nacht-Übergang langsam wandert.
# ════════════════════════════════════════════════════════════════
IDLE_LED_COUNT = 150           # physische Länge der Kette (LED 0..149)
IDLE_REFRESH_SEC = 60         # wie oft der Sonnenstand neu berechnet wird

WETTER_REFRESH_SEC = 600       # Wetterdaten alle 10 Minuten neu abrufen
WETTER_FRAME_SEC   = 0.1       # Animations-Takt der Wetterkarte (~10 fps)

_idle_stop = threading.Event()
_idle_active = False
_idle_mode = None             # "tag_nacht" | "wetter" | None (welcher Idle läuft)
_idle_thread = None           # laufender Idle-Worker (zum sauberen Beenden)
_idle_on_change = None        # optionaler UI-Callback: fn(active: bool)


def _idle_notify(active, mode=None):
    global _idle_active, _idle_mode
    _idle_active = active
    _idle_mode = mode if active else None
    if _idle_on_change:
        _idle_on_change(active)


def _build_idle_pixels():
    """Baut die RGB-Liste (LED 0..IDLE_LED_COUNT-1) für den Sonnenstand jetzt."""
    frame = build_frame(IDLE_LED_COUNT)
    pixels = [[0, 0, 0] for _ in range(IDLE_LED_COUNT)]
    for led, rgb in frame.items():
        if 0 <= led < IDLE_LED_COUNT:
            pixels[led] = list(rgb)
    return pixels


def start_idle():
    """Startet den Tag/Nacht-Idle-Modus (im Hintergrund)."""
    global _idle_thread
    stop_idle()                # evtl. laufenden Loop sauber beenden
    _idle_stop.clear()
    _idle_notify(True, "tag_nacht")

    def worker():
        first = True
        while not _idle_stop.is_set():
            pixels = _build_idle_pixels()
            # Zwischen Berechnung und Senden kann der Stop gekommen sein –
            # dann keinen Frame mehr rausschicken (sonst leuchtet die Kette
            # trotz "ausschalten" wieder auf).
            if _idle_stop.is_set():
                break
            ok = _esp_request("/frame", {"pixels": pixels})
            if first:
                print("[Idle] Tag/Nacht-Weltkarte aktiv." if ok
                      else "[Idle] ESP32 nicht erreichbar.")
                first = False
            _idle_stop.wait(IDLE_REFRESH_SEC)

    _idle_thread = threading.Thread(target=worker, daemon=True)
    _idle_thread.start()


def start_wetter_idle():
    """Startet den Wetter-Idle-Modus (im Hintergrund).

    Der Wetterabruf (Open-Meteo) ist langsam und läuft daher nur alle
    WETTER_REFRESH_SEC in einem eigenen kurzen Thread, damit die Animation
    nie hängt. Die Frames werden im WETTER_FRAME_SEC-Takt gerechnet und
    gesendet – so pulsieren/blitzen/glitzern die LEDs flüssig.
    """
    global _idle_thread
    stop_idle()                # evtl. laufenden Loop sauber beenden
    _idle_stop.clear()
    _idle_notify(True, "wetter")

    def worker():
        t0 = time.monotonic()
        box = {"kategorien": {}}      # aktueller Stand LED -> Wetterkategorie
        box_lock = threading.Lock()
        fetch_laeuft = {"v": False}
        gemeldet = {"v": False}

        def _refetch():
            neu = wetter_idle.kategorien_by_led(IDLE_LED_COUNT)
            with box_lock:
                if neu:
                    box["kategorien"] = neu
            if not gemeldet["v"]:
                print("[Idle] Wetter-Weltkarte aktiv." if neu
                      else "[Idle] Kein Wetter/Mapping verfügbar.")
                gemeldet["v"] = True
            fetch_laeuft["v"] = False

        naechster_abruf = 0.0
        while not _idle_stop.is_set():
            now = time.monotonic()
            if now >= naechster_abruf and not fetch_laeuft["v"]:
                fetch_laeuft["v"] = True
                naechster_abruf = now + WETTER_REFRESH_SEC
                threading.Thread(target=_refetch, daemon=True).start()

            with box_lock:
                kategorien = box["kategorien"]

            frame = wetter_idle.build_frame(IDLE_LED_COUNT, kategorien,
                                            now - t0)
            pixels = [[0, 0, 0] for _ in range(IDLE_LED_COUNT)]
            for led, rgb in frame.items():
                if 0 <= led < IDLE_LED_COUNT:
                    pixels[led] = list(rgb)

            # Zwischen Bauen und Senden kann der Stop gekommen sein – dann
            # keinen Frame mehr rausschicken (sonst leuchtet die Kette trotz
            # "ausschalten" wieder auf).
            if _idle_stop.is_set():
                break
            _esp_request("/frame", {"pixels": pixels})
            _idle_stop.wait(WETTER_FRAME_SEC)

    _idle_thread = threading.Thread(target=worker, daemon=True)
    _idle_thread.start()


def stop_idle():
    """Beendet den Idle-Modus und wartet, bis der Loop wirklich aus ist.

    Das Warten (join) ist wichtig: Wer danach `/stop` schickt, kann sicher
    sein, dass kein bereits losgeschickter `/frame` mehr hinterherkommt und
    die Kette wieder einschaltet.
    """
    global _idle_thread
    _idle_stop.set()
    t = _idle_thread
    if t is not None and t is not threading.current_thread():
        t.join(timeout=SERIAL_TIMEOUT + 1)
    _idle_thread = None
    _idle_notify(False)


# ════════════════════════════════════════════════════════════════
#  LED-Mapping: JSON laden/speichern + Single-LED-Steuerung
# ════════════════════════════════════════════════════════════════
JSON_MAPPING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "led_mapping.json")
MAPPING_ANZ_LEDS = 150
_NULL_SENTINEL = "NULL"   # LED ist physisch nicht sichtbar


def _lade_staedte():
    try:
        with open(JSON_MAPPING_PATH, encoding="utf-8") as f:
            return json.load(f).get("_verfuegbare_staedte", [])
    except (OSError, json.JSONDecodeError):
        return []


def _lade_mapping():
    try:
        with open(JSON_MAPPING_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {i: data.get(str(i)) for i in range(MAPPING_ANZ_LEDS)}
    except (OSError, json.JSONDecodeError):
        return {i: None for i in range(MAPPING_ANZ_LEDS)}


def _speichere_mapping_eintrag(index, city):
    try:
        with open(JSON_MAPPING_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    data[str(index)] = city if city else None
    with open(JSON_MAPPING_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _speichere_mapping_komplett(mapping):
    """Schreibt das gesamte Mapping (Index -> Stadt) zurück in die JSON-Datei."""
    try:
        with open(JSON_MAPPING_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    for i in range(MAPPING_ANZ_LEDS):
        data[str(i)] = mapping.get(i) or None
    with open(JSON_MAPPING_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_mapping_color():
    """Liest die aktuell gewählte Mapping-Farbe aus den UI-Feldern."""
    try:
        entries, _ = _mapping_color_store["farbe"]
        return [max(0, min(255, int(en.get()))) for en in entries]
    except (ValueError, KeyError):
        return [255, 255, 255]


def _stream_single_led(index):
    """Lässt genau LED <index> am ESP32 aufleuchten (Mapping-Modus)."""
    def worker():
        rgb = _get_mapping_color()
        ok = _esp_request(f"/led/{index}", {"rgb": rgb})
        print(f"[ESP32] LED {index} leuchtet." if ok else f"[ESP32] Single-LED fehlgeschlagen.")
    threading.Thread(target=worker, daemon=True).start()


# ════════════════════════════════════════════════════════════════
#  Ranking-Pipeline: Session-Ranking (Flask) -> LED-Positionen -> ESP32
# ════════════════════════════════════════════════════════════════
def _city_to_led_indices():
    """Invertiert led_mapping.json zu: Stadt (kleingeschrieben) -> [LED-Indizes]."""
    out = {}
    for idx, city in _lade_mapping().items():
        if city and city != _NULL_SENTINEL:
            out.setdefault(city.strip().lower(), []).append(idx)
    return out


# ════════════════════════════════════════════════════════════════
#  Scoring: Städte-CSV + Algorithmus aus functions.py importieren
# ════════════════════════════════════════════════════════════════
_PROJ_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, _PROJ_DIR)
from functions import build_user_from_swipes, calculate_user_preference, score_all_cities

_CSV_PATH = os.path.join(_PROJ_DIR, "static", "worldcities_ranked_german.csv")
try:
    with open(_CSV_PATH, encoding="utf-8") as _f:
        _rdr = csv.reader(_f)
        next(_rdr)
        _CITY_LIST = list(_rdr)
    _CITY_MAP = {city[0]: city for city in _CITY_LIST}
except Exception as _csv_err:
    print(f"[Scoring] Städte-CSV nicht geladen: {_csv_err}")
    _CITY_LIST = []
    _CITY_MAP  = {}

# Pfad zur SQLite-Datenbank (liegt eine Ebene über laptop_app/)
_DB_PATH = os.path.join(_PROJ_DIR, "database.db")


def _score_session_leds(session_id):
    """Liest Swipes aus DB, berechnet Score für alle Karten-Städte.
    Gibt (led_order, preview_namen, fehler) zurück."""
    try:
        con = sqlite3.connect(_DB_PATH)
        cur = con.cursor()
        cur.execute(
            "SELECT iteration, city, country, continent, choice "
            "FROM swipes WHERE session_id = ? ORDER BY iteration",
            (session_id,),
        )
        swipes = cur.fetchall()
        con.close()
    except Exception as e:
        return None, None, str(e)

    if not swipes:
        return None, None, "Session nicht in DB oder keine Swipes"

    user   = build_user_from_swipes(swipes, _CITY_MAP)
    prefs  = calculate_user_preference(user)
    scored = score_all_cities(user, prefs, _CITY_LIST)  # beste zuerst

    city_leds = _city_to_led_indices()
    led_order, preview = [], []
    for _, city in scored:
        idxs = city_leds.get(city[0].strip().lower())
        if idxs:
            led_order.extend(idxs)
            if len(preview) < 3:
                preview.append(city[0])

    if not led_order:
        return None, None, "Keine Karten-Städte im Scoring"

    return led_order, preview, None


def start_ranking_animation(session_id):
    """Berechnet Scores für alle Karten-Städte und startet die Animation am ESP32."""
    def _ui(text):
        root.after(0, lambda t=text: status_label.config(text=t))

    def worker():
        # 1) Sofort Fadeout auslösen – egal welche Animation gerade läuft.
        fade_start = time.time()
        _esp_request("/fadeout")

        # 2) Scores berechnen (läuft, während die LEDs ausfaden).
        _ui(f"Session {session_id[:8]} …  ·  Berechne Scores …")
        led_order, preview, err = _score_session_leds(session_id)
        if not led_order:
            _ui(f"Ranking nicht verfügbar  ·  {err}")
            print(f"[Ranking] Fehler: {err}")
            return

        vorschau = "  ·  ".join(f"#{i+1} {c}" for i, c in enumerate(preview))
        _ui(f"{len(led_order)} Karten-LEDs  ·  {vorschau}")

        # 3) Warten, bis der Fadeout sichtbar durchgelaufen ist, bevor die
        #    neue Animation startet (sonst killt /start den Fadeout sofort).
        rest = FADEOUT_DAUER_SEC - (time.time() - fade_start)
        if rest > 0:
            time.sleep(rest)

        ok = _esp_request("/start", {
            "start_rgb": list(LICHT_STATE["start_rgb"]),
            "mid_rgb":   list(LICHT_STATE["mid_rgb"]),
            "end_rgb":   list(LICHT_STATE["end_rgb"]),
            "animation": LICHT_STATE["animation"],
            "leds":      led_order,
        })
        print(f"[Ranking] {len(led_order)} LEDs angesteuert."
              if ok else "[Ranking] ESP32 nicht erreichbar.")
    threading.Thread(target=worker, daemon=True).start()


# ════════════════════════════════════════════════════════════════
#  Design-System  (gespiegelt aus static/theme.css – Sunset)
# ════════════════════════════════════════════════════════════════
BG            = "#0a0d15"   # --bg
BG_SOFT       = "#11141d"   # --bg-soft
SURFACE       = "#171c27"   # Glass-Karte (opak gerendert)
SURFACE_2     = "#1e2431"   # Hover/2. Ebene
BORDER        = "#262d3b"   # --border (opak)
TEXT          = "#f4f6fb"   # --text
TEXT_DIM      = "#a6abbb"   # --text-dim
TEXT_FAINT    = "#6b7080"   # --text-faint

BRAND_1       = "#ff7a18"   # Gradient-Start (orange)
BRAND_MID     = "#ff4140"   # Gradient-Mitte (rot)
BRAND_2       = "#ff2e63"   # Gradient-Ende (pink)
GOLD          = "#ffd166"   # --gold
SILVER        = "#cdd3e0"
BRONZE        = "#e8975a"


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lerp(c1, c2, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def make_gradient(width, height, stops):
    """Horizontaler Mehrfarb-Gradient als PhotoImage (Sunset-Look)."""
    img = Image.new("RGB", (width, height))
    px = img.load()
    n = len(stops) - 1
    for x in range(width):
        pos = x / max(width - 1, 1) * n
        idx = min(int(pos), n - 1)
        t = pos - idx
        col = _lerp(stops[idx], stops[idx + 1], t)
        for y in range(height):
            px[x, y] = col
    return ImageTk.PhotoImage(img)


def make_radial_glow(size, rgb, max_alpha=70):
    """Weicher Aurora-Blob (radialer Verlauf zu transparent).

    Wird klein (128 px) gerendert und hochskaliert – da der Blob ohnehin
    weich ist, bleibt das Ergebnis identisch, aber ohne teures O(n²)-Rendern.
    """
    base = 128
    img = Image.new("RGBA", (base, base), (0, 0, 0, 0))
    px = img.load()
    c = base / 2
    for y in range(base):
        for x in range(base):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5 / c
            if d < 1:
                a = int(max_alpha * (1 - d) ** 2)
                px[x, y] = (rgb[0], rgb[1], rgb[2], a)
    return img.resize((size, size), Image.BILINEAR)


# --- UI Setup ---

root = tk.Tk()
root.title("CityTrip QR-Scanner")
screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()
root.geometry(f"{screen_width}x{screen_height}+0+0")
root.configure(bg=BG)

vid = cv2.VideoCapture(0)
vid.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
vid.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

CAM_WIDTH = 320
CAM_HEIGHT = 180

# ── Aurora-Hintergrund (gespiegelt aus .aurora) ─────────────────
bg_canvas = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0)
bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)

_glow_refs = []
for gx, gy, gsize, gcol, ga in [
    (-0.10, -0.12, 0.55, _hex_to_rgb(BRAND_1), 60),
    (0.78,   0.16, 0.50, _hex_to_rgb(BRAND_2), 48),
    (0.12,   0.70, 0.52, (88, 60, 255),        34),
]:
    s = int(min(screen_width, screen_height) * gsize * 2)
    glow = ImageTk.PhotoImage(make_radial_glow(s, gcol, ga))
    _glow_refs.append(glow)
    bg_canvas.create_image(int(gx * screen_width), int(gy * screen_height),
                           image=glow, anchor="nw")

# Hauptcontainer über dem Hintergrund
main = tk.Frame(root, bg=BG)
main.place(relx=0.5, y=0, relwidth=1, relheight=1, anchor="n")

# Fonts (Sora/Outfit → Helvetica-Fallback wie auf der Website)
brand_font  = tkfont.Font(family="Helvetica", size=34, weight="bold")
title_font  = tkfont.Font(family="Helvetica", size=22, weight="bold")
result_font = tkfont.Font(family="Helvetica", size=16, weight="bold")
sub_font    = tkfont.Font(family="Helvetica", size=13)
status_font = tkfont.Font(family="Helvetica", size=12)
rank_font   = tkfont.Font(family="Helvetica", size=18, weight="bold")
score_font  = tkfont.Font(family="Helvetica", size=12)

# ── Kopfbereich: Marke + Sunset-Gradient-Akzent ─────────────────
header = tk.Frame(main, bg=BG)
header.pack(pady=(46, 6))

brand_label = tk.Label(header, text="CityTrip", font=brand_font, fg=TEXT, bg=BG)
brand_label.pack()

GRAD_STOPS = [_hex_to_rgb(BRAND_1), _hex_to_rgb(BRAND_MID), _hex_to_rgb(BRAND_2)]
accent_img = make_gradient(160, 5, GRAD_STOPS)
accent_bar = tk.Label(header, image=accent_img, bg=BG, bd=0)
accent_bar.image = accent_img
accent_bar.pack(pady=(8, 0))

# ── Status-Chip ─────────────────────────────────────────────────
status_label = tk.Label(main, text="QR-Code vor die Kamera halten …",
                        font=status_font, fg=TEXT_DIM, bg=SURFACE,
                        padx=18, pady=8)
status_label.pack(pady=(22, 8))

# ── Kamera-Vorschau (rechts oben, mit Gradient-Rahmen) ──────────
cam_frame = tk.Frame(root, bg=BRAND_MID, bd=0)
cam_frame.place(relx=1.0, x=-24, y=24, anchor="ne")
cam_label = tk.Label(cam_frame, bg=BG, bd=0)
cam_label.pack(padx=2, pady=2)

# ── Lichterketten-Steuerung: Buttons oben links (Sunset-Look) ───
def _make_chip_button(parent, text, command):
    """Erzeugt einen klickbaren Chip-Button (Label) im Sunset-Stil."""
    frame = tk.Frame(parent, bg=BRAND_MID, bd=0)
    btn = tk.Label(frame, text=text, font=status_font, fg=TEXT, bg=SURFACE,
                   padx=18, pady=10, cursor="hand2")
    btn.pack(padx=2, pady=2)
    btn.bind("<Button-1>", lambda e: command())
    btn.bind("<Enter>", lambda e: btn.config(bg=SURFACE_2, fg=GOLD))
    btn.bind("<Leave>", lambda e: btn.config(bg=SURFACE, fg=TEXT))
    return frame, btn


def _flash_text(btn, text, dauer=1600):
    """Zeigt kurz einen Bestätigungstext und stellt den Original-Text wieder her."""
    original = btn.cget("text")
    btn.config(text=text)
    btn.after(dauer, lambda: btn.config(text=original))


licht_panel = tk.Frame(root, bg=BG)
licht_panel.place(x=24, y=24, anchor="nw")


def _on_licht_neustart():
    stop_idle()
    _exit_mapping_ui()
    start_lichterkette()
    _flash_text(neustart_btn, "Animation neu gestartet ✓")


def _on_licht_aus():
    stop_idle()
    _exit_mapping_ui()
    stop_lichterkette()
    _flash_text(licht_aus_btn, "Kette ausgeschaltet ✓")


neustart_frame, neustart_btn = _make_chip_button(
    licht_panel, "↻  Animation neu starten", _on_licht_neustart)
neustart_frame.pack(anchor="nw")

aus_frame, licht_aus_btn = _make_chip_button(
    licht_panel, "⏻  Kette ausschalten", _on_licht_aus)
aus_frame.pack(anchor="nw", pady=(8, 0))

mapping_f, mapping_btn = _make_chip_button(
    licht_panel, "⊞  LED-Mapping", lambda: _toggle_mapping_mode())
mapping_f.pack(anchor="nw", pady=(8, 0))


def _on_idle_toggle():
    if _idle_active and _idle_mode == "tag_nacht":
        stop_idle()
        stop_lichterkette()      # Kette ausschalten
        status_label.config(text="Idle-Modus beendet.")
    else:
        _exit_mapping_ui()
        start_idle()             # stoppt vorher selbst einen evtl. Wetter-Idle
        status_label.config(text="Idle-Modus: Tag/Nacht-Weltkarte aktiv.")


idle_f, idle_btn = _make_chip_button(
    licht_panel, "🌙  Tag/Nacht (Idle)", _on_idle_toggle)
idle_f.pack(anchor="nw", pady=(8, 0))


def _on_wetter_idle_toggle():
    if _idle_active and _idle_mode == "wetter":
        stop_idle()
        stop_lichterkette()      # Kette ausschalten
        status_label.config(text="Wetter-Idle beendet.")
    else:
        _exit_mapping_ui()
        start_wetter_idle()      # stoppt vorher selbst einen evtl. Tag/Nacht-Idle
        status_label.config(text="Idle-Modus: Wetter-Weltkarte aktiv …")


wetter_idle_f, wetter_idle_btn = _make_chip_button(
    licht_panel, "🌦  Wetter (Idle)", _on_wetter_idle_toggle)
wetter_idle_f.pack(anchor="nw", pady=(8, 0))


# ── Steuer-Panel: Farben + Animation ────────────────────────────
def _rgb_to_hex(rgb):
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    return "#%02x%02x%02x" % (r, g, b)


control_card = tk.Frame(main, bg=SURFACE, highlightthickness=1,
                        highlightbackground=BORDER)
control_card.pack(pady=(10, 0), padx=80, fill="x")

tk.Label(control_card, text="Lichterkette steuern", font=title_font,
         fg=TEXT, bg=SURFACE).pack(anchor="w", padx=24, pady=(18, 2))
tk.Label(control_card, text="Farbverlauf der Kette · links = Stadt passt gut, "
         "rechts = passt kaum", font=sub_font, fg=TEXT_DIM,
         bg=SURFACE).pack(anchor="w", padx=24, pady=(0, 14))

# key -> (liste der 3 Entry-Felder, Swatch-Frame)
_color_entries = {}


def _build_color_row(key, label_text, rgb, parent=None, store=None):
    parent = parent if parent is not None else control_card
    store = store if store is not None else _color_entries
    row = tk.Frame(parent, bg=SURFACE)
    row.pack(fill="x", padx=24, pady=6)

    tk.Label(row, text=label_text, font=sub_font, fg=TEXT, bg=SURFACE,
             width=22, anchor="w").pack(side="left")

    swatch = tk.Frame(row, bg=_rgb_to_hex(rgb), width=38, height=26,
                      highlightthickness=1, highlightbackground=BORDER)
    swatch.pack_propagate(False)
    swatch.pack(side="left", padx=(0, 16))

    entries = []
    for i, ch in enumerate(("R", "G", "B")):
        tk.Label(row, text=ch, font=score_font, fg=TEXT_FAINT,
                 bg=SURFACE).pack(side="left", padx=(8, 3))
        e = tk.Entry(row, width=4, font=status_font, justify="center",
                     bg=SURFACE_2, fg=TEXT, insertbackground=TEXT, relief="flat",
                     highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=BRAND_MID)
        e.insert(0, str(rgb[i]))
        e.pack(side="left")
        entries.append(e)

    def _update_swatch(_event=None):
        try:
            swatch.config(bg=_rgb_to_hex([int(en.get() or 0) for en in entries]))
        except ValueError:
            pass

    for en in entries:
        en.bind("<KeyRelease>", _update_swatch)

    store[key] = (entries, swatch)


_build_color_row("start", "Startfarbe (passt gut)", LICHT_STATE["start_rgb"])
_build_color_row("mid", "Mittelfarbe (Übergang)", LICHT_STATE["mid_rgb"])
_build_color_row("end", "Endfarbe (passt kaum)", LICHT_STATE["end_rgb"])


def _read_color_entries():
    """Liest die Entry-Felder nach LICHT_STATE; False bei ungültiger Eingabe."""
    try:
        for key in ("start", "mid", "end"):
            entries, _ = _color_entries[key]
            LICHT_STATE[f"{key}_rgb"] = tuple(
                max(0, min(255, int(en.get()))) for en in entries)
        return True
    except ValueError:
        status_label.config(text="Ungültige Farbe – bitte ganze Zahlen 0–255 eingeben.")
        return False


def _update_anim_btn():
    an = LICHT_STATE["animation"]
    anim_btn.config(text=f"Animation: {'An' if an else 'Aus'}")


def _on_uebernehmen():
    if not _read_color_entries():
        return
    start_lichterkette()
    status_label.config(text="Farben übernommen · Animation neu gestartet.")


def _on_toggle_animation():
    LICHT_STATE["animation"] = not LICHT_STATE["animation"]
    _update_anim_btn()
    _read_color_entries()      # aktuelle Farben gleich mitnehmen
    start_lichterkette()       # sofort anwenden


action_row = tk.Frame(control_card, bg=SURFACE)
action_row.pack(fill="x", padx=24, pady=(14, 20))

uebernehmen_frame, uebernehmen_btn = _make_chip_button(
    action_row, "✓  Übernehmen & neu starten", _on_uebernehmen)
uebernehmen_frame.pack(side="left")

anim_frame, anim_btn = _make_chip_button(
    action_row, "Animation: An", _on_toggle_animation)
anim_frame.pack(side="left", padx=(10, 0))
_update_anim_btn()


# ── Steuer-Panel: Idle-Farben (Tag/Nacht) ───────────────────────
_idle_farben = get_idle_farben()
_idle_color_entries = {}

idle_card = tk.Frame(main, bg=SURFACE, highlightthickness=1,
                     highlightbackground=BORDER)
idle_card.pack(pady=(10, 0), padx=80, fill="x")

tk.Label(idle_card, text="Idle-Farben (Tag/Nacht)", font=title_font,
         fg=TEXT, bg=SURFACE).pack(anchor="w", padx=24, pady=(18, 2))
tk.Label(idle_card, text="Farben der Tag/Nacht-Weltkarte · Tag = Stadt im "
         "Sonnenlicht, Nacht = im Dunkeln", font=sub_font, fg=TEXT_DIM,
         bg=SURFACE).pack(anchor="w", padx=24, pady=(0, 14))

_build_color_row("tag", "Tag (Sonnenlicht)", _idle_farben["tag"],
                 parent=idle_card, store=_idle_color_entries)
_build_color_row("orange", "Sonnenauf-/untergang", _idle_farben["orange"],
                 parent=idle_card, store=_idle_color_entries)
_build_color_row("nacht", "Nacht (dunkel)", _idle_farben["nacht"],
                 parent=idle_card, store=_idle_color_entries)

# Helligkeit (0–100 %)
idle_hell_row = tk.Frame(idle_card, bg=SURFACE)
idle_hell_row.pack(fill="x", padx=24, pady=6)
tk.Label(idle_hell_row, text="Helligkeit (%)", font=sub_font, fg=TEXT, bg=SURFACE,
         width=22, anchor="w").pack(side="left")
idle_hell_entry = tk.Entry(idle_hell_row, width=5, font=status_font, justify="center",
                           bg=SURFACE_2, fg=TEXT, insertbackground=TEXT, relief="flat",
                           highlightthickness=1, highlightbackground=BORDER,
                           highlightcolor=BRAND_MID)
idle_hell_entry.insert(0, str(round(_idle_farben["helligkeit"] * 100)))
idle_hell_entry.pack(side="left")


def _read_idle_entries():
    """Liest die Idle-Felder und setzt sie in tag_nacht; False bei Fehleingabe."""
    try:
        farben = {}
        for key in ("tag", "orange", "nacht"):
            entries, _ = _idle_color_entries[key]
            farben[key] = tuple(max(0, min(255, int(en.get()))) for en in entries)
        hell = max(0, min(100, int(idle_hell_entry.get()))) / 100.0
        set_idle_farben(tag=farben["tag"], orange=farben["orange"],
                        nacht=farben["nacht"], helligkeit=hell)
        return True
    except ValueError:
        status_label.config(text="Ungültige Idle-Farbe – bitte ganze Zahlen eingeben.")
        return False


def _on_idle_farben_uebernehmen():
    if not _read_idle_entries():
        return
    if _idle_active:
        start_idle()      # Loop neu starten -> sofort Frame mit neuen Farben
        status_label.config(text="Idle-Farben übernommen · Tag/Nacht läuft.")
    else:
        status_label.config(text="Idle-Farben gespeichert · beim nächsten Idle-Start aktiv.")


idle_action_row = tk.Frame(idle_card, bg=SURFACE)
idle_action_row.pack(fill="x", padx=24, pady=(14, 20))

idle_uebernehmen_frame, idle_uebernehmen_btn = _make_chip_button(
    idle_action_row, "✓  Idle-Farben übernehmen", _on_idle_farben_uebernehmen)
idle_uebernehmen_frame.pack(side="left")


# ── Mapping-Modus Panel ─────────────────────────────────────────
_mapping_data  = _lade_mapping()
_alle_staedte  = _lade_staedte()
_mapping_index = [0]
_mapping_aktiv = [False]

mapping_card = tk.Frame(main, bg=SURFACE, highlightthickness=1,
                        highlightbackground=BORDER)
# Wird erst bei Aktivierung eingeblendet (pack_forget = versteckt)

m_header_row = tk.Frame(mapping_card, bg=SURFACE)
m_header_row.pack(fill="x", padx=24, pady=(18, 4))
tk.Label(m_header_row, text="LED-Mapping", font=title_font,
         fg=TEXT, bg=SURFACE).pack(side="left")
m_progress = tk.Label(m_header_row, text="", font=score_font,
                      fg=TEXT_FAINT, bg=SURFACE)
m_progress.pack(side="right")

tk.Label(mapping_card,
         text="Navigiere durch die LEDs · jede LED leuchtet auf · weise ihr eine Stadt zu",
         font=sub_font, fg=TEXT_DIM, bg=SURFACE).pack(anchor="w", padx=24, pady=(0, 8))

# Mapping-Farbe (Farbe der aufleuchtenden LED)
_mapping_color_store = {}
_build_color_row("farbe", "LED-Farbe (Mapping)", (255, 255, 255),
                 parent=mapping_card, store=_mapping_color_store)

# Navigation
m_nav_row = tk.Frame(mapping_card, bg=SURFACE)
m_nav_row.pack(fill="x", padx=24, pady=(0, 6))

m_prev_f, _ = _make_chip_button(m_nav_row, "←  Zurück",  lambda: _mapping_navigate(-1))
m_prev_f.pack(side="left")

m_led_label = tk.Label(m_nav_row, text=f"LED 0 / {MAPPING_ANZ_LEDS}", font=result_font,
                       fg=GOLD, bg=SURFACE, width=14, anchor="center")
m_led_label.pack(side="left", padx=20)

m_next_f, _ = _make_chip_button(m_nav_row, "Weiter  →", lambda: _mapping_navigate(+1))
m_next_f.pack(side="left")

# Aktuelle Zuweisung
m_current = tk.Label(mapping_card, text="", font=sub_font, fg=TEXT_FAINT, bg=SURFACE)
m_current.pack(anchor="w", padx=24, pady=(10, 0))

# Stadt-Suchzeile (Eingabefeld mit Live-Vorschlägen)
m_combo_row = tk.Frame(mapping_card, bg=SURFACE)
m_combo_row.pack(fill="x", padx=24, pady=(10, 22))

tk.Label(m_combo_row, text="Stadt:", font=sub_font, fg=TEXT,
         bg=SURFACE).pack(side="left", padx=(0, 12))

m_entry = tk.Entry(m_combo_row, font=sub_font, width=30,
                   bg=SURFACE_2, fg=TEXT, insertbackground=TEXT, relief="flat",
                   highlightthickness=1, highlightbackground=BORDER,
                   highlightcolor=BRAND_MID)
m_entry.pack(side="left")

m_null_frame, m_null_btn = _make_chip_button(
    m_combo_row, "⊘ NULL", lambda: _mapping_set_null())
m_null_frame.pack(side="left", padx=(10, 0))

m_save_label = tk.Label(m_combo_row, text="", font=score_font, fg=GOLD, bg=SURFACE)
m_save_label.pack(side="left", padx=(14, 0))

# Sammel-Aktionen: alle Zuweisungen löschen / zufällig verteilen
m_action_row = tk.Frame(mapping_card, bg=SURFACE)
m_action_row.pack(fill="x", padx=24, pady=(0, 22))

m_clear_f, m_clear_btn = _make_chip_button(
    m_action_row, "🗑  Alle entfernen", lambda: _mapping_alle_entfernen())
m_clear_f.pack(side="left")

m_random_f, m_random_btn = _make_chip_button(
    m_action_row, "🎲  Zufällig zuweisen", lambda: _mapping_random_zuweisen())
m_random_f.pack(side="left", padx=(10, 0))


def _mapping_update_ui():
    idx = _mapping_index[0]
    zugewiesen = sum(1 for v in _mapping_data.values() if v)
    m_progress.config(text=f"{zugewiesen} / {MAPPING_ANZ_LEDS} zugewiesen")
    m_led_label.config(text=f"LED {idx} / {MAPPING_ANZ_LEDS}")
    city = _mapping_data.get(idx)
    if city == _NULL_SENTINEL:
        m_current.config(text="NULL – nicht sichtbar", fg=TEXT_DIM)
    elif city:
        m_current.config(text=f"Gespeichert: {city}", fg=GOLD)
    else:
        m_current.config(text="Noch nicht zugewiesen", fg=TEXT_FAINT)
    _ac_close()
    m_entry.delete(0, "end")
    m_entry.insert(0, city if city else "")
    m_save_label.config(text="")


def _mapping_speichern_aktuell():
    raw = m_entry.get().strip()
    if raw.upper() == _NULL_SENTINEL:
        city = _NULL_SENTINEL
    else:
        city = _resolve_city(raw) if raw else None
    idx = _mapping_index[0]
    _mapping_data[idx] = city
    _speichere_mapping_eintrag(idx, city)
    # Eingabefeld auf den kanonischen Namen normalisieren (z. B. "Belize Stadt" -> "Belize-Stadt")
    if raw != (city or ""):
        m_entry.delete(0, "end")
        m_entry.insert(0, city or "")
    zugewiesen = sum(1 for v in _mapping_data.values() if v)
    m_progress.config(text=f"{zugewiesen} / {MAPPING_ANZ_LEDS} zugewiesen")
    if city == _NULL_SENTINEL:
        m_current.config(text="NULL – nicht sichtbar", fg=TEXT_DIM)
    else:
        m_current.config(
            text=f"Gespeichert: {city}" if city else "Noch nicht zugewiesen",
            fg=GOLD if city else TEXT_FAINT)
    m_save_label.config(text="✓")
    m_save_label.after(1400, lambda: m_save_label.config(text=""))


def _mapping_set_null():
    """Markiert die aktuelle LED explizit als nicht sichtbar (NULL) und springt zur nächsten."""
    idx = _mapping_index[0]
    _mapping_data[idx] = _NULL_SENTINEL
    _speichere_mapping_eintrag(idx, _NULL_SENTINEL)
    _ac_close()
    m_entry.delete(0, "end")
    m_entry.insert(0, _NULL_SENTINEL)
    zugewiesen = sum(1 for v in _mapping_data.values() if v)
    m_progress.config(text=f"{zugewiesen} / {MAPPING_ANZ_LEDS} zugewiesen")
    m_current.config(text="NULL – nicht sichtbar", fg=TEXT_DIM)
    m_save_label.config(text="✓")
    m_save_label.after(1400, lambda: m_save_label.config(text=""))
    # direkt zur nächsten LED springen
    new_idx = (idx + 1) % MAPPING_ANZ_LEDS
    _mapping_index[0] = new_idx
    _stream_single_led(new_idx)
    _mapping_update_ui()
    m_entry.focus_set()


def _mapping_navigate(richtung):
    _mapping_speichern_aktuell()
    new_idx = (_mapping_index[0] + richtung) % MAPPING_ANZ_LEDS
    _mapping_index[0] = new_idx
    _stream_single_led(new_idx)
    _mapping_update_ui()
    m_entry.focus_set()


def _mapping_alle_entfernen():
    """Löscht alle LED-Zuweisungen (mit Sicherheitsabfrage)."""
    if not messagebox.askyesno(
            "Alle entfernen",
            "Wirklich alle LED-Zuweisungen löschen?"):
        return
    for i in range(MAPPING_ANZ_LEDS):
        _mapping_data[i] = None
    _speichere_mapping_komplett(_mapping_data)
    _mapping_update_ui()
    _flash_text(m_clear_btn, "🗑  Alle entfernt ✓")


def _mapping_random_zuweisen():
    """Verteilt alle verfügbaren Städte auf zufällige, eindeutige LEDs."""
    staedte = list(_alle_staedte)
    if not staedte:
        _flash_text(m_random_btn, "Keine Städte gefunden")
        return
    if not messagebox.askyesno(
            "Zufällig zuweisen",
            f"Allen {len(staedte)} Städten zufällig eine LED zuweisen?\n"
            "Bestehende Zuweisungen werden überschrieben."):
        return
    random.shuffle(staedte)
    leds = random.sample(range(MAPPING_ANZ_LEDS), min(len(staedte), MAPPING_ANZ_LEDS))
    for i in range(MAPPING_ANZ_LEDS):
        _mapping_data[i] = None
    for led, city in zip(leds, staedte):
        _mapping_data[led] = city
    _speichere_mapping_komplett(_mapping_data)
    _mapping_update_ui()
    _stream_single_led(_mapping_index[0])
    _flash_text(m_random_btn, f"🎲  {len(leds)} Städte verteilt ✓")


# ── Autocomplete / Live-Suche für das Stadt-Feld ────────────────
# Normalisierung: Groß/Klein egal, Bindestriche und Leerzeichen
# gleichwertig, Akzente/Umlaute ignoriert. So findet "belize stadt"
# auch "Belize-Stadt" und "quebec" auch "Québec".
def _norm_city(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().replace("-", " ").split())


# Suchindex einmal vorberechnen: (normalisiert, Originalname)
_staedte_index = [(_norm_city(c), c) for c in _alle_staedte]

_ac_popup = [None]      # Toplevel mit der Vorschlagsliste
_ac_listbox = [None]    # Listbox darin


def _resolve_city(text):
    """Macht aus einer (evtl. ungenauen) Eingabe den exakten Städtenamen."""
    text = text.strip()
    if not text:
        return ""
    n = _norm_city(text)
    # 1) exakte (normalisierte) Übereinstimmung bevorzugen
    for norm, orig in _staedte_index:
        if norm == n:
            return orig
    # 2) sonst erster Vorschlag, der mit der Eingabe beginnt
    for norm, orig in _staedte_index:
        if norm.startswith(n):
            return orig
    # 3) sonst erster Treffer, der die Eingabe enthält
    for norm, orig in _staedte_index:
        if n in norm:
            return orig
    return text  # nichts gefunden -> Eingabe unverändert lassen


def _ac_matches(text):
    n = _norm_city(text)
    if not n:
        return list(_alle_staedte)
    # Treffer am Wortanfang zuerst, danach sonstige Teiltreffer
    starts = [orig for norm, orig in _staedte_index if norm.startswith(n)]
    contains = [orig for norm, orig in _staedte_index
                if n in norm and not norm.startswith(n)]
    return starts + contains


def _ac_close(_event=None):
    if _ac_popup[0] is not None:
        _ac_popup[0].destroy()
        _ac_popup[0] = None
        _ac_listbox[0] = None


def _ac_accept(_event=None):
    lb = _ac_listbox[0]
    if lb is not None and lb.curselection():
        city = lb.get(lb.curselection()[0])
        m_entry.delete(0, "end")
        m_entry.insert(0, city)
        m_entry.icursor("end")
    _ac_close()
    _mapping_speichern_aktuell()
    return "break"


def _ac_show(_event=None):
    # Navigationstasten nicht als Tippen behandeln
    if _event is not None and _event.keysym in (
            "Up", "Down", "Return", "Escape", "Tab"):
        return
    if m_entry.get().strip().upper() == _NULL_SENTINEL:
        _ac_close()
        return
    matches = _ac_matches(m_entry.get())[:60]
    if not matches:
        _ac_close()
        return

    if _ac_popup[0] is None:
        pop = tk.Toplevel(root)
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        lb = tk.Listbox(pop, font=sub_font, activestyle="none",
                        bg=SURFACE_2, fg=TEXT, relief="flat",
                        highlightthickness=1, highlightbackground=BORDER,
                        selectbackground=BRAND_MID, selectforeground=TEXT,
                        exportselection=False)
        lb.pack(fill="both", expand=True)
        lb.bind("<ButtonRelease-1>", _ac_accept)
        lb.bind("<Return>", _ac_accept)
        _ac_popup[0] = pop
        _ac_listbox[0] = lb
    else:
        pop = _ac_popup[0]
        lb = _ac_listbox[0]

    lb.delete(0, "end")
    for c in matches:
        lb.insert("end", c)
    lb.selection_clear(0, "end")
    lb.selection_set(0)
    lb.activate(0)
    lb.config(height=min(len(matches), 8))

    # Popup direkt unter das Eingabefeld setzen
    pop.update_idletasks()
    x = m_entry.winfo_rootx()
    y = m_entry.winfo_rooty() + m_entry.winfo_height()
    w = max(m_entry.winfo_width(), 220)
    h = lb.winfo_reqheight()
    pop.geometry(f"{w}x{h}+{x}+{y}")


def _ac_move(richtung):
    """Pfeiltasten: Auswahl in der Vorschlagsliste verschieben."""
    lb = _ac_listbox[0]
    if lb is None:
        _ac_show()
        return "break"
    size = lb.size()
    if size == 0:
        return "break"
    cur = lb.curselection()
    new = (cur[0] if cur else 0) + richtung
    new = max(0, min(size - 1, new))
    lb.selection_clear(0, "end")
    lb.selection_set(new)
    lb.activate(new)
    lb.see(new)
    return "break"


m_entry.bind("<KeyRelease>", _ac_show)
m_entry.bind("<Down>", lambda e: _ac_move(+1))
m_entry.bind("<Up>", lambda e: _ac_move(-1))
m_entry.bind("<Return>", _ac_accept)
m_entry.bind("<Escape>", _ac_close)
m_entry.bind("<FocusIn>", _ac_show)
m_entry.bind("<Button-1>", lambda e: m_entry.focus_force(), add="+")


def _on_entry_focus_out(event):
    # Autocomplete kurz warten lassen, damit ein Klick auf die Liste ankommt
    m_entry.after(150, _ac_close)
    # Fokus zurückgeben, wenn er an ein nicht-interaktives Widget gegangen ist
    if _mapping_aktiv[0]:
        def _guard():
            if not _mapping_aktiv[0]:
                return
            w = root.focus_get()
            if w is None or isinstance(w, (tk.Frame, tk.Canvas, tk.Label)):
                m_entry.focus_set()
        m_entry.after(200, _guard)


m_entry.bind("<FocusOut>", _on_entry_focus_out)


def _exit_mapping_ui():
    """Blendet das Mapping-Panel aus – ohne die Kette zu stoppen (wird von anderen Modi übernommen)."""
    if _mapping_aktiv[0]:
        _mapping_aktiv[0] = False
        mapping_card.pack_forget()
        mapping_btn.config(text="⊞  LED-Mapping")


def _toggle_mapping_mode():
    if _mapping_aktiv[0]:
        _exit_mapping_ui()
        stop_lichterkette()
    else:
        stop_idle()
        _mapping_aktiv[0] = True
        mapping_card.pack(pady=(10, 0), padx=80, fill="x", after=control_card)
        mapping_btn.config(text="✕  Mapping beenden")
        _mapping_update_ui()
        _stream_single_led(_mapping_index[0])
        m_entry.focus_set()


last_scan_time = 0.0
detector = cv2.QRCodeDetector()

# QR-Erkennung ist teuer (voller Frame). Sie läuft daher nicht in jedem
# Kamera-Frame, sondern nur jeden N-ten – das entlastet den Tk-Mainthread
# spürbar, ohne dass ein QR-Code übersehen wird (er bleibt mehrere Frames
# lang im Bild).
QR_DETECT_EVERY = 3
CAM_REFRESH_MS  = 33   # ~30 fps statt 200 fps
_frame_count = 0


def on_qr_detected(data):
    global last_scan_time

    # URL-Format: http://host/results/<uuid> oder direkte UUID
    if data.startswith("http"):
        session_id = data.rstrip("/").split("/")[-1]
    else:
        session_id = data

    if time.time() - last_scan_time < SCAN_COOLDOWN_SEC:
        return
    last_scan_time = time.time()

    stop_idle()
    _exit_mapping_ui()
    start_ranking_animation(session_id)   # löst zuerst den Fadeout aus, dann das Ranking


def update():
    global _frame_count
    _, frame = vid.read()

    _frame_count += 1
    if _frame_count % QR_DETECT_EVERY == 0:
        data, _, _ = detector.detectAndDecode(frame)
        if data:
            on_qr_detected(data)

    opencv_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
    captured_image = Image.fromarray(opencv_image)
    w, h = captured_image.size
    crop = 300
    captured_image = captured_image.crop((crop, 0, w - crop, h))
    cropped_w, cropped_h = captured_image.size
    new_h = int(CAM_WIDTH * cropped_h / cropped_w)
    captured_image = captured_image.resize((CAM_WIDTH, new_h))
    photo_image = ImageTk.PhotoImage(image=captured_image)
    cam_label.photo_image = photo_image
    cam_label.configure(image=photo_image)
    cam_label.after(CAM_REFRESH_MS, update)


update()

root.bind('<Escape>', lambda e: root.quit())
root.mainloop()
