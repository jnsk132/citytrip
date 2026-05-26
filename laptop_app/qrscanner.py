import cv2
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk
import sys
import os
import csv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from functions import scoring, calculate_user_preference, calculate_city_score
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "static/worldcities_ranked_german.csv")
DB_PATH = os.path.join(BASE_DIR, "database.db")

with open(CSV_PATH, mode="r", encoding="utf-8") as file:
    reader = csv.reader(file)
    next(reader)
    city_list = list(reader)

city_map = {city[0]: city for city in city_list}


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


def build_user_from_swipes(swipes):
    user = {
        "liked_rankings": [], "liked_cities": [], "liked_countries": [], "liked_continents": [],
        "count_liked_cost": [0, 0, 0, 0], "count_liked_population": [0, 0, 0, 0],
        "count_liked_ocean_distance": [0, 0, 0], "count_liked_abs_latitude": [0, 0, 0],
        "disliked_rankings": [], "disliked_cities": [], "disliked_countries": [], "disliked_continents": [],
        "count_disliked_cost": [0, 0, 0, 0], "count_disliked_population": [0, 0, 0, 0],
        "count_disliked_ocean_distance": [0, 0, 0], "count_disliked_abs_latitude": [0, 0, 0],
    }
    for _, city_name, _, _, choice in swipes:
        city_data = city_map.get(city_name)
        if city_data:
            scoring(city_data, user, choice == "like")
    return user


def calculate_ranking(session_id):
    """
    Holt Swipes für die Session, berechnet das Ranking und gibt ein Array zurück.
    Rückgabe: Liste von [stadtname, land, score] sortiert nach Score (bestes zuerst).
    """
    swipes = get_swipes(session_id)
    if not swipes:
        print(f"Keine Swipes für Session {session_id[:8]}... gefunden.")
        return []

    user = build_user_from_swipes(swipes)
    user_preferences = calculate_user_preference(user)
    _, _, scored_cities = calculate_city_score(user, user_preferences, city_list)

    ranked = [[city[0], city[1], round(score, 4)] for score, city in scored_cities]
    return ranked


# --- UI Setup ---

root = tk.Tk()
root.title("CityTrip QR-Scanner")
screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()
root.geometry(f"{screen_width}x{screen_height}+0+0")
root.configure(bg="#1a1a2e")

vid = cv2.VideoCapture(0)
vid.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
vid.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

CAM_WIDTH = 320
CAM_HEIGHT = 180

cam_label = tk.Label(root, bg="#1a1a2e")
cam_label.place(x=screen_width - CAM_WIDTH, y=0)

title_font = tkfont.Font(family="Helvetica", size=20, weight="bold")
result_font = tkfont.Font(family="Helvetica", size=14)
status_font = tkfont.Font(family="Helvetica", size=12)

status_label = tk.Label(root, text="QR-Code vor die Kamera halten ...",
                        font=status_font, fg="#aaaaaa", bg="#1a1a2e")
status_label.pack(pady=(40, 10))

title_label = tk.Label(root, text="", font=title_font, fg="#ffffff", bg="#1a1a2e")
title_label.pack(pady=(0, 20))

result_frame = tk.Frame(root, bg="#1a1a2e")
result_frame.pack(fill="both", expand=True, padx=60)

cam_label.lift()

last_session_id = None
detector = cv2.QRCodeDetector()


def show_ranking(ranked_cities):
    for widget in result_frame.winfo_children():
        widget.destroy()

    medals = ["🥇", "🥈", "🥉"]
    for i, (city, country, score) in enumerate(ranked_cities[:10]):
        medal = medals[i] if i < 3 else f"  {i+1}."
        color = "#FFD700" if i == 0 else "#C0C0C0" if i == 1 else "#CD7F32" if i == 2 else "#cccccc"
        row = tk.Frame(result_frame, bg="#1a1a2e")
        row.pack(anchor="w", pady=2)
        tk.Label(row, text=f"{medal}  {city}, {country}",
                 font=result_font, fg=color, bg="#1a1a2e").pack(side="left", padx=10)
        tk.Label(row, text=f"({score})",
                 font=result_font, fg="#888888", bg="#1a1a2e").pack(side="left")


def on_qr_detected(session_id):
    global last_session_id
    if session_id == last_session_id:
        return
    last_session_id = session_id

    status_label.config(text=f"Session erkannt: {session_id[:8]}...  –  Berechne Ranking ...")
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

    status_label.config(text=f"Ranking berechnet – {len(ranked)} Städte")
    title_label.config(text="Top Empfehlungen")
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
