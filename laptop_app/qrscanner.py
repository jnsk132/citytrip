import cv2
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox
from PIL import Image, ImageTk, ImageDraw
import os
import json
import random
import socket
import threading
import time
import unicodedata
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from tag_nacht import build_frame, set_idle_farben, get_idle_farben


# ════════════════════════════════════════════════════════════════
#  ESP32-Lichterkette: Kommunikation über WLAN (HTTP)
#  ESP32 muss esp32_firmware/main.py ausführen und im selben WLAN sein.
#  Die IP wird automatisch im lokalen Netz gesucht (kein Eintragen nötig).
# ════════════════════════════════════════════════════════════════
ESP32_HOST = None             # None = automatisch suchen; sonst feste IP "192.168.x.y"
ESP32_HTTP_PORT = 80
ESP32_TIMEOUT = 3             # Sekunden bis Timeout pro normalem Request
ESP32_SCAN_TIMEOUT = 0.4      # Sekunden pro Host beim Netz-Scan

# Steuerzustand der Lichterkette (wird von der UI gesetzt, an den ESP32 geschickt).
LICHT_STATE = {
    "start_rgb": (255, 255, 50),
    "mid_rgb":   (255, 130, 0),
    "end_rgb":   (255, 0,   0),
    "animation": True,
}

# Zuletzt gefundene/erreichbare ESP32-IP (Cache, damit nicht jedes Mal gescannt wird).
_esp_host = ESP32_HOST
_esp_host_lock = threading.Lock()


def _local_subnet_prefix():
    """Ermittelt das eigene /24-Präfix, z. B. '192.168.8.' (oder None)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # verbindet nicht wirklich, liefert nur lokale IP
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return ip.rsplit(".", 1)[0] + "."


def _probe_esp32(host):
    """True, wenn unter <host> wirklich unser ESP32 antwortet (echtes /status-JSON)."""
    url = f"http://{host}:{ESP32_HTTP_PORT}/status"
    try:
        with urllib.request.urlopen(url, timeout=ESP32_SCAN_TIMEOUT) as r:
            data = json.loads(r.read().decode())
        # Eindeutiger Marker unserer Firmware – filtert Router/Captive-Portals raus.
        return "anim_running" in data
    except Exception:
        return False


def _discover_esp32():
    """Scannt das lokale /24-Netz nach dem ESP32 und liefert dessen IP (oder None)."""
    prefix = _local_subnet_prefix()
    if not prefix:
        return None
    print(f"[ESP32] Suche Lichterkette im Netz {prefix}0/24 …")
    hosts = [f"{prefix}{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=64) as pool:
        for host, found in zip(hosts, pool.map(_probe_esp32, hosts)):
            if found:
                print(f"[ESP32] Gefunden: {host}")
                return host
    print("[ESP32] Kein ESP32 im Netz gefunden.")
    return None


def _get_esp_host(force_rescan=False):
    """Liefert die ESP32-IP – aus dem Cache, oder per Netz-Scan."""
    global _esp_host
    with _esp_host_lock:
        if _esp_host and not force_rescan:
            return _esp_host
        _esp_host = _discover_esp32()
        return _esp_host


def _esp_request(path, data=None, _retry=True):
    """Schickt GET/POST an den ESP32. Bei Fehler einmal neu suchen und erneut versuchen."""
    host = _get_esp_host()
    if not host:
        print(f"[ESP32] Kein ESP32 erreichbar ({path}).")
        return False
    url = f"http://{host}:{ESP32_HTTP_PORT}{path}"
    try:
        if data is not None:
            body = json.dumps(data).encode()
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=ESP32_TIMEOUT):
            return True
    except (urllib.error.URLError, OSError) as e:
        print(f"[ESP32] HTTP-Fehler ({path}): {e}")
        if _retry:
            # IP evtl. veraltet (neues WLAN / DHCP) → einmal neu suchen.
            if _get_esp_host(force_rescan=True):
                return _esp_request(path, data, _retry=False)
        return False


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
IDLE_LED_COUNT = 50           # physische Länge der Kette (LED 0..49)
IDLE_REFRESH_SEC = 60         # wie oft der Sonnenstand neu berechnet wird

_idle_stop = threading.Event()
_idle_active = False
_idle_thread = None           # laufender Idle-Worker (zum sauberen Beenden)
_idle_on_change = None        # optionaler UI-Callback: fn(active: bool)


def _idle_notify(active):
    global _idle_active
    _idle_active = active
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
    _idle_notify(True)

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
        t.join(timeout=ESP32_TIMEOUT + 1)
    _idle_thread = None
    _idle_notify(False)


# ════════════════════════════════════════════════════════════════
#  LED-Mapping: JSON laden/speichern + Single-LED-Steuerung
# ════════════════════════════════════════════════════════════════
JSON_MAPPING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "led_mapping.json")
MAPPING_ANZ_LEDS = 103


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


def _stream_single_led(index):
    """Lässt genau LED <index> am ESP32 aufleuchten (Mapping-Modus)."""
    def worker():
        ok = _esp_request(f"/led/{index}")
        print(f"[ESP32] LED {index} leuchtet." if ok else f"[ESP32] Single-LED fehlgeschlagen.")
    threading.Thread(target=worker, daemon=True).start()


# ════════════════════════════════════════════════════════════════
#  Ranking-Pipeline: Session-Ranking (Flask) -> LED-Positionen -> ESP32
# ════════════════════════════════════════════════════════════════
def _city_to_led_indices():
    """Invertiert led_mapping.json zu: Stadt (kleingeschrieben) -> [LED-Indizes]."""
    out = {}
    for idx, city in _lade_mapping().items():
        if city:
            out.setdefault(city.strip().lower(), []).append(idx)
    return out


def _fetch_ranking(base_url, session_id):
    """Holt das Ranking der Session vom Flask-Backend. Städte (bestes zuerst) oder None."""
    url = f"{base_url}/api/led-ranking/{session_id}"
    try:
        with urllib.request.urlopen(url, timeout=ESP32_TIMEOUT) as r:
            data = json.loads(r.read().decode())
        ranking = data.get("ranking", [])
        ranking.sort(key=lambda e: e.get("rank", 9999))   # sicherheitshalber nach Rang
        return [e["city"] for e in ranking if e.get("city")]
    except Exception as e:
        print(f"[Ranking] Abruf fehlgeschlagen: {e}")
        return None


def _ranking_to_led_order(cities):
    """Geordnete Städteliste -> geordnete LED-Indizes. Liefert (led_order, fehlende_staedte)."""
    city_leds = _city_to_led_indices()
    led_order, fehlend = [], []
    for city in cities:
        idxs = city_leds.get(city.strip().lower())
        if idxs:
            led_order.extend(idxs)
        else:
            fehlend.append(city)
    return led_order, fehlend


# Bereich für zufällige Test-Positionen, solange das LED-Mapping noch leer ist.
# Bei 0..49 sicher auf der Kette sichtbar; bei längerer Kette auf 103 erhöhen.
RANDOM_TEST_LEDS = 50


def _random_led_order(count):
    """Zufällige, eindeutige LED-Positionen (Rang-Reihenfolge) zum Testen ohne Mapping."""
    n = min(count, RANDOM_TEST_LEDS)
    return random.sample(range(RANDOM_TEST_LEDS), n)


def start_ranking_animation(base_url, session_id):
    """Holt Ranking, mappt es auf LED-Positionen und startet die Animation am ESP32.

    Fällt auf die generische Animation zurück, wenn kein Ranking/Mapping da ist.
    """
    def worker():
        cities = _fetch_ranking(base_url, session_id)
        if not cities:
            print("[Ranking] Kein Ranking erhalten – generische Animation.")
            start_lichterkette()
            return
        led_order, fehlend = _ranking_to_led_order(cities)
        if not led_order:
            # Mapping noch leer → Test mit zufälligen Positionen, Rang-Reihenfolge bleibt.
            led_order = _random_led_order(len(cities))
            print(f"[Ranking] Mapping leer – TEST mit {len(led_order)} zufälligen "
                  f"LED-Positionen (Rang-Reihenfolge): {led_order}")
        elif fehlend:
            print(f"[Ranking] {len(fehlend)} Städte ohne LED-Mapping (übersprungen): {fehlend[:5]}")
        ok = _esp_request("/start", {
            "start_rgb": list(LICHT_STATE["start_rgb"]),
            "mid_rgb":   list(LICHT_STATE["mid_rgb"]),
            "end_rgb":   list(LICHT_STATE["end_rgb"]),
            "animation": LICHT_STATE["animation"],
            "leds":      led_order,
        })
        print(f"[Ranking] {len(led_order)} LEDs nach Rang angesteuert."
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
    start_lichterkette()
    _flash_text(neustart_btn, "Animation neu gestartet ✓")


def _on_licht_aus():
    stop_idle()
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


def _idle_state_changed(active):
    idle_btn.config(text="✕  Tag/Nacht beenden" if active else "🌙  Tag/Nacht (Idle)")


def _on_idle_toggle():
    if _idle_active:
        stop_idle()
        stop_lichterkette()      # Kette ausschalten
        status_label.config(text="Idle-Modus beendet.")
    else:
        start_idle()
        status_label.config(text="Idle-Modus: Tag/Nacht-Weltkarte aktiv.")


idle_f, idle_btn = _make_chip_button(
    licht_panel, "🌙  Tag/Nacht (Idle)", _on_idle_toggle)
idle_f.pack(anchor="nw", pady=(8, 0))

# UI über Zustandswechsel des Idle-Modus informieren (auch wenn er von
# außen – z. B. durch einen QR-Scan – beendet wird).
_idle_on_change = _idle_state_changed


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
         font=sub_font, fg=TEXT_DIM, bg=SURFACE).pack(anchor="w", padx=24, pady=(0, 14))

# Navigation
m_nav_row = tk.Frame(mapping_card, bg=SURFACE)
m_nav_row.pack(fill="x", padx=24, pady=(0, 6))

m_prev_f, _ = _make_chip_button(m_nav_row, "←  Zurück",  lambda: _mapping_navigate(-1))
m_prev_f.pack(side="left")

m_led_label = tk.Label(m_nav_row, text="LED 0 / 103", font=result_font,
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
    if city:
        m_current.config(text=f"Gespeichert: {city}", fg=GOLD)
    else:
        m_current.config(text="Noch nicht zugewiesen", fg=TEXT_FAINT)
    _ac_close()
    m_entry.delete(0, "end")
    m_entry.insert(0, city if city else "")
    m_save_label.config(text="")


def _mapping_speichern_aktuell():
    city = _resolve_city(m_entry.get())
    idx = _mapping_index[0]
    _mapping_data[idx] = city if city else None
    _speichere_mapping_eintrag(idx, city if city else None)
    # Eingabefeld auf den kanonischen Namen normalisieren (z. B. "Belize Stadt" -> "Belize-Stadt")
    if m_entry.get().strip() != (city or ""):
        m_entry.delete(0, "end")
        m_entry.insert(0, city or "")
    zugewiesen = sum(1 for v in _mapping_data.values() if v)
    m_progress.config(text=f"{zugewiesen} / {MAPPING_ANZ_LEDS} zugewiesen")
    m_current.config(
        text=f"Gespeichert: {city}" if city else "Noch nicht zugewiesen",
        fg=GOLD if city else TEXT_FAINT)
    m_save_label.config(text="✓")
    m_save_label.after(1400, lambda: m_save_label.config(text=""))


def _mapping_navigate(richtung):
    _mapping_speichern_aktuell()
    new_idx = (_mapping_index[0] + richtung) % MAPPING_ANZ_LEDS
    _mapping_index[0] = new_idx
    _stream_single_led(new_idx)
    _mapping_update_ui()


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
# Beim Verlassen kurz warten, damit ein Klick auf die Liste noch ankommt
m_entry.bind("<FocusOut>", lambda e: m_entry.after(150, _ac_close))


def _toggle_mapping_mode():
    if _mapping_aktiv[0]:
        _mapping_aktiv[0] = False
        mapping_card.pack_forget()
        mapping_btn.config(text="⊞  LED-Mapping")
        stop_lichterkette()
    else:
        stop_idle()
        _mapping_aktiv[0] = True
        mapping_card.pack(pady=(10, 0), padx=80, fill="x", after=control_card)
        mapping_btn.config(text="✕  Mapping beenden")
        _mapping_update_ui()
        _stream_single_led(_mapping_index[0])


last_session_id = None
detector = cv2.QRCodeDetector()


def on_qr_detected(data):
    global last_session_id

    # URL-Format: http://host/results/<uuid> → Host + UUID extrahieren
    base_url = None
    if data.startswith("http"):
        parsed = urllib.parse.urlparse(data)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        session_id = data.rstrip("/").split("/")[-1]
    else:
        session_id = data

    if session_id == last_session_id:
        return
    last_session_id = session_id

    stop_idle()   # Ranking hat Vorrang vor dem Idle-Modus

    if base_url:
        # Ranking vom Backend holen → Städte → LED-Positionen → ESP32
        start_ranking_animation(base_url, session_id)
        status_label.config(text=f"Session {session_id[:8]} …  ·  Ranking → Lichterkette")
    else:
        # Nur Session-ID ohne Host: kein Ranking abrufbar → generische Animation
        start_lichterkette()
        status_label.config(text=f"Session erkannt: {session_id[:8]} …  ·  Lichterkette gestartet.")


def update():
    _, frame = vid.read()
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
    cam_label.after(5, update)


update()

root.bind('<Escape>', lambda e: root.quit())
root.mainloop()
