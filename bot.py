import os
import re
import json
import shutil
import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
)

# =========================
# Config / Logging
# =========================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "church_bot.db").strip()
TZ_NAME = os.getenv("TZ", "Asia/Yangon").strip()

ADMIN_IDS_ENV = os.getenv("ADMIN_IDS", "").strip()
ENV_ADMIN_IDS = set()
if ADMIN_IDS_ENV:
    for x in ADMIN_IDS_ENV.split(","):
        x = x.strip()
        if x.isdigit():
            ENV_ADMIN_IDS.add(int(x))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("church-community-bot")


def now_tz() -> datetime:
    return datetime.now(ZoneInfo(TZ_NAME))


def today_tz() -> date:
    return now_tz().date()


# =========================
# SQLite Database Helper
# =========================
class DB:
    def __init__(self, path: str):
        self.path = path
        self._init_db()

    def conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self):
        with self.conn() as con:
            cur = con.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA foreign_keys=ON;")

            # Global single-row tables
            cur.execute("""
                CREATE TABLE IF NOT EXISTS about (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    text TEXT NOT NULL
                );
            """)
            cur.execute("""
                INSERT OR IGNORE INTO about (id, text) VALUES (1, 'မင်္ဂလာပါ။ /about ကိုပြင်ရန် Admin မှ /edabout သုံးပါ။');
            """)

            # Contacts
            cur.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL
                );
            """)

            # Verses: type morning/night
            cur.execute("""
                CREATE TABLE IF NOT EXISTS verses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vtype TEXT NOT NULL CHECK (vtype IN ('morning','night')),
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            # Events
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_date TEXT,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            # Birthdays (month/day)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS birthdays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
                    day INTEGER NOT NULL CHECK (day BETWEEN 1 AND 31)
                );
            """)

            # Prayer requests
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pray_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    username TEXT,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            # Quiz questions
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quiz_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    a TEXT NOT NULL,
                    b TEXT NOT NULL,
                    c TEXT NOT NULL,
                    d TEXT NOT NULL,
                    correct TEXT NOT NULL CHECK (correct IN ('A','B','C','D')),
                    created_at TEXT NOT NULL
                );
            """)

            # Quiz scores
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quiz_scores (
                    user_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    username TEXT,
                    score INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
            """)

            # Chat settings (per chat)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    auto_quiz_every INTEGER NOT NULL DEFAULT 0,
                    msg_count INTEGER NOT NULL DEFAULT 0
                );
            """)

            # Track groups/chats
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT,
                    chat_type TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
            """)

            # Track users in chats (for /all mentions)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_users (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                );
            """)

            # Admins (DB-managed; ENV admins are also admins)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
            """)

            # Vote config + votes
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vote_configs (
                    chat_id INTEGER PRIMARY KEY,
                    vote_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vote_votes (
                    vote_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    choice_index INTEGER NOT NULL,
                    voted_at TEXT NOT NULL,
                    PRIMARY KEY (vote_id, user_id)
                );
            """)

            con.commit()

    # -------- Admin --------
    def is_admin(self, user_id: int) -> bool:
        if user_id in ENV_ADMIN_IDS:
            return True
        with self.conn() as con:
            row = con.execute(
                "SELECT enabled FROM admins WHERE user_id=?",
                (user_id,),
            ).fetchone()
            return bool(row and row["enabled"] == 1)

    def toggle_admin(self, user_id: int) -> str:
        ts = now_tz().isoformat()
        with self.conn() as con:
            row = con.execute("SELECT enabled FROM admins WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO admins (user_id, enabled, updated_at) VALUES (?,?,?)",
                    (user_id, 1, ts),
                )
                con.commit()
                return "✅ Admin အဖြစ် ထည့်ပြီးပါပြီ။"
            enabled = row["enabled"]
            new_enabled = 0 if enabled == 1 else 1
            con.execute("UPDATE admins SET enabled=?, updated_at=? WHERE user_id=?", (new_enabled, ts, user_id))
            con.commit()
            return "✅ Admin ပြောင်းလဲပြီးပါပြီ (toggle)။"

    # -------- Tracking --------
    def upsert_chat(self, chat_id: int, title: str | None, chat_type: str):
        ts = now_tz().isoformat()
        with self.conn() as con:
            con.execute("""
                INSERT INTO chats (chat_id, title, chat_type, last_seen)
                VALUES (?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title=excluded.title,
                    chat_type=excluded.chat_type,
                    last_seen=excluded.last_seen
            """, (chat_id, title, chat_type, ts))
            con.commit()

def upsert_chat(self, chat_id: int, title: Optional[str], chat_type: str):
        ts = now_tz().isoformat()
        with self.conn() as con:
            con.execute("""
                INSERT INTO chat_users (chat_id, user_id, username, first_name, last_name, last_seen)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    last_seen=excluded.last_seen
            """, (chat_id, user_id, username, first_name, last_name, ts))
            con.commit()

    # -------- Settings --------
    def get_settings(self, chat_id: int):
        with self.conn() as con:
            row = con.execute("SELECT * FROM chat_settings WHERE chat_id=?", (chat_id,)).fetchone()
            if row is None:
                con.execute("INSERT INTO chat_settings (chat_id, auto_quiz_every, msg_count) VALUES (?,?,?)", (chat_id, 0, 0))
                con.commit()
                row = con.execute("SELECT * FROM chat_settings WHERE chat_id=?", (chat_id,)).fetchone()
            return dict(row)

    def set_auto_quiz_every(self, chat_id: int, n: int):
        with self.conn() as con:
            con.execute("""
                INSERT INTO chat_settings (chat_id, auto_quiz_every, msg_count)
                VALUES (?,?,0)
                ON CONFLICT(chat_id) DO UPDATE SET auto_quiz_every=excluded.auto_quiz_every
            """, (chat_id, n))
            con.commit()

    def inc_msg_count(self, chat_id: int) -> int:
        s = self.get_settings(chat_id)
        new_count = int(s["msg_count"]) + 1
        with self.conn() as con:
            con.execute("UPDATE chat_settings SET msg_count=? WHERE chat_id=?", (new_count, chat_id))
            con.commit()
        return new_count

    def reset_msg_count(self, chat_id: int):
        with self.conn() as con:
            con.execute("UPDATE chat_settings SET msg_count=0 WHERE chat_id=?", (chat_id,))
            con.commit()

    # -------- About --------
    def get_about(self) -> str:
        with self.conn() as con:
            row = con.execute("SELECT text FROM about WHERE id=1").fetchone()
            return row["text"] if row else ""

    def set_about(self, text: str):
        with self.conn() as con:
            con.execute("UPDATE about SET text=? WHERE id=1", (text,))
            con.commit()

    # -------- Contacts --------
    def set_contacts(self, items: list[tuple[str, str]]):
        with self.conn() as con:
            con.execute("DELETE FROM contacts")
            con.executemany("INSERT INTO contacts (name, phone) VALUES (?,?)", items)
            con.commit()

    def get_contacts(self) -> list[sqlite3.Row]:
        with self.conn() as con:
            return con.execute("SELECT name, phone FROM contacts ORDER BY id ASC").fetchall()

    # -------- Verses --------
    def add_verses(self, vtype: str, texts: list[str]):
        ts = now_tz().isoformat()
        with self.conn() as con:
            con.executemany(
                "INSERT INTO verses (vtype, text, created_at) VALUES (?,?,?)",
                [(vtype, t, ts) for t in texts],
            )
            con.commit()

    def get_verses(self, vtype: str) -> list[str]:
        with self.conn() as con:
            rows = con.execute("SELECT text FROM verses WHERE vtype=? ORDER BY id ASC", (vtype,)).fetchall()
            return [r["text"] for r in rows]

    def delete_last_verses(self, vtype: str, amount: int) -> int:
        with self.conn() as con:
            rows = con.execute("SELECT id FROM verses WHERE vtype=? ORDER BY id DESC LIMIT ?", (vtype, amount)).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                return 0
            con.execute(f"DELETE FROM verses WHERE id IN ({','.join('?'*len(ids))})", ids)
            con.commit()
            return len(ids)

    # -------- Events --------
    def set_events(self, items: list[tuple[str | None, str]]):
        ts = now_tz().isoformat()
        with self.conn() as con:
            con.execute("DELETE FROM events")
            con.executemany(
                "INSERT INTO events (event_date, text, created_at) VALUES (?,?,?)",
                [(d, t, ts) for d, t in items],
            )
            con.commit()

    def get_events(self) -> list[sqlite3.Row]:
        with self.conn() as con:
            return con.execute("SELECT event_date, text FROM events ORDER BY event_date IS NULL, event_date ASC, id ASC").fetchall()

    def delete_last_events(self, amount: int) -> int:
        with self.conn() as con:
            rows = con.execute("SELECT id FROM events ORDER BY id DESC LIMIT ?", (amount,)).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                return 0
            con.execute(f"DELETE FROM events WHERE id IN ({','.join('?'*len(ids))})", ids)
            con.commit()
            return len(ids)

    # -------- Birthdays --------
    def set_birthdays(self, items: list[tuple[str, int, int]]):
        with self.conn() as con:
            con.execute("DELETE FROM birthdays")
            con.executemany("INSERT INTO birthdays (name, month, day) VALUES (?,?,?)", items)
            con.commit()

    def get_birthdays_month(self, month: int) -> list[sqlite3.Row]:
        with self.conn() as con:
            return con.execute(
                "SELECT name, month, day FROM birthdays WHERE month=? ORDER BY day ASC, name ASC",
                (month,),
            ).fetchall()

    def delete_last_birthdays(self, amount: int) -> int:
        with self.conn() as con:
            rows = con.execute("SELECT id FROM birthdays ORDER BY id DESC LIMIT ?", (amount,)).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                return 0
            con.execute(f"DELETE FROM birthdays WHERE id IN ({','.join('?'*len(ids))})", ids)
            con.commit()
            return len(ids)

    # -------- Pray --------
    def add_pray(self, chat_id: int, user_id: int, name: str, username: str | None, text: str):
        ts = now_tz().isoformat()
        with self.conn() as con:
            con.execute("""
                INSERT INTO pray_requests (chat_id, user_id, name, username, text, created_at)
                VALUES (?,?,?,?,?,?)
            """, (chat_id, user_id, name, username, text, ts))
            con.commit()

    def get_pray_list(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.conn() as con:
            return con.execute("""
                SELECT name, username, text, created_at
                FROM pray_requests
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)).fetchall()

    def delete_last_pray(self, amount: int) -> int:
        with self.conn() as con:
            rows = con.execute("SELECT id FROM pray_requests ORDER BY id DESC LIMIT ?", (amount,)).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                return 0
            con.execute(f"DELETE FROM pray_requests WHERE id IN ({','.join('?'*len(ids))})", ids)
            con.commit()
            return len(ids)

    # -------- Quiz --------
    def add_quiz_questions(self, items: list[tuple[str, str, str, str, str, str]]):
        ts = now_tz().isoformat()
        with self.conn() as con:
            con.executemany("""
                INSERT INTO quiz_questions (question, a, b, c, d, correct, created_at)
                VALUES (?,?,?,?,?,?,?)
            """, [(q,a,b,c,d,correct,ts) for (q,a,b,c,d,correct) in items])
            con.commit()

    def get_random_quiz(self) -> sqlite3.Row | None:
        with self.conn() as con:
            return con.execute("SELECT * FROM quiz_questions ORDER BY RANDOM() LIMIT 1").fetchone()

    def delete_last_quiz(self, amount: int) -> int:
        with self.conn() as con:
            rows = con.execute("SELECT id FROM quiz_questions ORDER BY id DESC LIMIT ?", (amount,)).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                return 0
            con.execute(f"DELETE FROM quiz_questions WHERE id IN ({','.join('?'*len(ids))})", ids)
            con.commit()
            return len(ids)

    def add_score(self, user_id: int, name: str, username: str | None, delta: int):
        ts = now_tz().isoformat()
        with self.conn() as con:
            row = con.execute("SELECT score FROM quiz_scores WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                con.execute("""
                    INSERT INTO quiz_scores (user_id, name, username, score, updated_at)
                    VALUES (?,?,?,?,?)
                """, (user_id, name, username, max(0, delta), ts))
            else:
                new_score = int(row["score"]) + delta
                if new_score < 0:
                    new_score = 0
                con.execute("""
                    UPDATE quiz_scores
                    SET name=?, username=?, score=?, updated_at=?
                    WHERE user_id=?
                """, (name, username, new_score, ts, user_id))
            con.commit()

    def set_score(self, user_id: int, name: str, username: str | None, score: int):
        ts = now_tz().isoformat()
        score = max(0, int(score))
        with self.conn() as con:
            con.execute("""
                INSERT INTO quiz_scores (user_id, name, username, score, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    name=excluded.name,
                    username=excluded.username,
                    score=excluded.score,
                    updated_at=excluded.updated_at
            """, (user_id, name, username, score, ts))
            con.commit()

    def top_scores(self, limit: int = 10) -> list[sqlite3.Row]:
        with self.conn() as con:
            return con.execute("""
                SELECT name, username, score
                FROM quiz_scores
                ORDER BY score DESC, updated_at DESC
                LIMIT ?
            """, (limit,)).fetchall()

    # -------- Vote --------
    def set_vote(self, chat_id: int, question: str, options: list[str]) -> str:
        vote_id = f"{chat_id}:{int(now_tz().timestamp())}"
        ts = now_tz().isoformat()
        with self.conn() as con:
            con.execute("""
                INSERT INTO vote_configs (chat_id, vote_id, question, options_json, active, created_at)
                VALUES (?,?,?,?,1,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    vote_id=excluded.vote_id,
                    question=excluded.question,
                    options_json=excluded.options_json,
                    active=1,
                    created_at=excluded.created_at
            """, (chat_id, vote_id, question, json.dumps(options, ensure_ascii=False), ts))
            con.commit()
        return vote_id

    def get_vote(self, chat_id: int) -> sqlite3.Row | None:
        with self.conn() as con:
            return con.execute("SELECT * FROM vote_configs WHERE chat_id=?", (chat_id,)).fetchone()

    def cast_vote(self, vote_id: str, user_id: int, choice_index: int):
        ts = now_tz().isoformat()
        with self.conn() as con:
            con.execute("""
                INSERT INTO vote_votes (vote_id, user_id, choice_index, voted_at)
                VALUES (?,?,?,?)
                ON CONFLICT(vote_id, user_id) DO UPDATE SET
                    choice_index=excluded.choice_index,
                    voted_at=excluded.voted_at
            """, (vote_id, user_id, choice_index, ts))
            con.commit()

    def vote_results(self, vote_id: str, options_count: int) -> list[int]:
        counts = [0] * options_count
        with self.conn() as con:
            rows = con.execute("""
                SELECT choice_index, COUNT(*) as c
                FROM vote_votes
                WHERE vote_id=?
                GROUP BY choice_index
            """, (vote_id,)).fetchall()
            for r in rows:
                idx = int(r["choice_index"])
                if 0 <= idx < options_count:
                    counts[idx] = int(r["c"])
        return counts

    # -------- Stats / Maintenance --------
    def stats(self) -> dict:
        with self.conn() as con:
            users = con.execute("SELECT COUNT(DISTINCT user_id) AS c FROM quiz_scores").fetchone()["c"]
            chats = con.execute("SELECT COUNT(*) AS c FROM chats").fetchone()["c"]
            groups = con.execute("SELECT COUNT(*) AS c FROM chats WHERE chat_type IN ('group','supergroup')").fetchone()["c"]
            return {"users": users, "chats": chats, "groups": groups}

    def list_groups(self) -> list[sqlite3.Row]:
        with self.conn() as con:
            return con.execute("""
                SELECT chat_id, title, chat_type, last_seen
                FROM chats
                WHERE chat_type IN ('group','supergroup')
                ORDER BY last_seen DESC
            """).fetchall()

    def all_clear(self):
        with self.conn() as con:
            con.execute("DELETE FROM contacts")
            con.execute("DELETE FROM verses")
            con.execute("DELETE FROM events")
            con.execute("DELETE FROM birthdays")
            con.execute("DELETE FROM pray_requests")
            con.execute("DELETE FROM quiz_questions")
            con.execute("DELETE FROM quiz_scores")
            con.execute("DELETE FROM vote_votes")
            con.execute("DELETE FROM vote_configs")
            con.execute("DELETE FROM chat_users")
            con.execute("DELETE FROM chats")
            con.execute("DELETE FROM chat_settings")
            con.commit()


db = DB(DB_PATH)

# =========================
# Utilities
# =========================
def is_group(chat_type: str) -> bool:
    return chat_type in (ChatType.GROUP, ChatType.SUPERGROUP)


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or not db.is_admin(user.id):
            if update.effective_message:
                await update.effective_message.reply_text("⛔ Admin မဟုတ်သဖြင့် မသုံးနိုင်ပါ။")
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapper


def pick_daily_item(items: list[str], salt: str) -> str | None:
    if not items:
        return None
    key = f"{today_tz().isoformat()}|{salt}"
    idx = abs(hash(key)) % len(items)
    return items[idx]


def user_display_name(u) -> str:
    if not u:
        return "Unknown"
    name = (u.full_name or "").strip()
    if not name:
        name = (u.first_name or "User").strip()
    return name


# =========================
# Command Texts
# =========================
USER_HELP = (
    "📌 <b>User Commands</b>\n"
    "• /start - Bot စတင် & နှုတ်ခွန်းဆက်\n"
    "• /helps - Commands စာရင်း\n"
    "• /about - အဖွဲ့သမိုင်း/ရည်ရွယ်ချက်\n"
    "• /contact - တာဝန်ခံများ ဖုန်းနံပါတ်\n"
    "• /verse - Morning/Night Daily Verse\n"
    "• /events - လာမည့် အစီအစဉ်များ\n"
    "• /birthday - ယခုလ မွေးနေ့ရှင်များ\n"
    "• /pray <text> - ဆုတောင်းတောင်းဆိုရန်\n"
    "• /praylist - ဆုတောင်းစာရင်း\n"
    "• /quiz - Quiz ဖြေ (Auto Quiz လည်းရှိ)\n"
    "• /tops - Quiz Ranking (Name + Score)\n"
    "• /report - Admin ထံ အကြောင်းကြား\n"
    "• /all - Group ထဲ active members ကို mention\n"
    "• /vote - မဲပေးရန် (Button)\n"
)

ADMIN_HELP = (
    "🛠️ <b>Admin Commands</b>\n"
    "• /edit - Admin commands စာရင်း\n"
    "• /edabout - About ပြင်ရန်\n"
    "• /edcontact - Contacts ထည့်/ပြင်\n"
    "• /edverse - Verses ထည့် (M/N)\n"
    "• /edevents - Events ထည့်/ပြင်\n"
    "• /edbirthday - Birthdays ထည့်/ပြင်\n"
    "• /set <number> - Auto Quiz message count သတ်မှတ်\n"
    "• /edquiz - Quiz ထည့်\n"
    "• /edpoint <user_id> <score> - Score set\n"
    "• /broadcast - Groups အားလုံးထံ broadcast\n"
    "• /stats - Users/Groups stats\n"
    "• /backup - DB backup file ပို့\n"
    "• /restore - DB file ဖြင့် restore\n"
    "• /allclear - Data အကုန်ဖျက် (confirm)\n"
    "• /delete <type> <amount> - Data ဖျက် (verse/quiz/events/birthday/pray)\n"
    "• /edadmin <id> - Admin toggle\n"
    "• /edvote - Vote question/options သတ်မှတ်\n"
)

# =========================
# Conversations States
# =========================
EDABOUT = 10
EDCONTACT = 20
EDVERSE = 30
EDEVENTS = 40
EDBIRTHDAY = 50
EDQUIZ = 60
BROADCAST = 70
RESTORE = 80
EDVOTE = 90

ALLCLEAR_CONFIRM = "ALLCLEAR_CONFIRM"


# =========================
# Core Handlers
# =========================
async def track_update(update: Update):
    """Track chats and users for /all, /broadcast stats etc."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        if chat:
            db.upsert_chat(chat.id, chat.title, chat.type)
        if chat and user:
            db.upsert_chat_user(chat.id, user.id, user.username, user.first_name, user.last_name)
    except Exception as e:
        log.warning("track_update error: %s", e)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    msg = (
        "🙋‍♂️ <b>Church Community Bot မှ ကြိုဆိုပါတယ်</b>\n\n"
        "• Commands ကြည့်ရန်: /helps\n"
        "• Daily Verse: /verse\n"
        "• Events: /events\n"
        "• Pray request: /pray <text>\n"
    )
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def helps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(USER_HELP, parse_mode=ParseMode.HTML)


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    text = db.get_about()
    await update.effective_message.reply_text(f"📖 <b>About</b>\n\n{text}", parse_mode=ParseMode.HTML)


async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    rows = db.get_contacts()
    if not rows:
        await update.effective_message.reply_text("📞 Contact မသတ်မှတ်ရသေးပါ။ Admin မှ /edcontact ဖြင့် ထည့်ပါ။")
        return
    lines = ["📞 <b>Contacts</b>\n"]
    for r in rows:
        lines.append(f"• <b>{r['name']}</b> — <code>{r['phone']}</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def verse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    m = db.get_verses("morning")
    n = db.get_verses("night")
    mv = pick_daily_item(m, "morning")
    nv = pick_daily_item(n, "night")

    out = ["📜 <b>Daily Verse</b>\n"]
    out.append(f"🌅 <b>Morning</b>\n{mv if mv else 'မရှိသေးပါ (Admin: /edverse)'}\n")
    out.append(f"🌙 <b>Night</b>\n{nv if nv else 'မရှိသေးပါ (Admin: /edverse)'}")
    await update.effective_message.reply_text("\n".join(out), parse_mode=ParseMode.HTML)


async def events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    rows = db.get_events()
    if not rows:
        await update.effective_message.reply_text("📅 Events မရှိသေးပါ။ Admin မှ /edevents ဖြင့် ထည့်ပါ။")
        return
    lines = ["📅 <b>Upcoming Events</b>\n"]
    for r in rows:
        d = r["event_date"] or ""
        if d:
            lines.append(f"• <b>{d}</b> — {r['text']}")
        else:
            lines.append(f"• {r['text']}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def birthday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    month = today_tz().month
    rows = db.get_birthdays_month(month)
    if not rows:
        await update.effective_message.reply_text("🎂 ယခုလ မွေးနေ့ရှင်စာရင်း မရှိသေးပါ။ Admin: /edbirthday")
        return
    lines = [f"🎂 <b>{month} လ မွေးနေ့ရှင်များ</b>\n"]
    for r in rows:
        lines.append(f"• {r['day']:02d}/{r['month']:02d} — <b>{r['name']}</b>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def pray(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    chat = update.effective_chat
    user = update.effective_user
    text = update.effective_message.text or ""
    m = re.match(r"^/pray(?:@\w+)?\s+(.+)$", text, flags=re.S)
    if not m:
        await update.effective_message.reply_text("အသုံးပြုနည်း: /pray <ဆုတောင်းအချက်>")
        return
    req = m.group(1).strip()
    if not req:
        await update.effective_message.reply_text("ဆုတောင်းအချက် မပါဝင်ပါ။")
        return
    db.add_pray(chat.id, user.id, user_display_name(user), user.username, req)
    await update.effective_message.reply_text("🙏 ဆုတောင်းတောင်းဆိုချက်ကို လက်ခံပြီးပါပြီ။")


async def praylist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    rows = db.get_pray_list(limit=30)
    if not rows:
        await update.effective_message.reply_text("🙏 ဆုတောင်းစာရင်း မရှိသေးပါ။")
        return
    lines = ["🙏 <b>Pray Requests (Latest)</b>\n"]
    for r in rows:
        uname = f"@{r['username']}" if r["username"] else ""
        created = r["created_at"][:19].replace("T", " ")
        lines.append(f"• <b>{r['name']}</b> {uname}\n  └ {r['text']}\n  <i>{created}</i>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# =========================
# Quiz (command + auto)
# =========================
def quiz_keyboard(qid: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("A", callback_data=f"quiz:{qid}:A"),
         InlineKeyboardButton("B", callback_data=f"quiz:{qid}:B")],
        [InlineKeyboardButton("C", callback_data=f"quiz:{qid}:C"),
         InlineKeyboardButton("D", callback_data=f"quiz:{qid}:D")],
    ]
    return InlineKeyboardMarkup(buttons)


async def send_quiz_to_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE, force: bool = False):
    q = db.get_random_quiz()
    if not q:
        if force:
            await context.bot.send_message(chat_id, "🧠 Quiz မရှိသေးပါ။ Admin: /edquiz ဖြင့် ထည့်ပါ။")
        return

    text = (
        "🧠 <b>Quiz Time</b>\n\n"
        f"<b>Q{q['id']}:</b> {q['question']}\n\n"
        f"A) {q['a']}\n"
        f"B) {q['b']}\n"
        f"C) {q['c']}\n"
        f"D) {q['d']}\n\n"
        "✅ အဖြေကို Button နဲ့ရွေးပါ။"
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=quiz_keyboard(q["id"]),
    )


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await send_quiz_to_chat(update.effective_chat.id, context, force=True)


async def quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    query = update.callback_query
    await query.answer()

    data = query.data  # quiz:<qid>:<choice>
    _, qid_str, choice = data.split(":")
    qid = int(qid_str)

    with db.conn() as con:
        q = con.execute("SELECT * FROM quiz_questions WHERE id=?", (qid,)).fetchone()

    if not q:
        await query.edit_message_text("ဒီ Quiz မရှိတော့ပါ။ /quiz ပြန်စမ်းပါ။")
        return

    user = update.effective_user
    correct = q["correct"]
    if choice == correct:
        db.add_score(user.id, user_display_name(user), user.username, delta=1)
        verdict = "✅ မှန်ပါတယ်! (+1)"
    else:
        verdict = f"❌ မမှန်ပါ။ Correct: <b>{correct}</b>"

    await query.edit_message_text(
        text=(
            "🧠 <b>Quiz Result</b>\n\n"
            f"<b>Q{qid}:</b> {q['question']}\n"
            f"Your answer: <b>{choice}</b>\n"
            f"{verdict}"
        ),
        parse_mode=ParseMode.HTML,
    )


async def tops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    rows = db.top_scores(limit=10)
    if not rows:
        await update.effective_message.reply_text("🏆 Ranking မရှိသေးပါ။ /quiz ဖြေပါ။")
        return
    lines = ["🏆 <b>Top Quiz Ranking</b>\n"]
    for i, r in enumerate(rows, start=1):
        uname = f"@{r['username']}" if r["username"] else ""
        lines.append(f"{i}. <b>{r['name']}</b> {uname} — <b>{r['score']}</b>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# =========================
# Report
# =========================
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    text = update.effective_message.text or ""
    m = re.match(r"^/report(?:@\w+)?\s+(.+)$", text, flags=re.S)
    if not m:
        await update.effective_message.reply_text("အသုံးပြုနည်း: /report <အကြောင်းအရာ>")
        return
    body = m.group(1).strip()
    if not body:
        await update.effective_message.reply_text("အကြောင်းအရာ မပါဝင်ပါ။")
        return

    user = update.effective_user
    chat = update.effective_chat
    msg = (
        "📣 <b>User Report</b>\n\n"
        f"From: <b>{user_display_name(user)}</b> (@{user.username if user.username else '-'})\n"
        f"UserID: <code>{user.id}</code>\n"
        f"Chat: <code>{chat.id}</code> ({chat.type})\n\n"
        f"Message:\n{body}"
    )

    # Send to ENV admins + DB admins
    admin_ids = set(ENV_ADMIN_IDS)
    with db.conn() as con:
        rows = con.execute("SELECT user_id FROM admins WHERE enabled=1").fetchall()
        admin_ids.update([int(r["user_id"]) for r in rows])

    sent_any = False
    for aid in admin_ids:
        try:
            await context.bot.send_message(aid, msg, parse_mode=ParseMode.HTML)
            sent_any = True
        except Exception:
            pass

    if sent_any:
        await update.effective_message.reply_text("✅ Admin ထံ အကြောင်းကြားပြီးပါပြီ။")
    else:
        await update.effective_message.reply_text("⚠️ Admin ဆီ မပို့နိုင်ပါ။ Bot ကို Admin များနဲ့ private chat မှာ /start လုပ်ထားရန်လိုပါတယ်။")


# =========================
# /all (mention active users)
# =========================
async def all_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    chat = update.effective_chat
    if not is_group(chat.type):
        await update.effective_message.reply_text("/all ကို Group ထဲမှာပဲ သုံးပါ။")
        return

    with db.conn() as con:
        rows = con.execute("""
            SELECT user_id, username, first_name, last_name
            FROM chat_users
            WHERE chat_id=?
            ORDER BY last_seen DESC
            LIMIT 60
        """, (chat.id,)).fetchall()

    if not rows:
        await update.effective_message.reply_text("ဒီ Group ထဲ mention လုပ်ရန် user data မရှိသေးပါ။ Member များ စကားပြောပြီးနောက် /all သုံးပါ။")
        return

    parts = ["📣 <b>All Members</b>\n"]
    for r in rows:
        uid = int(r["user_id"])
        uname = (r["username"] or "").strip()
        if uname:
            parts.append(f"@{uname}")
        else:
            name = " ".join([x for x in [r["first_name"], r["last_name"]] if x]) or "member"
            # clickable mention without username:
            parts.append(f'<a href="tg://user?id={uid}">{name}</a>')

    text = " ".join(parts)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# =========================
# Vote (user) + edvote (admin)
# =========================
def vote_keyboard(vote_id: str, options: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for i, opt in enumerate(options):
        rows.append([InlineKeyboardButton(opt, callback_data=f"vote:{vote_id}:{i}")])
    rows.append([InlineKeyboardButton("📊 Results", callback_data=f"vote_result:{vote_id}")])
    return InlineKeyboardMarkup(rows)


async def vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    chat = update.effective_chat
    v = db.get_vote(chat.id)
    if not v or int(v["active"]) != 1:
        await update.effective_message.reply_text("🗳️ Vote မသတ်မှတ်ရသေးပါ။ Admin: /edvote")
        return

    options = json.loads(v["options_json"])
    text = f"🗳️ <b>{v['question']}</b>\n\nရွေးပြီးမဲပေးပါ (Button)👇"
    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=vote_keyboard(v["vote_id"], options),
    )


async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    q = update.callback_query
    await q.answer()

    if q.data.startswith("vote_result:"):
        vote_id = q.data.split(":", 1)[1]
        # Need options to compute
        with db.conn() as con:
            row = con.execute("SELECT * FROM vote_configs WHERE vote_id=?", (vote_id,)).fetchone()
        if not row:
            await q.edit_message_text("Vote မရှိတော့ပါ။")
            return
        options = json.loads(row["options_json"])
        counts = db.vote_results(vote_id, len(options))
        total = sum(counts)
        lines = [f"📊 <b>Vote Results</b>\n<b>{row['question']}</b>\n"]
        for opt, c in zip(options, counts):
            pct = (c / total * 100.0) if total > 0 else 0.0
            lines.append(f"• {opt} — <b>{c}</b> ({pct:.1f}%)")
        lines.append(f"\nTotal votes: <b>{total}</b>")
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    # vote:<vote_id>:<idx>
    _, vote_id, idx_str = q.data.split(":")
    idx = int(idx_str)
    user = update.effective_user
    db.cast_vote(vote_id, user.id, idx)
    await q.answer("✅ မဲပေးပြီးပါပြီ။", show_alert=True)


# =========================
# Auto Quiz Trigger (message counter)
# =========================
async def on_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)

    chat = update.effective_chat
    if not chat:
        return

    # only count in groups
    if is_group(chat.type):
        s = db.get_settings(chat.id)
        every = int(s["auto_quiz_every"] or 0)
        if every > 0:
            cnt = db.inc_msg_count(chat.id)
            if cnt >= every:
                db.reset_msg_count(chat.id)
                await send_quiz_to_chat(chat.id, context, force=False)


# =========================
# Admin: /edit help
# =========================
@admin_only
async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(ADMIN_HELP, parse_mode=ParseMode.HTML)


# =========================
# Admin: /edabout (conversation)
# =========================
@admin_only
async def edabout_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(
        "📝 About ကို အသစ်ရေးပေးပါ (စာအရှည် OK)။\nပြီးရင် message တစ်ခုတည်းနဲ့ ပို့ပါ။\nCancel: /cancel"
    )
    return EDABOUT


@admin_only
async def edabout_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("စာမပါဝင်ပါ။ ပြန်ပို့ပါ။")
        return EDABOUT
    db.set_about(text)
    await update.effective_message.reply_text("✅ About ကို သိမ်းပြီးပါပြီ။")
    return ConversationHandler.END


# =========================
# Admin: /edcontact
# =========================
@admin_only
async def edcontact_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(
        "📞 Contacts ထည့်/ပြင်ရန်\n"
        "Line တစ်ကြောင်းစီကို ဒီပုံစံနဲ့ ပို့ပါ:\n"
        "<Name> - <Phone>\n\n"
        "ဥပမာ:\n"
        "Ko Aung - 09xxxxxxx\n"
        "Ma Su - 09yyyyyyy\n\n"
        "ပြီးရင် message တစ်ခုတည်းနဲ့ ပို့ပါ။\nCancel: /cancel"
    )
    return EDCONTACT


@admin_only
async def edcontact_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    raw = (update.effective_message.text or "").strip()
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "-" not in line:
            continue
        name, phone = [x.strip() for x in line.split("-", 1)]
        if name and phone:
            items.append((name, phone))
    if not items:
        await update.effective_message.reply_text("Format မမှန်ပါ။ နမူနာအတိုင်း ပြန်ပို့ပါ။")
        return EDCONTACT
    db.set_contacts(items)
    await update.effective_message.reply_text(f"✅ Contacts ({len(items)}) ခု သိမ်းပြီးပါပြီ။")
    return ConversationHandler.END


# =========================
# Admin: /edverse
# =========================
@admin_only
async def edverse_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(
        "📜 Verses ထည့်ရန်\n"
        "Line တစ်ကြောင်းစီကို ဒီပုံစံနဲ့ ပို့ပါ:\n"
        "M| <Morning Verse>\n"
        "N| <Night Verse>\n\n"
        "ဥပမာ:\n"
        "M| Psalm 23:1 ...\n"
        "N| John 3:16 ...\n\n"
        "Message တစ်ခုတည်းနဲ့ ပို့ပါ။ (တစ်ခုထက်မက OK)\nCancel: /cancel"
    )
    return EDVERSE


@admin_only
async def edverse_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    raw = (update.effective_message.text or "").strip()
    morning = []
    night = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("M|") or line.startswith("m|"):
            t = line.split("|", 1)[1].strip()
            if t:
                morning.append(t)
        elif line.startswith("N|") or line.startswith("n|"):
            t = line.split("|", 1)[1].strip()
            if t:
                night.append(t)

    if not morning and not night:
        await update.effective_message.reply_text("M| / N| format မတွေ့ပါ။ ပြန်ပို့ပါ။")
        return EDVERSE

    if morning:
        db.add_verses("morning", morning)
    if night:
        db.add_verses("night", night)

    await update.effective_message.reply_text(f"✅ Verse သိမ်းပြီးပါပြီ (Morning {len(morning)} / Night {len(night)})")
    return ConversationHandler.END


# =========================
# Admin: /edevents
# =========================
@admin_only
async def edevents_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(
        "📅 Events ထည့်/ပြင်ရန်\n"
        "Message တစ်ခုတည်းနဲ့ line တစ်ကြောင်းစီ ပို့ပါ။\n\n"
        "Format:\n"
        "YYYY-MM-DD | <Event Text>\n"
        "date မသိရင်:\n"
        "| <Event Text>\n\n"
        "ဥပမာ:\n"
        "2026-03-01 | Sunday Service 9AM\n"
        "| Youth Fellowship (time TBD)\n\n"
        "ဒီ command က old events အကုန် replace လုပ်ပါမယ်။\nCancel: /cancel"
    )
    return EDEVENTS


@admin_only
async def edevents_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    raw = (update.effective_message.text or "").strip()
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            continue
        left, right = [x.strip() for x in line.split("|", 1)]
        d = left if left else None
        t = right
        if not t:
            continue
        if d:
            # quick validate
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                d = None
        items.append((d, t))

    if not items:
        await update.effective_message.reply_text("Format မမှန်ပါ။ နမူနာအတိုင်း ပြန်ပို့ပါ။")
        return EDEVENTS

    db.set_events(items)
    await update.effective_message.reply_text(f"✅ Events ({len(items)}) ခု သိမ်းပြီးပါပြီ။")
    return ConversationHandler.END


# =========================
# Admin: /edbirthday
# =========================
@admin_only
async def edbirthday_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(
        "🎂 Birthdays ထည့်/ပြင်ရန်\n"
        "Line တစ်ကြောင်းစီ:\n"
        "<Name> | MM-DD\n\n"
        "ဥပမာ:\n"
        "Ko Aung | 03-15\n"
        "Ma Su   | 03-29\n\n"
        "ဒီ command က old list အကုန် replace လုပ်ပါမယ်။\nCancel: /cancel"
    )
    return EDBIRTHDAY


@admin_only
async def edbirthday_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    raw = (update.effective_message.text or "").strip()
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        name, md = [x.strip() for x in line.split("|", 1)]
        m = re.match(r"^(\d{2})-(\d{2})$", md)
        if not (name and m):
            continue
        month = int(m.group(1))
        day = int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            items.append((name, month, day))

    if not items:
        await update.effective_message.reply_text("Format မမှန်ပါ။ နမူနာအတိုင်း ပြန်ပို့ပါ။")
        return EDBIRTHDAY

    db.set_birthdays(items)
    await update.effective_message.reply_text(f"✅ Birthdays ({len(items)}) ခု သိမ်းပြီးပါပြီ။")
    return ConversationHandler.END


# =========================
# Admin: /set <number>
# =========================
@admin_only
async def set_auto_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    chat = update.effective_chat
    args = context.args
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("အသုံးပြုနည်း: /set <number>\nဥပမာ: /set 50\n(0 ဆို auto quiz ပိတ်)")
        return
    n = int(args[0])
    if n < 0:
        n = 0
    db.set_auto_quiz_every(chat.id, n)
    await update.effective_message.reply_text(f"✅ Auto Quiz message count = {n} သတ်မှတ်ပြီးပါပြီ။")


# =========================
# Admin: /edquiz
# =========================
@admin_only
async def edquiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(
        "🧠 Quiz ထည့်ရန် (တစ်ခုထက်မက OK)\n"
        "Format (Block တစ်ခုချင်းစီကို blank line နဲ့ ခွဲနိုင်):\n"
        "Q: ...\n"
        "A: ...\n"
        "B: ...\n"
        "C: ...\n"
        "D: ...\n"
        "Correct: A/B/C/D\n\n"
        "ဥပမာ:\n"
        "Q: Jesus was born in?\n"
        "A: Jerusalem\nB: Bethlehem\nC: Nazareth\nD: Rome\n"
        "Correct: B\n\n"
        "Message တစ်ခုတည်းနဲ့ ပို့ပါ။\nCancel: /cancel"
    )
    return EDQUIZ


def parse_quiz_blocks(raw: str):
    blocks = re.split(r"\n\s*\n", raw.strip())
    items = []
    for b in blocks:
        lines = [x.strip() for x in b.splitlines() if x.strip()]
        data = {}
        for ln in lines:
            if ":" not in ln:
                continue
            k, v = [x.strip() for x in ln.split(":", 1)]
            data[k.upper()] = v
        if "Q" in data and "A" in data and "B" in data and "C" in data and "D" in data and "CORRECT" in data:
            c = data["CORRECT"].upper()
            if c in ("A", "B", "C", "D"):
                items.append((data["Q"], data["A"], data["B"], data["C"], data["D"], c))
    return items


@admin_only
async def edquiz_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    raw = (update.effective_message.text or "").strip()
    items = parse_quiz_blocks(raw)
    if not items:
        await update.effective_message.reply_text("Quiz format မမှန်ပါ။ နမူနာအတိုင်း ပြန်ပို့ပါ။")
        return EDQUIZ
    db.add_quiz_questions(items)
    await update.effective_message.reply_text(f"✅ Quiz ({len(items)}) ခု ထည့်ပြီးပါပြီ။")
    return ConversationHandler.END


# =========================
# Admin: /edpoint <user_id> <score>
# =========================
@admin_only
async def edpoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    args = context.args
    if len(args) < 2 or (not args[0].isdigit()) or (not re.match(r"^-?\d+$", args[1])):
        await update.effective_message.reply_text("အသုံးပြုနည်း: /edpoint <user_id> <score>\nဥပမာ: /edpoint 12345678 10")
        return
    user_id = int(args[0])
    score = int(args[1])
    # name unknown; keep user_id as name if not found
    name = f"User {user_id}"
    username = None
    db.set_score(user_id, name, username, score)
    await update.effective_message.reply_text("✅ Score set ပြီးပါပြီ။")


# =========================
# Admin: /broadcast (conversation)
# =========================
@admin_only
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(
        "📣 Broadcast လုပ်မည့် message ကို <b>နောက် message</b> အဖြစ် ပို့ပါ။\n"
        "Text/Photo (caption)/Document အားလုံး OK.\nCancel: /cancel",
        parse_mode=ParseMode.HTML,
    )
    return BROADCAST


@admin_only
async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    groups = db.list_groups()
    if not groups:
        await update.effective_message.reply_text("Group စာရင်း မရှိသေးပါ။ Bot ကို group ထဲထည့်ပြီး message တစ်ချို့လာမှ stats ထဲဝင်ပါမယ်။")
        return ConversationHandler.END

    msg = update.effective_message
    ok = 0
    fail = 0

    for g in groups:
        gid = int(g["chat_id"])
        try:
            # Copy message preserves media/caption nicely
            await msg.copy(chat_id=gid)
            ok += 1
        except Exception:
            fail += 1

    await update.effective_message.reply_text(f"✅ Broadcast ပြီးပါပြီ။ Success: {ok} | Fail: {fail}")
    return ConversationHandler.END


# =========================
# Admin: /stats
# =========================
@admin_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    s = db.stats()
    groups = db.list_groups()
    lines = [
        "📊 <b>Bot Stats</b>\n",
        f"• Users (scored): <b>{s['users']}</b>",
        f"• Chats tracked: <b>{s['chats']}</b>",
        f"• Groups: <b>{s['groups']}</b>\n",
        "<b>Groups List</b>:"
    ]
    for g in groups[:20]:
        lines.append(f"• <code>{g['chat_id']}</code> — {g['title'] or '-'}")
    if len(groups) > 20:
        lines.append(f"... and {len(groups)-20} more")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# =========================
# Admin: /backup
# =========================
@admin_only
async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    if not os.path.exists(DB_PATH):
        await update.effective_message.reply_text("DB file မတွေ့ပါ။")
        return
    await update.effective_message.reply_document(
        document=InputFile(DB_PATH),
        filename=os.path.basename(DB_PATH),
        caption="🗄️ Backup DB file",
    )


# =========================
# Admin: /restore (conversation)
# =========================
@admin_only
async def restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(
        "♻️ Restore လုပ်ရန် .db file ကို <b>နောက် message</b> အဖြစ် Document အနေနဲ့ ပို့ပါ။\n"
        "⚠️ လက်ရှိ DB ကို အစားထိုးပါမယ်။\nCancel: /cancel",
        parse_mode=ParseMode.HTML,
    )
    return RESTORE


@admin_only
async def restore_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    doc = update.effective_message.document
    if not doc:
        await update.effective_message.reply_text("DB file (Document) မတွေ့ပါ။ ပြန်ပို့ပါ။")
        return RESTORE

    if not doc.file_name.lower().endswith(".db"):
        await update.effective_message.reply_text("⚠️ .db file ပဲ ပို့ပါ။")
        return RESTORE

    # download to temp then replace
    tmp_path = f"{DB_PATH}.restore_tmp"
    f = await doc.get_file()
    await f.download_to_drive(custom_path=tmp_path)

    # quick open test
    try:
        con = sqlite3.connect(tmp_path)
        con.execute("SELECT name FROM sqlite_master LIMIT 1;").fetchone()
        con.close()
    except Exception:
        os.remove(tmp_path)
        await update.effective_message.reply_text("❌ DB file မမှန်ပါ။")
        return ConversationHandler.END

    # replace
    shutil.copyfile(tmp_path, DB_PATH)
    os.remove(tmp_path)

    # re-init (tables exist check)
    global db
    db = DB(DB_PATH)

    await update.effective_message.reply_text("✅ Restore ပြီးပါပြီ။")
    return ConversationHandler.END


# =========================
# Admin: /allclear (confirm)
# =========================
@admin_only
async def allclear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ YES, DELETE ALL", callback_data=ALLCLEAR_CONFIRM)],
        [InlineKeyboardButton("Cancel", callback_data="cancel_allclear")]
    ])
    await update.effective_message.reply_text(
        "⚠️ Data အကုန်ဖျက်မလား?\n"
        "Contacts/Verses/Events/Birthdays/Pray/Quiz/Scores/Votes/Chats tracking အကုန်ဖျက်ပါမယ်။",
        reply_markup=kb
    )


async def allclear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    q = update.callback_query
    user = update.effective_user
    if not db.is_admin(user.id):
        await q.answer("Admin မဟုတ်ပါ။", show_alert=True)
        return
    await q.answer()
    if q.data == ALLCLEAR_CONFIRM:
        db.all_clear()
        await q.edit_message_text("✅ All data cleared.")
    else:
        await q.edit_message_text("Cancelled.")


# =========================
# Admin: /delete <type> <amount>
# types: verse_morning, verse_night, quiz, events, birthday, pray
# =========================
@admin_only
async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    args = context.args
    if len(args) < 2 or not args[1].isdigit():
        await update.effective_message.reply_text(
            "အသုံးပြုနည်း: /delete <type> <amount>\n"
            "type: verse_morning | verse_night | quiz | events | birthday | pray\n"
            "ဥပမာ: /delete quiz 10"
        )
        return
    tp = args[0].lower()
    amount = int(args[1])
    if amount <= 0:
        await update.effective_message.reply_text("amount သတ်မှတ်ချက် မမှန်ပါ။")
        return

    if tp == "verse_morning":
        n = db.delete_last_verses("morning", amount)
    elif tp == "verse_night":
        n = db.delete_last_verses("night", amount)
    elif tp == "quiz":
        n = db.delete_last_quiz(amount)
    elif tp == "events":
        n = db.delete_last_events(amount)
    elif tp == "birthday":
        n = db.delete_last_birthdays(amount)
    elif tp == "pray":
        n = db.delete_last_pray(amount)
    else:
        await update.effective_message.reply_text("type မမှန်ပါ။")
        return

    await update.effective_message.reply_text(f"✅ Deleted: {n} item(s).")


# =========================
# Admin: /edadmin <id> (toggle)
# =========================
@admin_only
async def edadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    args = context.args
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("အသုံးပြုနည်း: /edadmin <user_id>")
        return
    uid = int(args[0])
    msg = db.toggle_admin(uid)
    await update.effective_message.reply_text(msg)


# =========================
# Admin: /edvote (conversation)
# =========================
@admin_only
async def edvote_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text(
        "🗳️ Vote သတ်မှတ်ရန်\n"
        "Format:\n"
        "Question: ...\n"
        "Options:\n"
        "- Name1\n"
        "- Name2\n"
        "- Name3\n"
        "(၃-၅ ခု)\n\n"
        "Message တစ်ခုတည်းနဲ့ ပို့ပါ။\nCancel: /cancel"
    )
    return EDVOTE


@admin_only
async def edvote_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    chat = update.effective_chat
    raw = (update.effective_message.text or "").strip()

    qmatch = re.search(r"(?im)^question:\s*(.+)$", raw)
    if not qmatch:
        await update.effective_message.reply_text("Question: ... မတွေ့ပါ။")
        return EDVOTE
    question = qmatch.group(1).strip()

    opts = []
    in_opts = False
    for line in raw.splitlines():
        ln = line.strip()
        if re.match(r"(?im)^options:\s*$", ln):
            in_opts = True
            continue
        if in_opts:
            m = re.match(r"^-+\s*(.+)$", ln)
            if m:
                opts.append(m.group(1).strip())

    opts = [o for o in opts if o]
    if not (3 <= len(opts) <= 5):
        await update.effective_message.reply_text("Options ၃-၅ ခု လိုပါတယ်။")
        return EDVOTE

    vote_id = db.set_vote(chat.id, question, opts)
    await update.effective_message.reply_text(f"✅ Vote set ပြီးပါပြီ။ (/vote)\nVoteID: {vote_id}")
    return ConversationHandler.END


# =========================
# Cancel conversation
# =========================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_update(update)
    await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END


# =========================
# App Setup
# =========================
def build_app():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not set. Put it in .env")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("helps", helps))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("contact", contact))
    app.add_handler(CommandHandler("verse", verse))
    app.add_handler(CommandHandler("events", events))
    app.add_handler(CommandHandler("birthday", birthday))
    app.add_handler(CommandHandler("pray", pray))
    app.add_handler(CommandHandler("praylist", praylist))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CallbackQueryHandler(quiz_callback, pattern=r"^quiz:\d+:[ABCD]$"))
    app.add_handler(CommandHandler("tops", tops))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("all", all_mention))
    app.add_handler(CommandHandler("vote", vote))
    app.add_handler(CallbackQueryHandler(vote_callback, pattern=r"^(vote:|vote_result:)"))

    # Admin commands (some are conversations)
    app.add_handler(CommandHandler("edit", edit))
    app.add_handler(CommandHandler("set", set_auto_quiz))
    app.add_handler(CommandHandler("edpoint", edpoint))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(CommandHandler("allclear", allclear))
    app.add_handler(CallbackQueryHandler(allclear_callback, pattern=r"^(ALLCLEAR_CONFIRM|cancel_allclear)$"))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("edadmin", edadmin))

    # Conversations
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edabout", edabout_start)],
        states={EDABOUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edabout_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edcontact", edcontact_start)],
        states={EDCONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edcontact_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edverse", edverse_start)],
        states={EDVERSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edverse_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edevents", edevents_start)],
        states={EDEVENTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, edevents_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edbirthday", edbirthday_start)],
        states={EDBIRTHDAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, edbirthday_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edquiz", edquiz_start)],
        states={EDQUIZ: [MessageHandler(filters.TEXT & ~filters.COMMAND, edquiz_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={BROADCAST: [MessageHandler(~filters.COMMAND, broadcast_send)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("restore", restore_start)],
        states={RESTORE: [MessageHandler(filters.Document.ALL, restore_receive)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edvote", edvote_start)],
        states={EDVOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edvote_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    # Auto quiz message counter (must be near end)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_any_message))

    return app


def main():
    app = build_app()
    log.info("Bot started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
