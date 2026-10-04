import time
import aiosqlite
from config import DB_PATH

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def init_db():
    db = await get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS warns (
            chat_id INTEGER, user_id INTEGER, count INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS bans (
            chat_id INTEGER, user_id INTEGER, until_ts INTEGER,
            PRIMARY KEY (chat_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id         INTEGER PRIMARY KEY,
            antimat         INTEGER DEFAULT 0,
            antiflood       INTEGER DEFAULT 0,
            antilink        INTEGER DEFAULT 0,
            anticaps        INTEGER DEFAULT 0,
            welcome         INTEGER DEFAULT 1,
            goodbye         INTEGER DEFAULT 1,
            rules           TEXT,
            welcome_text    TEXT,
            goodbye_text    TEXT,
            slowmode        INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS custom_bad_words (
            chat_id INTEGER, word TEXT,
            PRIMARY KEY (chat_id, word)
        );

        CREATE TABLE IF NOT EXISTS white_list (
            chat_id INTEGER, user_id INTEGER,
            PRIMARY KEY (chat_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS known_users (
            chat_id INTEGER, username TEXT, user_id INTEGER,
            PRIMARY KEY (chat_id, username)
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY, checked_ts INTEGER
        );

        CREATE TABLE IF NOT EXISTS saved_perms (
            chat_id INTEGER, user_id INTEGER, perms TEXT,
            PRIMARY KEY (chat_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS notes (
            chat_id INTEGER, user_id INTEGER, note TEXT, created_ts INTEGER,
            PRIMARY KEY (chat_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS activity (
            chat_id INTEGER, user_id INTEGER, date TEXT, count INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id, date)
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER, from_id INTEGER, about_id INTEGER,
            message_id INTEGER, text TEXT, ts INTEGER
        );
    """)
    await db.commit()


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# ================ WARNS ================
async def add_warn(chat_id: int, user_id: int) -> int:
    db = await get_db()
    await db.execute("""
        INSERT INTO warns (chat_id, user_id, count) VALUES (?, ?, 1)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET count = count + 1
    """, (chat_id, user_id))
    await db.commit()
    cur = await db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = await cur.fetchone()
    return row["count"] if row else 0


async def remove_warn(chat_id: int, user_id: int) -> int:
    db = await get_db()
    cur = await db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = await cur.fetchone()
    if not row or row["count"] <= 0:
        return 0
    new_count = row["count"] - 1
    if new_count == 0:
        await db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    else:
        await db.execute(
            "UPDATE warns SET count=? WHERE chat_id=? AND user_id=?",
            (new_count, chat_id, user_id),
        )
    await db.commit()
    return new_count


async def get_warns(chat_id: int, user_id: int) -> int:
    db = await get_db()
    cur = await db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = await cur.fetchone()
    return row["count"] if row else 0


async def reset_warns(chat_id: int, user_id: int):
    db = await get_db()
    await db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    await db.commit()


async def warns_stats(chat_id: int) -> tuple[int, int]:
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) AS users, COALESCE(SUM(count),0) AS total "
        "FROM warns WHERE chat_id=?", (chat_id,)
    )
    row = await cur.fetchone()
    return row["users"], row["total"]


async def warns_top(chat_id: int, limit: int = 10) -> list:
    db = await get_db()
    cur = await db.execute(
        "SELECT user_id, count FROM warns WHERE chat_id=? "
        "ORDER BY count DESC LIMIT ?",
        (chat_id, limit),
    )
    return await cur.fetchall()


# ================ BANS ================
async def set_ban(chat_id: int, user_id: int, until_ts: int):
    db = await get_db()
    await db.execute("""
        INSERT INTO bans (chat_id, user_id, until_ts) VALUES (?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET until_ts=excluded.until_ts
    """, (chat_id, user_id, until_ts))
    await db.commit()


async def remove_ban(chat_id: int, user_id: int):
    db = await get_db()
    await db.execute("DELETE FROM bans WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    await db.commit()


async def count_bans(chat_id: int) -> int:
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) AS c FROM bans WHERE chat_id=?", (chat_id,))
    row = await cur.fetchone()
    return row["c"]


# ================ SETTINGS ================
async def get_settings(chat_id: int) -> dict:
    db = await get_db()
    cur = await db.execute("SELECT * FROM chat_settings WHERE chat_id=?", (chat_id,))
    row = await cur.fetchone()
    if not row:
        await db.execute("INSERT INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
        await db.commit()
        cur = await db.execute("SELECT * FROM chat_settings WHERE chat_id=?", (chat_id,))
        row = await cur.fetchone()
    return dict(row)


ALLOWED_SETTINGS = (
    "antimat", "antiflood", "antilink", "anticaps",
    "welcome", "goodbye", "rules", "welcome_text", "goodbye_text", "slowmode",
)


async def update_setting(chat_id: int, key: str, value):
    if key not in ALLOWED_SETTINGS:
        raise ValueError(f"Unknown setting: {key}")
    db = await get_db()
    await db.execute(
        f"INSERT INTO chat_settings (chat_id, {key}) VALUES (?, ?) "
        f"ON CONFLICT(chat_id) DO UPDATE SET {key}=excluded.{key}",
        (chat_id, value),
    )
    await db.commit()


async def reset_settings(chat_id: int):
    db = await get_db()
    await db.execute("DELETE FROM chat_settings WHERE chat_id=?", (chat_id,))
    await db.commit()


# ================ CUSTOM BAD WORDS ================
async def add_bad_word(chat_id: int, word: str):
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO custom_bad_words (chat_id, word) VALUES (?, ?)",
        (chat_id, word.lower()),
    )
    await db.commit()


async def remove_bad_word(chat_id: int, word: str):
    db = await get_db()
    await db.execute(
        "DELETE FROM custom_bad_words WHERE chat_id=? AND word=?",
        (chat_id, word.lower()),
    )
    await db.commit()


async def get_bad_words(chat_id: int) -> list[str]:
    db = await get_db()
    cur = await db.execute("SELECT word FROM custom_bad_words WHERE chat_id=?", (chat_id,))
    rows = await cur.fetchall()
    return [r["word"] for r in rows]


# ================ WHITELIST ================
async def add_whitelist(chat_id: int, user_id: int):
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO white_list (chat_id, user_id) VALUES (?, ?)",
        (chat_id, user_id),
    )
    await db.commit()


async def remove_whitelist(chat_id: int, user_id: int):
    db = await get_db()
    await db.execute(
        "DELETE FROM white_list WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    await db.commit()


async def is_whitelisted(chat_id: int, user_id: int) -> bool:
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM white_list WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    return (await cur.fetchone()) is not None


# ================ KNOWN USERS ================
async def remember_user(chat_id: int, username: str, user_id: int):
    if not username:
        return
    db = await get_db()
    await db.execute("""
        INSERT INTO known_users (chat_id, username, user_id) VALUES (?, ?, ?)
        ON CONFLICT(chat_id, username) DO UPDATE SET user_id=excluded.user_id
    """, (chat_id, username.lower(), user_id))
    await db.commit()


async def find_user_by_username(chat_id: int, username: str) -> int | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT user_id FROM known_users WHERE chat_id=? AND username=?",
        (chat_id, username.lower()),
    )
    row = await cur.fetchone()
    return row["user_id"] if row else None


# ================ SUBSCRIPTIONS ================
async def save_sub_check(user_id: int):
    db = await get_db()
    await db.execute("""
        INSERT INTO subscriptions (user_id, checked_ts) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET checked_ts=excluded.checked_ts
    """, (user_id, int(time.time())))
    await db.commit()


async def get_sub_check(user_id: int) -> int | None:
    db = await get_db()
    cur = await db.execute("SELECT checked_ts FROM subscriptions WHERE user_id=?", (user_id,))
    row = await cur.fetchone()
    return row["checked_ts"] if row else None


# ================ SAVED PERMS ================
async def save_perms(chat_id: int, user_id: int, perms: str):
    db = await get_db()
    await db.execute("""
        INSERT INTO saved_perms (chat_id, user_id, perms) VALUES (?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET perms=excluded.perms
    """, (chat_id, user_id, perms))
    await db.commit()


async def pop_perms(chat_id: int, user_id: int) -> str | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT perms FROM saved_perms WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = await cur.fetchone()
    if not row:
        return None
    await db.execute("DELETE FROM saved_perms WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    await db.commit()
    return row["perms"]


# ================ NOTES ================
async def set_note(chat_id: int, user_id: int, note: str):
    db = await get_db()
    await db.execute("""
        INSERT INTO notes (chat_id, user_id, note, created_ts) VALUES (?, ?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET note=excluded.note, created_ts=excluded.created_ts
    """, (chat_id, user_id, note, int(time.time())))
    await db.commit()


async def get_note(chat_id: int, user_id: int) -> str | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT note FROM notes WHERE chat_id=? AND user_id=?", (chat_id, user_id)
    )
    row = await cur.fetchone()
    return row["note"] if row else None


# ================ ACTIVITY ================
async def track_activity(chat_id: int, user_id: int):
    from datetime import date
    today = date.today().isoformat()
    db = await get_db()
    await db.execute("""
        INSERT INTO activity (chat_id, user_id, date, count) VALUES (?, ?, ?, 1)
        ON CONFLICT(chat_id, user_id, date) DO UPDATE SET count = count + 1
    """, (chat_id, user_id, today))
    await db.commit()


async def top_active(chat_id: int, limit: int = 10) -> list:
    db = await get_db()
    cur = await db.execute("""
        SELECT user_id, SUM(count) AS total FROM activity
        WHERE chat_id=? GROUP BY user_id ORDER BY total DESC LIMIT ?
    """, (chat_id, limit))
    return await cur.fetchall()


# ================ REPORTS ================
async def add_report(chat_id: int, from_id: int, about_id: int, message_id: int, text: str):
    db = await get_db()
    await db.execute("""
        INSERT INTO reports (chat_id, from_id, about_id, message_id, text, ts)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (chat_id, from_id, about_id, message_id, text, int(time.time())))
    await db.commit()
