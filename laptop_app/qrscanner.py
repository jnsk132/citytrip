import cv2
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageDraw
import sys
import os
import csv
import time
import threading

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None  # Lichterkette optional – App läuft auch ohne pyserial

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from functions import build_user_from_swipes, calculate_user_preference, calculate_city_score
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "static/worldcities_ranked_german.csv")
DB_PATH = os.path.join(BASE_DIR, "database.db")

with open(CSV_PATH, mode="r", encoding="utf-8") as file:
    reader = csv.reader(file)
    next(reader)
    city_list = list(reader)

city_map = {city[0]: city for city in city_list}


# ════════════════════════════════════════════════════════════════
#  ESP32-Lichterkette: MicroPython-Skript beim Scan starten
#  (Laptop streamt lichterkette.py über die serielle REPL)
# ════════════════════════════════════════════════════════════════
ESP32_PORT = None        # None = Auto-Erkennung, sonst z. B. "/dev/cu.usbmodem1101"
ESP32_BAUD = 115200      # bei nativem USB-CDC egal, pyserial braucht aber einen Wert
LICHT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lichterkette.py")
# USB-Kennungen typischer ESP32-Boards (UART-Chips + native USB):
_ESP32_HINTS = ("cp210", "ch340", "ch910", "silicon labs", "wch",
                "usb serial", "usbserial", "slab", "uart",
                "espressif", "jtag", "usbmodem")

_esp = None
_esp_lock = threading.Lock()


def _find_esp32_port():
    if ESP32_PORT:
        return ESP32_PORT
    if serial is None:
        return None
    for p in serial.tools.list_ports.comports():
        haystack = f"{p.device} {p.description} {p.manufacturer}".lower()
        if any(h in haystack for h in _ESP32_HINTS):
            return p.device
    return None


def _ensure_esp32():
    """Liefert eine offene serielle Verbindung oder None (lazy reconnect)."""
    global _esp
    if serial is None:
        return None
    if _esp is not None and _esp.is_open:
        return _esp
    port = _find_esp32_port()
    if not port:
        print("[ESP32] Kein Port gefunden – Lichterkette wird übersprungen.")
        return None
    try:
        _esp = serial.Serial(port, ESP32_BAUD, timeout=1)
        # ESP32-C3/S3 mit USB-Serial-JTAG sendet/empfängt nur bei aktivem DTR.
        _esp.dtr = True
        _esp.rts = False
        time.sleep(0.2)
        print(f"[ESP32] Verbunden über {port}")
    except serial.SerialException as e:
        print(f"[ESP32] Verbindung fehlgeschlagen: {e}")
        _esp = None
    return _esp


def _stream_script(ser, code):
    """Stoppt ein laufendes Programm und startet das Skript über Paste-Mode."""
    ser.write(b"\r\x03\x03")          # 2× Ctrl-C: laufendes Programm beenden
    time.sleep(0.15)
    ser.reset_input_buffer()
    ser.write(b"\x05")                # Ctrl-E: Paste-Modus
    time.sleep(0.05)
    ser.write(code.encode("utf-8"))
    ser.write(b"\x04")               # Ctrl-D: pasted Block ausführen
    ser.flush()


def start_lichterkette():
    """Startet lichterkette.py auf dem ESP32 (im Hintergrund, blockiert die UI nicht)."""
    def worker():
        with _esp_lock:
            ser = _ensure_esp32()
            if ser is None:
                return
            try:
                with open(LICHT_SCRIPT, "r", encoding="utf-8") as f:
                    code = f.read()
                _stream_script(ser, code)
                print("[ESP32] Lichterkette gestartet.")
            except (OSError, Exception) as e:  # serial-Fehler nicht in die UI durchschlagen
                print(f"[ESP32] Start fehlgeschlagen: {e}")
                global _esp
                try:
                    if _esp:
                        _esp.close()
                finally:
                    _esp = None
    threading.Thread(target=worker, daemon=True).start()


def get_swipes(session_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT iteration, city, country, continent, choice FROM swipes WHERE session_id = ? ORDER BY iteration",
        (session_id,)
    )
    rows = cur.fetchall()
    con.close()
    return rows


def calculate_ranking(session_id):
    """
    Holt Swipes für die Session, berechnet das Ranking und gibt ein Array zurück.
    Rückgabe: Liste von [stadtname, land, score] sortiert nach Score (bestes zuerst).
    """
    swipes = get_swipes(session_id)
    if not swipes:
        print(f"Keine Swipes für Session {session_id[:8]}... gefunden.")
        return []

    user = build_user_from_swipes(swipes, city_map)
    user_preferences = calculate_user_preference(user)
    _, scored_cities = calculate_city_score(user, user_preferences, city_list)

    ranked = [[city[0], city[1], round(score, 4)] for score, city in scored_cities]
    return ranked


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

title_label = tk.Label(main, text="", font=title_font, fg=TEXT, bg=BG)
title_label.pack(pady=(8, 18))

result_frame = tk.Frame(main, bg=BG)
result_frame.pack(fill="both", expand=True, padx=80)

# ── Kamera-Vorschau (rechts oben, mit Gradient-Rahmen) ──────────
cam_frame = tk.Frame(root, bg=BRAND_MID, bd=0)
cam_frame.place(relx=1.0, x=-24, y=24, anchor="ne")
cam_label = tk.Label(cam_frame, bg=BG, bd=0)
cam_label.pack(padx=2, pady=2)

last_session_id = None
detector = cv2.QRCodeDetector()


def show_ranking(ranked_cities):
    for widget in result_frame.winfo_children():
        widget.destroy()

    medals = ["🥇", "🥈", "🥉"]
    accents = [GOLD, SILVER, BRONZE]
    for i, (city, country, score) in enumerate(ranked_cities[:10]):
        top3 = i < 3
        accent = accents[i] if top3 else BRAND_MID
        rank_text = medals[i] if top3 else f"{i + 1}"

        # Glass-Karte
        card = tk.Frame(result_frame, bg=SURFACE, highlightthickness=1,
                        highlightbackground=BORDER)
        card.pack(fill="x", pady=5)

        # Farbiger Akzentbalken links (Sunset/Medaille)
        tk.Frame(card, bg=accent, width=4).pack(side="left", fill="y")

        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(side="left", fill="x", expand=True, padx=16, pady=11)

        tk.Label(inner, text=rank_text, font=rank_font, fg=accent,
                 bg=SURFACE, width=3, anchor="w").pack(side="left")
        tk.Label(inner, text=city, font=result_font, fg=TEXT,
                 bg=SURFACE).pack(side="left", padx=(6, 8))
        tk.Label(inner, text=country, font=sub_font, fg=TEXT_DIM,
                 bg=SURFACE).pack(side="left")

        # Score-Chip rechts
        tk.Label(inner, text=f"Score {score}", font=score_font, fg=TEXT_FAINT,
                 bg=SURFACE_2, padx=12, pady=4).pack(side="right")


def on_qr_detected(data):
    global last_session_id

    # URL-Format: http://host/results/<uuid> → UUID extrahieren
    if data.startswith("http"):
        session_id = data.rstrip("/").split("/")[-1]
    else:
        session_id = data

    if session_id == last_session_id:
        return
    last_session_id = session_id

    start_lichterkette()  # MicroPython-Skript auf dem ESP32 starten

    status_label.config(text=f"Session erkannt: {session_id[:8]} …  ·  Berechne Ranking …")
    title_label.config(text="")
    for widget in result_frame.winfo_children():
        widget.destroy()
    root.update()

    ranked = calculate_ranking(session_id)

    if not ranked:
        status_label.config(text="Keine Daten für diese Session gefunden.")
        return

    print("=== RANKING ARRAY ===")
    print(ranked)
    print("=====================")

    status_label.config(text=f"Ranking berechnet  ·  {len(ranked)} Städte")
    title_label.config(text="Deine Top-Empfehlungen")
    show_ranking(ranked)


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

if _esp is not None:
    _esp.close()
