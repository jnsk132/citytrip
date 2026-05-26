from flask import Flask, render_template, redirect, request, session, jsonify
from flask_session import Session
from functions import *
from database import init_db, create_session, save_swipe, save_result, complete_session, get_results, save_onboarding_answers, print_session_overview, get_all_sessions_overview
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
    session["session_id"] = create_session()

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
    prefetch_row = get_next_city(user, city_list, extra_seen={city_row[0]})
    return jsonify({
        "city_name": city_row[0],
        "country_name": city_row[1],
        "file_path": f"static/Alle_Stadt_Bilder/{city_row[11]}.jpg",
        "iteration": 1,
        "prefetch_path": f"static/Alle_Stadt_Bilder/{prefetch_row[11]}.jpg",
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

    img = qrcode.make(session_id)

    buffered = BytesIO()
    img.save(buffered, format="JPEG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")

    return render_template("results.html", cities=cities, sd=sd, img_b64=img_str)


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

    city_row = get_next_city(user, city_list)
    session["current_city_data"] = city_row
    prefetch_row = get_next_city(user, city_list, extra_seen={city_row[0]})

    return jsonify({
        "city_name": city_row[0],
        "country_name": city_row[1],
        "file_path": f"static/Alle_Stadt_Bilder/{city_row[11]}.jpg",
        "iteration": session["iteration"],
        "prefetch_path": f"static/Alle_Stadt_Bilder/{prefetch_row[11]}.jpg",
    })


@app.route("/results/<session_id>")
def results_by_id(session_id):
    rows = get_results(session_id)
    if not rows:
        return redirect("/")
    # rows: [(rank, city, country, score), ...]
    cities = [[row[1], row[2]] for row in rows]
    return render_template("results.html", cities=cities, sd=[], img_b64=None)


@app.route("/admin")
def admin():
    sessions = get_all_sessions_overview()
    return render_template("admin.html", sessions=sessions)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
