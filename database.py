import sqlite3
import uuid
import datetime
import random
import string

_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # kein O/I/0/1 (Verwechslungsgefahr)

def _generate_short_code(cur):
    for _ in range(50):
        code = ''.join(random.choices(_CODE_CHARS, k=4))
        cur.execute("SELECT 1 FROM sessions WHERE short_code = ?", (code,))
        if not cur.fetchone():
            return code
    raise RuntimeError("Kein freier short_code gefunden")

DB_PATH = "database.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    con = get_connection()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id         TEXT PRIMARY KEY,
            created_at TIMESTAMP,
            completed  INTEGER DEFAULT 0,
            short_code TEXT UNIQUE
        )
    """)
    try:
        cur.execute("ALTER TABLE sessions ADD COLUMN short_code TEXT")
    except Exception:
        pass
    cur.execute("""
        CREATE TABLE IF NOT EXISTS onboarding_answers (
            id          TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL REFERENCES sessions(id),
            question_nr INTEGER NOT NULL,
            question    TEXT NOT NULL,
            answer      TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS swipes (
            id         TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            iteration  INTEGER NOT NULL,
            city       TEXT NOT NULL,
            country    TEXT NOT NULL,
            continent  TEXT NOT NULL,
            choice     TEXT NOT NULL CHECK(choice IN ('like', 'dislike')),
            created_at TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id         TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            rank       INTEGER NOT NULL CHECK(rank IN (1, 2, 3)),
            city       TEXT NOT NULL,
            country    TEXT NOT NULL,
            score      REAL NOT NULL
        )
    """)

    con.commit()
    con.close()


def save_onboarding_answers(session_id, answers):
    """answers: list of dicts with keys question_nr, question, answer"""
    con = get_connection()
    cur = con.cursor()
    for a in answers:
        cur.execute(
            "INSERT INTO onboarding_answers (id, session_id, question_nr, question, answer) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), session_id, a["question_nr"], a["question"], a["answer"])
        )
    con.commit()
    con.close()


def print_session_overview(session_id):
    con = get_connection()
    cur = con.cursor()

    cur.execute("SELECT id, created_at, completed FROM sessions WHERE id = ?", (session_id,))
    session = cur.fetchone()

    cur.execute(
        "SELECT question_nr, question, answer FROM onboarding_answers WHERE session_id = ? ORDER BY question_nr",
        (session_id,)
    )
    onboarding = cur.fetchall()

    cur.execute(
        "SELECT iteration, city, country, choice FROM swipes WHERE session_id = ? ORDER BY iteration",
        (session_id,)
    )
    swipes = cur.fetchall()

    cur.execute(
        "SELECT rank, city, country, score FROM results WHERE session_id = ? ORDER BY rank",
        (session_id,)
    )
    results = cur.fetchall()

    con.close()

    w = 60
    print("\n" + "═" * w)
    print(f"  SESSION: {session[0][:8]}...  |  {session[1]}  |  {'✓ abgeschlossen' if session[2] else '○ offen'}")
    print("═" * w)

    if onboarding:
        print("\n  ONBOARDING-ANTWORTEN")
        print("  " + "─" * (w - 2))
        for nr, question, answer in onboarding:
            q_short = question if len(question) <= 38 else question[:35] + "..."
            print(f"  {nr}. {q_short}")
            print(f"     → {answer}")
    else:
        print("\n  Keine Onboarding-Antworten gespeichert.")

    if swipes:
        likes    = sum(1 for s in swipes if s[3] == "like")
        dislikes = len(swipes) - likes
        print(f"\n  SWIPES ({len(swipes)})   ❤ {likes} likes   ✗ {dislikes} dislikes")
        print("  " + "─" * (w - 2))
        for it, city, country, choice in swipes:
            icon = "❤" if choice == "like" else "✗"
            print(f"  {it:>2}. {city}, {country:<20} {icon}")

    if results:
        medals = ["🥇", "🥈", "🥉"]
        print(f"\n  TOP 3 ERGEBNISSE")
        print("  " + "─" * (w - 2))
        for rank, city, country, score in results:
            print(f"  {medals[rank-1]}  {city}, {country:<22}  Score: {score:.2f}")

    print("\n" + "═" * w + "\n")


def create_session():
    session_id = str(uuid.uuid4())
    created_at = datetime.datetime.now()

    con = get_connection()
    cur = con.cursor()
    short_code = _generate_short_code(cur)
    cur.execute(
        "INSERT INTO sessions (id, created_at, completed, short_code) VALUES (?, ?, ?, ?)",
        (session_id, created_at, 0, short_code)
    )
    con.commit()
    con.close()

    return session_id, short_code


def get_session_id_by_code(short_code):
    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT id FROM sessions WHERE short_code = ?", (short_code.upper(),))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None


def save_swipe(session_id, iteration, city, country, continent, choice):
    swipe_id = str(uuid.uuid4())
    created_at = datetime.datetime.now()

    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO swipes (id, session_id, iteration, city, country, continent, choice, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (swipe_id, session_id, iteration, city, country, continent, choice, created_at)
    )
    con.commit()
    con.close()


def save_result(session_id, rank, city, country, score):
    result_id = str(uuid.uuid4())

    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO results (id, session_id, rank, city, country, score) VALUES (?, ?, ?, ?, ?, ?)",
        (result_id, session_id, rank, city, country, score)
    )
    con.commit()
    con.close()


def get_results(session_id):
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT rank, city, country, score FROM results WHERE session_id = ? ORDER BY rank",
        (session_id,)
    )
    rows = cur.fetchall()
    con.close()
    return rows


def get_all_sessions_overview():
    """Returns all sessions with their onboarding answers, swipes and results."""
    con = get_connection()
    cur = con.cursor()

    cur.execute("SELECT id, created_at, completed FROM sessions ORDER BY created_at DESC")
    sessions = cur.fetchall()

    overview = []
    for sid, created_at, completed in sessions:
        cur.execute(
            "SELECT question_nr, question, answer FROM onboarding_answers WHERE session_id = ? ORDER BY question_nr",
            (sid,)
        )
        onboarding = cur.fetchall()

        cur.execute(
            "SELECT iteration, city, country, continent, choice FROM swipes WHERE session_id = ? ORDER BY iteration",
            (sid,)
        )
        swipes = cur.fetchall()

        cur.execute(
            "SELECT rank, city, country, score FROM results WHERE session_id = ? ORDER BY rank",
            (sid,)
        )
        results = cur.fetchall()

        overview.append({
            "id": sid,
            "created_at": created_at,
            "completed": bool(completed),
            "onboarding": [{"nr": r[0], "question": r[1], "answer": r[2]} for r in onboarding],
            "swipes": [{"iteration": r[0], "city": r[1], "country": r[2], "continent": r[3], "choice": r[4]} for r in swipes],
            "results": [{"rank": r[0], "city": r[1], "country": r[2], "score": r[3]} for r in results],
        })

    con.close()
    return overview


def get_swipes_for_session(session_id):
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT iteration, city, country, continent, choice FROM swipes WHERE session_id = ? ORDER BY iteration",
        (session_id,)
    )
    rows = cur.fetchall()
    con.close()
    return rows


def get_all_liked_cities():
    """Gibt alle gelikten Städtenamen aus abgeschlossenen Sessions zurück."""
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT sw.city FROM swipes sw
        JOIN sessions s ON sw.session_id = s.id
        WHERE sw.choice = 'like' AND s.completed = 1
    """)
    rows = cur.fetchall()
    con.close()
    return [row[0] for row in rows]


def get_global_stats():
    con = get_connection()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM sessions WHERE completed = 1")
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT sw.continent, COUNT(*)
        FROM swipes sw
        JOIN sessions s ON sw.session_id = s.id
        WHERE sw.choice = 'like' AND s.completed = 1
        GROUP BY sw.continent
    """)
    continent_likes = {row[0]: row[1] for row in cur.fetchall()}

    cur.execute("""
        SELECT r.city, COUNT(*)
        FROM results r
        JOIN sessions s ON r.session_id = s.id
        WHERE r.rank = 1 AND s.completed = 1
        GROUP BY r.city
    """)
    top_city_counts = {row[0]: row[1] for row in cur.fetchall()}

    con.close()
    return {
        "total_sessions": total,
        "continent_likes": continent_likes,
        "top_city_counts": top_city_counts,
    }


def complete_session(session_id):
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "UPDATE sessions SET completed = 1 WHERE id = ?",
        (session_id,)
    )
    con.commit()
    con.close()