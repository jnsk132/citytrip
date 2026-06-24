import base64
import csv
import os
from functools import wraps
from io import BytesIO

import qrcode
from flask import Flask, Response, jsonify, redirect, render_template, request, session
from flask_session import Session

from functions import *
from database import (
    init_db, create_session, save_swipe, save_result, complete_session,
    get_results, print_session_overview, get_all_sessions_overview,
    get_completed_session_count, get_all_liked_cities, get_swipes_for_session,
    get_session_id_by_code, get_short_code, get_city_result_stats,
    get_city_shown_counts, update_city_global_stats, get_city_avg_ranks,
    get_all_completed_swipes, delete_session, delete_all_sessions,
    is_session_completed,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Passwort für /admin – für die Messe per Umgebungsvariable setzen!
ADMIN_PASSWORD = os.environ.get("CITYTRIP_ADMIN_PASSWORD", "citytrip2026")

app = Flask(__name__)
init_db()

# --- KONFIGURATION DER SESSIONS ---
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# CSV Einmalig laden (absoluter Pfad, unabhängig vom Startverzeichnis)
with open(os.path.join(BASE_DIR, "static", "worldcities_ranked_german.csv"),
          mode="r", encoding="utf-8") as file:
    reader = csv.reader(file)
    header = next(reader)
    city_list = list(reader)

# Globale Zeig-Häufigkeit für ausgeglichene Stadtauswahl
city_shown_counts = get_city_shown_counts()


def require_admin(f):
    """HTTP Basic Auth für die Admin-Seiten (Nutzername egal, nur Passwort zählt)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.password != ADMIN_PASSWORD:
            return Response("Login erforderlich", 401,
                            {"WWW-Authenticate": 'Basic realm="CityTrip Admin"'})
        return f(*args, **kwargs)
    return wrapper


def _ensure_db_session():
    """DB-Session erst beim Quiz-Start anlegen – ein Refresh der Startseite
    soll keine Karteileichen in der Datenbank erzeugen."""
    if not session.get("session_id"):
        session_id, short_code = create_session()
        session["session_id"] = session_id
        session["short_code"] = short_code


@app.route("/")
def index():
    # Session für neuen Besucher initialisieren
    session["iteration"] = 0
    session["current_city_data"] = None
    session["next_city_data"] = None
    session["session_id"] = None
    session["short_code"] = None
    session["user"] = new_user()

    return render_template("index.html")


@app.route("/quiz")
def quiz():
    if "user" not in session:
        return redirect("/")

    user = session["user"]
    current_city_data = session.get("current_city_data")
    choice = request.args.get('choice')

    if choice in ("like", "dislike"):
        # Fallback-Navigation (z.B. bei Netzwerkfehler im fetch)
        _ensure_db_session()
        session["iteration"] += 1

        if current_city_data is not None:
            scoring(current_city_data, user, choice == "like")
            session["user"] = user
            save_swipe(
                session_id=session["session_id"],
                iteration=session["iteration"] - 1,
                city=current_city_data[0],
                country=current_city_data[1],
                continent=current_city_data[2],
                choice=choice
            )

        if session["iteration"] > 20:
            return redirect("/results")

        city_row = get_next_city(user, city_list, city_shown_counts=city_shown_counts)
        session["current_city_data"] = city_row
        # Prefetch-Kette neu aufsetzen, damit /quiz/next keine veraltete Stadt liefert
        session["next_city_data"] = get_next_city(user, city_list, extra_seen={city_row[0]},
                                                  city_shown_counts=city_shown_counts)
    elif current_city_data is not None and session.get("iteration", 0) > 0:
        # Seite neu geladen – aktuelle Stadt aus Session wiederverwenden
        city_row = current_city_data
    else:
        # Erster Aufruf
        _ensure_db_session()
        session["iteration"] = 1
        city_row = get_next_city(user, city_list, city_shown_counts=city_shown_counts)
        session["current_city_data"] = city_row
        session["next_city_data"] = get_next_city(user, city_list, extra_seen={city_row[0]},
                                                  city_shown_counts=city_shown_counts)

    next_city = session.get("next_city_data")
    prefetch_path = (f"static/Alle_Stadt_Bilder_neu/{next_city[11]}.jpg"
                     if next_city else None)

    return render_template("file.html",
                           city_name=city_row[0],
                           country_name=city_row[1],
                           file_path=f"static/Alle_Stadt_Bilder_neu/{city_row[11]}.jpg",
                           prefetch_path=prefetch_path,
                           iteration=session["iteration"])


@app.route("/quiz/next", methods=["POST"])
def quiz_next():
    if "user" not in session:
        return jsonify({"redirect": "/"})

    user = session["user"]
    current_city_data = session.get("current_city_data")
    if current_city_data is None:
        return jsonify({"redirect": "/"})

    data = request.get_json(silent=True)
    choice = data.get("choice") if data else None

    if choice not in ("like", "dislike"):
        # Ungültige Anfrage – aktuelle Stadt erneut ausliefern, Fortschritt unverändert
        return jsonify({
            "city_name": current_city_data[0],
            "country_name": current_city_data[1],
            "file_path": f"static/Alle_Stadt_Bilder_neu/{current_city_data[11]}.jpg",
            "iteration": session.get("iteration", 1),
        })

    _ensure_db_session()
    scoring(current_city_data, user, choice == "like")
    session["user"] = user
    save_swipe(
        session_id=session["session_id"],
        iteration=session["iteration"],
        city=current_city_data[0],
        country=current_city_data[1],
        continent=current_city_data[2],
        choice=choice
    )
    session["iteration"] += 1

    if session["iteration"] > 20:
        return jsonify({"redirect": "/results"})

    # Festgeschriebene Stadt N+1 aus Session holen (garantiert korrektes Prefetch)
    city_row = session.get("next_city_data") or get_next_city(user, city_list, city_shown_counts=city_shown_counts)
    session["current_city_data"] = city_row

    # Stadt N+2 vorausberechnen und festschreiben (basiert auf Ratings 1..N)
    new_next = get_next_city(user, city_list, extra_seen={city_row[0]}, city_shown_counts=city_shown_counts)
    session["next_city_data"] = new_next

    return jsonify({
        "city_name": city_row[0],
        "country_name": city_row[1],
        "file_path": f"static/Alle_Stadt_Bilder_neu/{city_row[11]}.jpg",
        "iteration": session["iteration"],
        "prefetch_path": f"static/Alle_Stadt_Bilder_neu/{new_next[11]}.jpg" if new_next else None,
    })


@app.route("/results")
def results():
    # Prüfen ob Session existiert
    if "user" not in session or not session.get("session_id"):
        return redirect("/")

    user = session["user"]
    session_id = session["session_id"]

    user_preferences = calculate_user_preference(user)
    cities, all_cities = calculate_city_score(user, user_preferences, city_list)

    # Vollständige Rangliste (inkl. gesehene Städte) für globale Stats
    full_scored = score_all_cities(user, user_preferences, city_list)

    # Globale Rang-Statistik lesen, BEVOR die eigene Session eingerechnet wird
    city_avg_ranks = get_city_avg_ranks()

    # Nur beim ersten Abschluss speichern – ein Refresh der Seite darf
    # Ergebnisse und globale Statistiken nicht doppelt zählen
    if not is_session_completed(session_id):
        for rank, item in enumerate(cities, start=1):
            save_result(
                session_id=session_id,
                rank=rank,
                city=item[0],
                country=item[1],
                score=all_cities[rank - 1][0]
            )
        complete_session(session_id)
        print_session_overview(session_id)
        update_city_global_stats(full_scored)
        global city_shown_counts
        city_shown_counts = get_city_shown_counts()

    result_url = f"{request.host_url}results/{session_id}"
    img = qrcode.make(result_url)

    buffered = BytesIO()
    img.save(buffered, format="JPEG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")

    personality = get_personality_type(user)

    # Seltenheit nur gegen die ANDEREN Spieler vergleichen
    other_sessions = get_completed_session_count(exclude_session_id=session_id)
    rarity = None
    if other_sessions > 0:
        liked_cities_global = get_all_liked_cities(exclude_session_id=session_id)
        rarity = calculate_rarity(user, city_list, liked_cities_global)
        rarity["total_sessions"] = other_sessions

    hidden_gem = find_hidden_gem(user, full_scored, city_avg_ranks)

    all_swipes   = get_all_completed_swipes()
    travel_buddy = find_travel_buddy(user, session_id, city_list, all_swipes)

    todo_activities = get_todo_activities(user, cities)

    challenge = get_ranking_challenge(user, user_preferences, city_list)

    return render_template("results.html", cities=cities, img_b64=img_str,
                           personality=personality, rarity=rarity,
                           session_id=session_id, short_code=session.get("short_code"),
                           hidden_gem=hidden_gem, todo_activities=todo_activities,
                           travel_buddy=travel_buddy, challenge=challenge)


@app.route("/results/<session_id>")
def results_by_id(session_id):
    rows = get_results(session_id)
    if not rows:
        return redirect("/")

    # Volle Stadtdaten aus CSV holen (Bild, Beschreibung etc.)
    city_map = {city[0]: city for city in city_list}
    used_descriptions = set()
    cities = []
    for _, city_name, country, score in rows:
        city_data = city_map.get(city_name)
        if city_data:
            city_data = list(city_data)
            city_data.append(get_city_description(city_data, used_descriptions))
        else:
            city_data = [city_name, country] + [''] * 16 + ['']
        cities.append(city_data)

    # User-Profil aus gespeicherten Swipes rekonstruieren
    swipes = get_swipes_for_session(session_id)
    user = build_user_from_swipes(swipes, city_map)

    personality = get_personality_type(user)

    other_sessions = get_completed_session_count(exclude_session_id=session_id)
    rarity = None
    if other_sessions > 0:
        liked_cities_global = get_all_liked_cities(exclude_session_id=session_id)
        rarity = calculate_rarity(user, city_list, liked_cities_global)
        rarity["total_sessions"] = other_sessions

    user_preferences = calculate_user_preference(user)
    full_scored = score_all_cities(user, user_preferences, city_list)
    city_avg_ranks = get_city_avg_ranks()
    hidden_gem = find_hidden_gem(user, full_scored, city_avg_ranks)

    all_swipes   = get_all_completed_swipes()
    travel_buddy = find_travel_buddy(user, session_id, city_list, all_swipes)

    todo_activities = get_todo_activities(user, cities)

    challenge = get_ranking_challenge(user, user_preferences, city_list)

    return render_template("results.html", cities=cities, img_b64=None,
                           personality=personality, rarity=rarity, hidden_gem=hidden_gem,
                           todo_activities=todo_activities, travel_buddy=travel_buddy,
                           challenge=challenge)


@app.route("/api/led-ranking/<session_id>")
def api_led_ranking(session_id):
    """Ranking einer Session als JSON – für die ESP32-Lichterkette am Scanner.

    Der Laptop-Scanner ruft das beim QR-Scan ab, übersetzt die Städte über
    led_mapping.json in LED-Positionen und schickt sie an den ESP32.
    """
    rows = get_results(session_id)
    if not rows:
        return jsonify({"error": "unknown session"}), 404
    ranking = [
        {"rank": rank, "city": city, "country": country, "score": score}
        for rank, city, country, score in rows
    ]
    return jsonify({"session_id": session_id, "ranking": ranking})


def _load_session_cities(sid):
    rows = get_results(sid)
    if not rows:
        return None
    city_map = {city[0]: city for city in city_list}
    used_descriptions = set()
    cities = []
    for _, city_name, country, score in rows:
        city_data = city_map.get(city_name)
        if city_data:
            city_data = list(city_data)
            city_data.append(get_city_description(city_data, used_descriptions))
        else:
            city_data = [city_name, country] + [''] * 16 + ['']
        cities.append(city_data)
    return cities


@app.route("/compare/join", methods=["POST"])
def compare_join():
    if not session.get("session_id"):
        return redirect("/")
    friend_code = request.form.get("friend_code", "").strip().upper()
    friend_id = get_session_id_by_code(friend_code)
    if not friend_id:
        return redirect("/results?code_error=1")
    ids = f"{session['session_id']},{friend_id}"
    return redirect(f"/compare?ids={ids}")


@app.route("/compare/add", methods=["POST"])
def compare_add():
    ids_param = request.form.get("ids", "")
    friend_code = request.form.get("friend_code", "").strip().upper()
    friend_id = get_session_id_by_code(friend_code)
    if not friend_id:
        return redirect(f"/compare?ids={ids_param}&code_error=1")
    existing = [i.strip() for i in ids_param.split(",") if i.strip()]
    if friend_id not in existing:
        existing.append(friend_id)
    return redirect(f"/compare?ids={','.join(existing)}")


@app.route("/compare/<id1>/<id2>")
def compare_legacy(id1, id2):
    return redirect(f"/compare?ids={id1},{id2}")


@app.route("/compare")
def compare_group():
    ids_param = request.args.get("ids", "")
    code_error = request.args.get("code_error")
    ids = [i.strip() for i in ids_param.split(",") if i.strip()]
    if len(ids) < 2:
        return redirect("/")

    sessions = []
    for sid in ids:
        cities = _load_session_cities(sid)
        if cities:
            code = get_short_code(sid) or "????"
            sessions.append({"id": sid, "short_code": code, "cities": cities})

    if len(sessions) < 2:
        return redirect("/")

    common = set(c[0] for c in sessions[0]["cities"])
    for s in sessions[1:]:
        common &= set(c[0] for c in s["cities"])

    # Gruppen-Top-3 per Durchschnittsrang berechnen
    city_map = {city[0]: city for city in city_list}
    per_session_ranks = []
    for s in sessions:
        swipes = get_swipes_for_session(s["id"])
        user = build_user_from_swipes(swipes, city_map)
        prefs = calculate_user_preference(user)
        scored = score_all_cities(user, prefs, city_list)
        ranks = {city[0]: rank for rank, (_, city) in enumerate(scored)}
        per_session_ranks.append(ranks)

    city_avg_ranks = {}
    for city in city_list:
        name = city[0]
        all_ranks = [r[name] for r in per_session_ranks if name in r]
        if len(all_ranks) == len(per_session_ranks):
            city_avg_ranks[name] = sum(all_ranks) / len(all_ranks)

    sorted_by_rank = sorted(city_avg_ranks.items(), key=lambda x: x[1])
    used_descriptions = set()
    group_top3 = []
    for name, _ in sorted_by_rank[:3]:
        city_data = list(city_map[name])
        city_data.append(get_city_description(city_data, used_descriptions))
        group_top3.append(city_data)

    return render_template("compare.html", sessions=sessions, common=common,
                           ids_param=ids_param, code_error=code_error,
                           my_short_code=session.get("short_code"),
                           group_top3=group_top3)


@app.route("/admin")
@require_admin
def admin():
    sessions = get_all_sessions_overview()
    city_stats = get_city_result_stats()
    shown_counts = get_city_shown_counts()
    city_shown_list = sorted(
        [{"city": c[0], "country": c[1], "count": shown_counts.get(c[0], 0)} for c in city_list],
        key=lambda x: x["count"]
    )
    return render_template("admin.html", sessions=sessions, city_stats=city_stats, city_shown_list=city_shown_list)


@app.route("/admin/delete/<session_id>", methods=["POST"])
@require_admin
def admin_delete_session(session_id):
    delete_session(session_id)
    return redirect("/admin")


@app.route("/admin/delete-all", methods=["POST"])
@require_admin
def admin_delete_all():
    delete_all_sessions()
    return redirect("/admin")


if __name__ == '__main__':
    # debug=False: Der Werkzeug-Debugger wäre im offenen Messe-Netz Remote Code Execution
    app.run(host='0.0.0.0', port=5000, debug=True)
