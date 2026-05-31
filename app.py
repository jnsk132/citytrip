from flask import Flask, render_template, redirect, request, session, jsonify
from flask_session import Session
from functions import *
from database import init_db, create_session, save_swipe, save_result, complete_session, get_results, save_onboarding_answers, print_session_overview, get_all_sessions_overview, get_global_stats, get_all_liked_cities, get_swipes_for_session, get_session_id_by_code
import csv
import qrcode
import base64
from io import BytesIO

app = Flask(__name__)
init_db()

# --- KONFIGURATION DER SESSIONS ---
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# CSV Einmalig laden
with open("static/worldcities_ranked_german.csv", mode="r", encoding="utf-8") as file:
    reader = csv.reader(file)
    header = next(reader)
    city_list = list(reader)


@app.route("/")
def index():
    # Session für neuen Besucher initialisieren

    session["iteration"] = 0
    session["current_city_data"] = None
    session_id, short_code = create_session()
    session["session_id"] = session_id
    session["short_code"] = short_code

    session["user"] = {
        # Likes
        "liked_rankings": [],
        "liked_cities": [],
        "liked_countries": [],
        "liked_continents": [],
        "count_liked_cost": [0, 0, 0, 0],
        "count_liked_population": [0, 0, 0, 0],
        "count_liked_ocean_distance": [0, 0, 0],
        "count_liked_abs_latitude": [0, 0, 0],

        # Dislikes
        "disliked_rankings": [],
        "disliked_cities": [],
        "disliked_countries": [],
        "disliked_continents": [],
        "count_disliked_cost": [0, 0, 0, 0],
        "count_disliked_population": [0, 0, 0, 0],
        "count_disliked_ocean_distance": [0, 0, 0],
        "count_disliked_abs_latitude": [0, 0, 0],
    }

    return render_template("index.html")


@app.route("/onboarding")
def onboarding():
    if "user" not in session:
        return redirect("/")
    return render_template("onboarding.html")


@app.route("/onboarding/submit", methods=["POST"])
def onboarding_submit():
    if "user" not in session:
        return jsonify({"error": "no session"}), 401
    data = request.get_json()
    answers = data.get("answers", [])
    if answers:
        save_onboarding_answers(session["session_id"], answers)
    return jsonify({"ok": True})


@app.route("/quiz/init")
def quiz_init():
    if "user" not in session:
        return jsonify({"error": "no session"}), 401
    user = session["user"]
    session["iteration"] = 1
    city_row = get_next_city(user, city_list)
    session["current_city_data"] = city_row
    # Stadt N+1 vorausberechnen und in Session festschreiben
    next_row = get_next_city(user, city_list, extra_seen={city_row[0]})
    session["next_city_data"] = next_row
    return jsonify({
        "city_name": city_row[0],
        "country_name": city_row[1],
        "file_path": f"static/Alle_Stadt_Bilder/{city_row[11]}.jpg",
        "iteration": 1,
        "prefetch_path": f"static/Alle_Stadt_Bilder/{next_row[11]}.jpg",
    })


@app.route("/quiz")
def quiz():
    if "user" not in session:
        return redirect("/")

    user = session["user"]
    current_city_data = session.get("current_city_data")
    choice = request.args.get('choice')

    if choice:
        # Normale Navigation mit Antwort
        session["iteration"] += 1

        if current_city_data is not None:
            scoring(current_city_data, user, choice == "like")
            session["user"] = user

        if session["iteration"] > 20:
            return redirect("/results")

        city_row = get_next_city(user, city_list)
        session["current_city_data"] = city_row
    elif current_city_data is not None and session.get("iteration", 0) > 0:
        # Seite neu geladen – aktuelle Stadt aus Session wiederverwenden
        city_row = current_city_data
    else:
        # Erster Aufruf
        session["iteration"] = 1
        city_row = get_next_city(user, city_list)
        session["current_city_data"] = city_row

    return render_template("file.html",
                           city_name=city_row[0],
                           country_name=city_row[1],
                           file_path=f"static/Alle_Stadt_Bilder/{city_row[11]}.jpg",
                           iteration=session["iteration"])


@app.route("/results")
def results():
    # Prüfen ob Session existiert
    if "user" not in session:
        return redirect("/")

    user = session["user"]

    user_preferences = calculate_user_preference(user)
    cities, sd, all_cities = calculate_city_score(user, user_preferences, city_list)

    session_id = session["session_id"]

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

    result_url = f"{request.host_url}results/{session_id}"
    img = qrcode.make(result_url)

    buffered = BytesIO()
    img.save(buffered, format="JPEG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")

    personality = get_personality_type(user)

    global_stats = get_global_stats()
    rarity = None
    if global_stats["total_sessions"] > 1:
        liked_cities_global = get_all_liked_cities()
        rarity = calculate_rarity(user, city_list, liked_cities_global)
        rarity["total_sessions"] = global_stats["total_sessions"]

    return render_template("results.html", cities=cities, sd=sd, img_b64=img_str,
                           personality=personality, rarity=rarity,
                           session_id=session_id, short_code=session.get("short_code"))


@app.route("/quiz/next", methods=["POST"])
def quiz_next():
    if "user" not in session:
        return jsonify({"redirect": "/"})

    user = session["user"]
    current_city_data = session.get("current_city_data")

    data = request.get_json()
    choice = data.get("choice") if data else None

    session["iteration"] += 1

    if current_city_data is not None and choice:
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
        return jsonify({"redirect": "/results"})

    # Festgeschriebene Stadt N+1 aus Session holen (garantiert korrektes Prefetch)
    city_row = session.get("next_city_data") or get_next_city(user, city_list)
    session["current_city_data"] = city_row

    # Stadt N+2 vorausberechnen und festschreiben (basiert auf Ratings 1..N)
    new_next = get_next_city(user, city_list, extra_seen={city_row[0]})
    session["next_city_data"] = new_next

    return jsonify({
        "city_name": city_row[0],
        "country_name": city_row[1],
        "file_path": f"static/Alle_Stadt_Bilder/{city_row[11]}.jpg",
        "iteration": session["iteration"],
        "prefetch_path": f"static/Alle_Stadt_Bilder/{new_next[11]}.jpg",
    })


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
    user = {
        "liked_rankings": [], "liked_cities": [], "liked_countries": [], "liked_continents": [],
        "count_liked_cost": [0,0,0,0], "count_liked_population": [0,0,0,0],
        "count_liked_ocean_distance": [0,0,0], "count_liked_abs_latitude": [0,0,0],
        "disliked_rankings": [], "disliked_cities": [], "disliked_countries": [], "disliked_continents": [],
        "count_disliked_cost": [0,0,0,0], "count_disliked_population": [0,0,0,0],
        "count_disliked_ocean_distance": [0,0,0], "count_disliked_abs_latitude": [0,0,0],
    }
    for _, city_name, _, _, choice in swipes:
        city_data = city_map.get(city_name)
        if city_data:
            scoring(city_data, user, choice == "like")

    personality = get_personality_type(user)

    global_stats = get_global_stats()
    rarity = None
    if global_stats["total_sessions"] > 1:
        liked_cities_global = get_all_liked_cities()
        rarity = calculate_rarity(user, city_list, liked_cities_global)
        rarity["total_sessions"] = global_stats["total_sessions"]

    return render_template("results.html", cities=cities, sd=[], img_b64=None,
                           personality=personality, rarity=rarity)


@app.route("/compare/join", methods=["POST"])
def compare_join():
    if "session_id" not in session:
        return redirect("/")
    friend_code = request.form.get("friend_code", "").strip().upper()
    friend_id = get_session_id_by_code(friend_code)
    if not friend_id:
        return redirect(f"/results?code_error=1")
    return redirect(f"/compare/{session['session_id']}/{friend_id}")


@app.route("/compare/<id1>/<id2>")
def compare(id1, id2):
    def load_session(sid):
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

    cities1 = load_session(id1)
    cities2 = load_session(id2)
    if not cities1 or not cities2:
        return redirect("/")

    names1 = {c[0] for c in cities1}
    names2 = {c[0] for c in cities2}
    common = names1 & names2

    return render_template("compare.html", cities1=cities1, cities2=cities2, common=common)


@app.route("/admin")
def admin():
    sessions = get_all_sessions_overview()
    return render_template("admin.html", sessions=sessions)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
