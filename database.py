import sqlite3
import uuid
import datetime
import os
import random

_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # kein O/I/0/1 (Verwechslungsgefahr)

def _generate_short_code(cur):
    for _ in range(50):
        code = ''.join(random.choices(_CODE_CHARS, k=4))
        cur.execute("SELECT 1 FROM sessions WHERE short_code = ?", (code,))
        if not cur.fetchone():
            return code
    raise RuntimeError("Kein freier short_code gefunden")

# Absoluter Pfad, damit die DB unabhängig vom Startverzeichnis gefunden wird
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")

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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS city_global_stats (
            city      TEXT PRIMARY KEY,
            country   TEXT NOT NULL,
            rank_sum  INTEGER DEFAULT 0,
            sessions  INTEGER DEFAULT 0,
            avg_rank  REAL DEFAULT 0
        )
    """)

    con.commit()
    con.close()


def print_session_overview(session_id):
    con = get_connection()
    cur = con.cursor()

    cur.execute("SELECT id, created_at, completed FROM sessions WHERE id = ?", (session_id,))
    session = cur.fetchone()
    if not session:
        con.close()
        return

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


def get_short_code(session_id):
    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT short_code FROM sessions WHERE id = ?", (session_id,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None


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
    # Deduplizieren: nur ersten Eintrag pro Rang behalten (falls /results mehrfach aufgerufen wurde)
    seen_ranks = set()
    deduped = []
    for row in rows:
        if row[0] not in seen_ranks:
            seen_ranks.add(row[0])
            deduped.append(row)
    return deduped


def get_all_sessions_overview():
    """Returns all sessions with their swipes and results."""
    con = get_connection()
    cur = con.cursor()

    cur.execute("SELECT id, created_at, completed FROM sessions ORDER BY created_at DESC")
    sessions = cur.fetchall()

    # Alle Swipes/Results in je einem Query laden statt zwei Queries pro Session
    cur.execute("SELECT session_id, iteration, city, country, continent, choice FROM swipes ORDER BY iteration")
    swipes_by_session = {}
    for sid, iteration, city, country, continent, choice in cur.fetchall():
        swipes_by_session.setdefault(sid, []).append(
            {"iteration": iteration, "city": city, "country": country, "continent": continent, "choice": choice}
        )

    cur.execute("SELECT session_id, rank, city, country, score FROM results ORDER BY rank")
    results_by_session = {}
    for sid, rank, city, country, score in cur.fetchall():
        rows = results_by_session.setdefault(sid, [])
        # Duplikate pro Rang ignorieren (Altlasten aus mehrfach gespeicherten Ergebnissen)
        if not any(r["rank"] == rank for r in rows):
            rows.append({"rank": rank, "city": city, "country": country, "score": score})

    con.close()

    return [{
        "id": sid,
        "created_at": created_at,
        "completed": bool(completed),
        "swipes": swipes_by_session.get(sid, []),
        "results": results_by_session.get(sid, []),
    } for sid, created_at, completed in sessions]


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


def get_all_liked_cities(exclude_session_id=None):
    """Gibt alle gelikten Städtenamen aus abgeschlossenen Sessions zurück.
    Mit exclude_session_id wird die eigene Session aus dem Vergleich ausgeschlossen."""
    con = get_connection()
    cur = con.cursor()
    query = """
        SELECT sw.city FROM swipes sw
        JOIN sessions s ON sw.session_id = s.id
        WHERE sw.choice = 'like' AND s.completed = 1
    """
    params = []
    if exclude_session_id:
        query += " AND sw.session_id != ?"
        params.append(exclude_session_id)
    cur.execute(query, params)
    rows = cur.fetchall()
    con.close()
    return [row[0] for row in rows]


def get_completed_session_count(exclude_session_id=None):
    """Anzahl abgeschlossener Sessions, optional ohne die eigene."""
    con = get_connection()
    cur = con.cursor()
    query = "SELECT COUNT(*) FROM sessions WHERE completed = 1"
    params = []
    if exclude_session_id:
        query += " AND id != ?"
        params.append(exclude_session_id)
    cur.execute(query, params)
    total = cur.fetchone()[0]
    con.close()
    return total


def is_session_completed(session_id):
    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT completed FROM sessions WHERE id = ?", (session_id,))
    row = cur.fetchone()
    con.close()
    return bool(row and row[0])


def get_city_shown_counts():
    """Gibt zurück, wie oft jede Stadt global in Swipes angezeigt wurde."""
    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT city, COUNT(*) FROM swipes GROUP BY city")
    rows = cur.fetchall()
    con.close()
    return {row[0]: row[1] for row in rows}


def get_city_result_stats():
    """Gibt zurück, wie oft jede Stadt in den Ergebnissen (Top-3) aller Sessions vorkam."""
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT r.city, r.country, COUNT(*) as count
        FROM results r
        JOIN sessions s ON r.session_id = s.id
        WHERE s.completed = 1
        GROUP BY r.city, r.country
        ORDER BY count DESC
    """)
    rows = cur.fetchall()
    con.close()
    return [{"city": row[0], "country": row[1], "count": row[2]} for row in rows]


def update_city_global_stats(scored_cities):
    """Aktualisiert laufende Rang-Aggregate pro Stadt.
    scored_cities: Liste von (score, city_data) sortiert best-first (aus score_all_cities).
    """
    con = get_connection()
    cur = con.cursor()
    for rank_idx, (_, city) in enumerate(scored_cities):
        city_name = city[0]
        country   = city[1]
        cur.execute("""
            INSERT INTO city_global_stats (city, country, rank_sum, sessions, avg_rank)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(city) DO UPDATE SET
                rank_sum = rank_sum + excluded.rank_sum,
                sessions = sessions + 1,
                avg_rank = CAST(rank_sum + excluded.rank_sum AS REAL) / (sessions + 1)
        """, (city_name, country, rank_idx, float(rank_idx)))
    con.commit()
    con.close()


def get_city_avg_ranks():
    """Gibt {city_name: {"avg_rank": float, "sessions": int}} zurück."""
    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT city, avg_rank, sessions FROM city_global_stats")
    rows = cur.fetchall()
    con.close()
    return {row[0]: {"avg_rank": row[1], "sessions": row[2]} for row in rows}


def get_all_completed_swipes():
    """Gibt alle Swipes aller abgeschlossenen Sessions zurück.
    Rückgabe: {session_id: [(iteration, city, country, continent, choice), ...]}"""
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT sw.session_id, sw.iteration, sw.city, sw.country, sw.continent, sw.choice
        FROM swipes sw
        JOIN sessions s ON sw.session_id = s.id
        WHERE s.completed = 1
        ORDER BY sw.session_id, sw.iteration
    """)
    rows = cur.fetchall()
    con.close()

    result = {}
    for session_id, iteration, city, country, continent, choice in rows:
        if session_id not in result:
            result[session_id] = []
        result[session_id].append((iteration, city, country, continent, choice))
    return result


def complete_session(session_id):
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "UPDATE sessions SET completed = 1 WHERE id = ?",
        (session_id,)
    )
    con.commit()
    con.close()


def delete_all_sessions():
    con = get_connection()
    cur = con.cursor()
    cur.execute("DELETE FROM swipes")
    cur.execute("DELETE FROM results")
    cur.execute("DELETE FROM sessions")
    cur.execute("DELETE FROM city_global_stats")
    con.commit()
    con.close()


def delete_session(session_id):
    con = get_connection()
    cur = con.cursor()
    cur.execute("DELETE FROM swipes WHERE session_id = ?", (session_id,))
    cur.execute("DELETE FROM results WHERE session_id = ?", (session_id,))
    cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    con.commit()
    con.close()