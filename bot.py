#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import html
import shutil
import sqlite3
import logging
import datetime as dt
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple

from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    Defaults,
)

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ChurchCommunityBot")

# ---------------------------
# ENV
# ---------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_ENV = os.getenv("ADMIN_IDS", "").strip()

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN မရှိပါ။ .env ထဲမှာ BOT_TOKEN ထည့်ပေးပါ။")

def _parse_admin_ids(raw: str) -> List[int]:
    ids: List[int] = []
    if not raw:
        return ids
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit():
            ids.append(int(x))
    return ids

BOOTSTRAP_ADMIN_IDS: List[int] = _parse_admin_ids(ADMIN_IDS_ENV)

# ---------------------------
# DB
# ---------------------------
DB_FILE = os.getenv("DB_FILE", "church_community.db")

def utc_now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def today_ymd() -> dt.date:
    return dt.date.today()

def safe_full_name(first: Optional[str], last: Optional[str]) -> str:
    f = (first or "").strip()
    l = (last or "").strip()
    name = (f + " " + l).strip()
    return name if name else "Unknown"

def mention_html(user_id: int, name: str) -> str:
    # Telegram HTML mention
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'

class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def _exec(self, q: str, args: Tuple[Any, ...] = ()) -> sqlite3.Cursor:
        cur = self.conn.cursor()
        cur.execute(q, args)
        self.conn.commit()
        return cur

    def _init(self) -> None:
        self._exec("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS admins(
            user_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS chats(
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            type TEXT,
            added_at TEXT
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            last_seen TEXT,
            PRIMARY KEY(user_id, chat_id)
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS about(
            id INTEGER PRIMARY KEY CHECK (id = 1),
            text TEXT NOT NULL
        )
        """)
        self._exec("INSERT OR IGNORE INTO about(id, text) VALUES(1, ?)", (
            "အသင်းတော်/လူငယ်အဖွဲ့ အကြောင်းကို Admin က /edabout နဲ့ ထည့်သွင်းနိုင်ပါတယ်။",
        ))

        self._exec("""
        CREATE TABLE IF NOT EXISTS contacts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS verses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vtype TEXT NOT NULL,   -- morning | night
            text TEXT NOT NULL
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT,       -- YYYY-MM-DD (optional)
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS birthdays(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            day INTEGER NOT NULL,
            month INTEGER NOT NULL,
            note TEXT
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS prayers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS quiz(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            options_json TEXT NOT NULL,  -- ["A","B","C","D"]
            answer_index INTEGER NOT NULL, -- 0..3
            explanation TEXT,
            created_at TEXT NOT NULL
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS quiz_answers(
            quiz_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            chosen_index INTEGER NOT NULL,
            is_correct INTEGER NOT NULL,
            answered_at TEXT NOT NULL,
            PRIMARY KEY(quiz_id, chat_id, user_id)
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS scores(
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            score INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, user_id)
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS quiz_settings(
            chat_id INTEGER PRIMARY KEY,
            threshold INTEGER NOT NULL DEFAULT 0,
            msg_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS votes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            topic TEXT NOT NULL,
            options_json TEXT NOT NULL, -- ["name1","name2",...]
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """)
        self._exec("""
        CREATE TABLE IF NOT EXISTS vote_votes(
            vote_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            option_index INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(vote_id, chat_id, user_id)
        )
        """)

        # Bootstrap admins
        for aid in BOOTSTRAP_ADMIN_IDS:
            self._exec("INSERT OR IGNORE INTO admins(user_id, enabled) VALUES(?,1)", (aid,))

    # settings
    def get_setting_int(self, key: str, default: int) -> int:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return int(row["value"])
        except Exception:
            return default

    def set_setting(self, key: str, value: str) -> None:
        self._exec("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # admin
    def is_admin(self, user_id: int) -> bool:
        row = self.conn.execute("SELECT enabled FROM admins WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and int(row["enabled"]) == 1)

    def toggle_admin(self, user_id: int) -> bool:
        row = self.conn.execute("SELECT enabled FROM admins WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            self._exec("INSERT INTO admins(user_id, enabled) VALUES(?,1)", (user_id,))
            return True
        enabled = int(row["enabled"])
        new_val = 0 if enabled == 1 else 1
        self._exec("UPDATE admins SET enabled=? WHERE user_id=?", (new_val, user_id))
        return new_val == 1

    def list_admins(self) -> List[int]:
        rows = self.conn.execute("SELECT user_id FROM admins WHERE enabled=1 ORDER BY user_id").fetchall()
        return [int(r["user_id"]) for r in rows]

    # chats/users
    def upsert_chat(self, chat_id: int, title: Optional[str], ctype: str) -> None:
        self._exec(
            "INSERT INTO chats(chat_id,title,type,added_at) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, type=excluded.type",
            (chat_id, title or "", ctype, utc_now_iso())
        )

    def upsert_user(self, chat_id: int, user_id: int, username: Optional[str], full_name: str) -> None:
        self._exec(
            "INSERT INTO users(user_id,chat_id,username,full_name,last_seen) VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id,chat_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name, last_seen=excluded.last_seen",
            (user_id, chat_id, username or "", full_name, utc_now_iso())
        )

    def chat_ids(self) -> List[int]:
        rows = self.conn.execute("SELECT chat_id FROM chats ORDER BY chat_id").fetchall()
        return [int(r["chat_id"]) for r in rows]

    def users_in_chat(self, chat_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT user_id, username, full_name FROM users WHERE chat_id=? ORDER BY last_seen DESC",
            (chat_id,)
        ).fetchall()

    # about
    def get_about(self) -> str:
        row = self.conn.execute("SELECT text FROM about WHERE id=1").fetchone()
        return row["text"] if row else ""

    def set_about(self, text: str) -> None:
        self._exec("UPDATE about SET text=? WHERE id=1", (text,))

    # contacts
    def replace_contacts(self, items: List[Tuple[str, str]]) -> None:
        self._exec("DELETE FROM contacts")
        for name, phone in items:
            self._exec("INSERT INTO contacts(name,phone) VALUES(?,?)", (name, phone))

    def get_contacts(self) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT name, phone FROM contacts ORDER BY id").fetchall()

    # verses
    def add_verses(self, verses: List[Tuple[str, str]]) -> int:
        added = 0
        for vtype, text in verses:
            vt = vtype.strip().lower()
            if vt not in ("morning", "night"):
                continue
            self._exec("INSERT INTO verses(vtype,text) VALUES(?,?)", (vt, text.strip()))
            added += 1
        return added

    def list_verses(self, vtype: str) -> List[sqlite3.Row]:
        return self.conn.execute("SELECT id, text FROM verses WHERE vtype=? ORDER BY id", (vtype,)).fetchall()

    def delete_verses(self, amount: int) -> int:
        rows = self.conn.execute("SELECT id FROM verses ORDER BY id DESC LIMIT ?", (amount,)).fetchall()
        ids = [int(r["id"]) for r in rows]
        for i in ids:
            self._exec("DELETE FROM verses WHERE id=?", (i,))
        return len(ids)

    # events
    def replace_events(self, items: List[Tuple[Optional[str], str]]) -> None:
        self._exec("DELETE FROM events")
        for d, text in items:
            self._exec(
                "INSERT INTO events(event_date,text,created_at) VALUES(?,?,?)",
                (d or None, text.strip(), utc_now_iso())
            )

    def upcoming_events(self) -> List[sqlite3.Row]:
        today = today_ymd().isoformat()
        # events with date >= today first, then no-date items
        return self.conn.execute(
            """
            SELECT event_date, text FROM events
            ORDER BY
                CASE WHEN event_date IS NULL OR event_date='' THEN 1 ELSE 0 END,
                event_date ASC
            """
        ).fetchall()

    # birthdays
    def replace_birthdays(self, items: List[Tuple[str, int, int, Optional[str]]]) -> None:
        self._exec("DELETE FROM birthdays")
        for name, day, month, note in items:
            self._exec("INSERT INTO birthdays(name,day,month,note) VALUES(?,?,?,?)", (name.strip(), day, month, note or ""))

    def birthdays_in_month(self, month: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT name, day, month, note FROM birthdays WHERE month=? ORDER BY day ASC, name ASC",
            (month,)
        ).fetchall()

    # prayers
    def add_pray(self, chat_id: int, user_id: int, username: Optional[str], full_name: str, text: str) -> None:
        self._exec(
            "INSERT INTO prayers(chat_id,user_id,username,full_name,text,created_at) VALUES(?,?,?,?,?,?)",
            (chat_id, user_id, username or "", full_name, text.strip(), utc_now_iso())
        )

    def list_prayers(self, chat_id: int, limit: int = 30) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT username, full_name, text, created_at FROM prayers WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, limit)
        ).fetchall()

    # quiz
    def add_quiz_bulk(self, items: List[Tuple[str, List[str], int, Optional[str]]]) -> int:
        added = 0
        for q, opts, ans, exp in items:
            if len(opts) != 4:
                continue
            if ans < 0 or ans > 3:
                continue
            self._exec(
                "INSERT INTO quiz(question,options_json,answer_index,explanation,created_at) VALUES(?,?,?,?,?)",
                (q.strip(), json.dumps(opts, ensure_ascii=False), ans, (exp or "").strip(), utc_now_iso())
            )
            added += 1
        return added

    def delete_quiz(self, amount: int) -> int:
        rows = self.conn.execute("SELECT id FROM quiz ORDER BY id DESC LIMIT ?", (amount,)).fetchall()
        ids = [int(r["id"]) for r in rows]
        for i in ids:
            self._exec("DELETE FROM quiz WHERE id=?", (i,))
        return len(ids)

    def random_quiz(self) -> Optional[sqlite3.Row]:
        row = self.conn.execute("SELECT id, question, options_json, answer_index, explanation FROM quiz ORDER BY RANDOM() LIMIT 1").fetchone()
        return row

    def has_answered_quiz(self, quiz_id: int, chat_id: int, user_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM quiz_answers WHERE quiz_id=? AND chat_id=? AND user_id=?",
            (quiz_id, chat_id, user_id)
        ).fetchone()
        return bool(row)

    def record_quiz_answer(self, quiz_id: int, chat_id: int, user_id: int, chosen: int, is_correct: int) -> None:
        self._exec(
            "INSERT OR IGNORE INTO quiz_answers(quiz_id,chat_id,user_id,chosen_index,is_correct,answered_at) VALUES(?,?,?,?,?,?)",
            (quiz_id, chat_id, user_id, chosen, is_correct, utc_now_iso())
        )

    def add_points(self, chat_id: int, user_id: int, username: Optional[str], full_name: str, delta: int) -> None:
        row = self.conn.execute("SELECT score FROM scores WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
        if not row:
            self._exec(
                "INSERT INTO scores(chat_id,user_id,username,full_name,score,updated_at) VALUES(?,?,?,?,?,?)",
                (chat_id, user_id, username or "", full_name, max(0, delta), utc_now_iso())
            )
            return
        score = int(row["score"])
        new_score = max(0, score + delta)
        self._exec(
            "UPDATE scores SET score=?, username=?, full_name=?, updated_at=? WHERE chat_id=? AND user_id=?",
            (new_score, username or "", full_name, utc_now_iso(), chat_id, user_id)
        )

    def set_points(self, chat_id: int, user_id: int, username: Optional[str], full_name: str, score: int) -> None:
        self._exec(
            "INSERT INTO scores(chat_id,user_id,username,full_name,score,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(chat_id,user_id) DO UPDATE SET score=excluded.score, username=excluded.username, full_name=excluded.full_name, updated_at=excluded.updated_at",
            (chat_id, user_id, username or "", full_name, max(0, score), utc_now_iso())
        )

    def top_scores(self, chat_id: int, limit: int = 10) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT username, full_name, score FROM scores WHERE chat_id=? ORDER BY score DESC, updated_at ASC LIMIT ?",
            (chat_id, limit)
        ).fetchall()

    # quiz auto settings
    def set_threshold(self, chat_id: int, n: int) -> None:
        self._exec(
            "INSERT INTO quiz_settings(chat_id,threshold,msg_count,updated_at) VALUES(?,?,0,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET threshold=excluded.threshold, updated_at=excluded.updated_at",
            (chat_id, max(0, n), utc_now_iso())
        )

    def get_threshold(self, chat_id: int) -> int:
        row = self.conn.execute("SELECT threshold FROM quiz_settings WHERE chat_id=?", (chat_id,)).fetchone()
        return int(row["threshold"]) if row else 0

    def inc_msg_count(self, chat_id: int) -> Tuple[int, int]:
        # returns (threshold, msg_count)
        row = self.conn.execute("SELECT threshold, msg_count FROM quiz_settings WHERE chat_id=?", (chat_id,)).fetchone()
        if not row:
            self._exec("INSERT INTO quiz_settings(chat_id,threshold,msg_count,updated_at) VALUES(?,?,?,?)", (chat_id, 0, 1, utc_now_iso()))
            return (0, 1)
        threshold = int(row["threshold"])
        msg_count = int(row["msg_count"]) + 1
        self._exec("UPDATE quiz_settings SET msg_count=?, updated_at=? WHERE chat_id=?", (msg_count, utc_now_iso(), chat_id))
        return (threshold, msg_count)

    def reset_msg_count(self, chat_id: int) -> None:
        self._exec("UPDATE quiz_settings SET msg_count=0, updated_at=? WHERE chat_id=?", (utc_now_iso(), chat_id))

    # reports
    def add_report(self, chat_id: int, user_id: int, username: Optional[str], full_name: str, text: str) -> None:
        self._exec(
            "INSERT INTO reports(chat_id,user_id,username,full_name,text,created_at) VALUES(?,?,?,?,?,?)",
            (chat_id, user_id, username or "", full_name, text.strip(), utc_now_iso())
        )

    # votes
    def set_vote(self, chat_id: int, topic: str, options: List[str]) -> int:
        # deactivate previous
        self._exec("UPDATE votes SET is_active=0 WHERE chat_id=?", (chat_id,))
        cur = self._exec(
            "INSERT INTO votes(chat_id,topic,options_json,is_active,created_at) VALUES(?,?,?,?,?)",
            (chat_id, topic.strip(), json.dumps(options, ensure_ascii=False), 1, utc_now_iso())
        )
        return int(cur.lastrowid)

    def get_active_vote(self, chat_id: int) -> Optional[sqlite3.Row]:
        row = self.conn.execute(
            "SELECT id, topic, options_json FROM votes WHERE chat_id=? AND is_active=1 ORDER BY id DESC LIMIT 1",
            (chat_id,)
        ).fetchone()
        return row

    def cast_vote(self, vote_id: int, chat_id: int, user_id: int, option_index: int) -> bool:
        try:
            self._exec(
                "INSERT OR REPLACE INTO vote_votes(vote_id,chat_id,user_id,option_index,created_at) VALUES(?,?,?,?,?)",
                (vote_id, chat_id, user_id, option_index, utc_now_iso())
            )
            return True
        except Exception:
            return False

    def vote_results(self, vote_id: int, chat_id: int) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT option_index, COUNT(*) AS c
            FROM vote_votes
            WHERE vote_id=? AND chat_id=?
            GROUP BY option_index
            ORDER BY c DESC
            """,
            (vote_id, chat_id)
        ).fetchall()


db = DB(DB_FILE)

# ---------------------------
# Pending admin actions (in-memory)
# ---------------------------
@dataclass
class PendingAction:
    action: str
    chat_id: int
    extra: Optional[Dict[str, Any]] = None

PENDING: Dict[int, PendingAction] = {}

# ---------------------------
# Helpers
# ---------------------------
def is_group_chat(update: Update) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    return chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

async def send_typing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

def require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not db.is_admin(user.id):
            await update.effective_message.reply_text("⛔ Admin only command ဖြစ်ပါတယ်။")
            return
        return await func(update, context)
    return wrapper

def chunk_text(text: str, limit: int = 3500) -> List[str]:
    parts: List[str] = []
    buf = ""
    for line in text.splitlines(True):
        if len(buf) + len(line) > limit:
            if buf.strip():
                parts.append(buf)
            buf = line
        else:
            buf += line
    if buf.strip():
        parts.append(buf)
    return parts

def parse_key_value_lines(raw: str) -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if "-" in s:
            left, right = s.split("-", 1)
            name = left.strip()
            value = right.strip()
            if name and value:
                items.append((name, value))
    return items

# ---------------------------
# Track chats/users on any message
# ---------------------------
async def track_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    db.upsert_chat(chat.id, chat.title, chat.type)
    db.upsert_user(
        chat_id=chat.id,
        user_id=user.id,
        username=user.username,
        full_name=safe_full_name(user.first_name, user.last_name)
    )

# ---------------------------
# User Commands
# ---------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    msg = (
        "<b>🙏 Church Community Bot မှ ကြိုဆိုပါတယ်</b>\n\n"
        "အသုံးပြုလို့ရတဲ့ command များကို <b>/helps</b> နဲ့ကြည့်နိုင်ပါတယ်။\n"
        "အသင်းတော်အကြောင်း <b>/about</b>\n"
        "တာဝန်ခံ ဖုန်းနံပါတ် <b>/contact</b>\n"
        "Daily Verse <b>/verse</b>\n"
        "Upcoming Events <b>/events</b>\n"
        "Birthday List <b>/birthday</b>\n"
        "Prayer Request <b>/pray</b>\n"
        "Quiz <b>/quiz</b>\n"
        "Top Scores <b>/tops</b>\n"
        "Report to Admin <b>/report</b>\n"
    )
    await update.effective_message.reply_text(msg)

async def cmd_helps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    text = (
        "<b>📌 User Commands</b>\n"
        "/start - Bot စတင်အသုံးပြုရန်\n"
        "/helps - Command list\n"
        "/about - အသင်းတော်/လူငယ်အဖွဲ့အကြောင်း\n"
        "/contact - တာဝန်ခံ ဖုန်းနံပါတ်များ\n"
        "/verse - ယနေ့ Morning/Night Daily Verse\n"
        "/events - လာမည့် အသင်းတော်အစီအစဉ်များ\n"
        "/birthday - ယခုလ မွေးနေ့ရှင်များ\n"
        "/pray &lt;text&gt; - ဆုတောင်းခံချက် ပို့ရန်\n"
        "/praylist - ဆုတောင်းခံချက် စာရင်း\n"
        "/quiz - Quiz ဖြေဆိုရန်\n"
        "/tops - Quiz Ranking (Name + Score)\n"
        "/report &lt;text&gt; - Admin ထံ အကြောင်းကြားရန်\n"
        "/all - Group ထဲက tracked members များကို mention ခေါ်ရန်\n"
        "/vote - မဲပေးရန် (active vote ရှိလျှင်)\n"
    )
    await update.effective_message.reply_text(text)

async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    about = db.get_about()
    await update.effective_message.reply_text(f"<b>📖 About</b>\n\n{about}")

async def cmd_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    rows = db.get_contacts()
    if not rows:
        await update.effective_message.reply_text("Contact မရှိသေးပါ။ Admin က /edcontact နဲ့ ထည့်ပေးနိုင်ပါတယ်။")
        return

    lines = ["<b>☎️ Contacts</b>\n"]
    for r in rows:
        lines.append(f"• <b>{html.escape(r['name'])}</b> — <code>{html.escape(r['phone'])}</code>")
    await update.effective_message.reply_text("\n".join(lines))

async def cmd_verse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    morning = db.list_verses("morning")
    night = db.list_verses("night")

    if not morning and not night:
        await update.effective_message.reply_text("Verse မရှိသေးပါ။ Admin က /edverse နဲ့ ထည့်နိုင်ပါတယ်။")
        return

    d = today_ymd()
    day_index = d.toordinal()

    def pick(rows: List[sqlite3.Row]) -> Optional[str]:
        if not rows:
            return None
        i = day_index % len(rows)
        return str(rows[i]["text"])

    m = pick(morning)
    n = pick(night)

    msg = f"<b>📜 Daily Verse</b>\n<b>Date:</b> <code>{d.isoformat()}</code>\n\n"
    if m:
        msg += f"<b>🌅 Morning</b>\n{m}\n\n"
    if n:
        msg += f"<b>🌙 Night</b>\n{n}\n"
    await update.effective_message.reply_text(msg)

async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    rows = db.upcoming_events()
    if not rows:
        await update.effective_message.reply_text("Events မရှိသေးပါ။ Admin က /edevents နဲ့ ထည့်နိုင်ပါတယ်။")
        return

    lines = ["<b>🗓 Upcoming Events</b>\n"]
    for r in rows[:15]:
        d = (r["event_date"] or "").strip()
        if d:
            lines.append(f"• <b>{html.escape(d)}</b> — {html.escape(r['text'])}")
        else:
            lines.append(f"• {html.escape(r['text'])}")
    await update.effective_message.reply_text("\n".join(lines))

async def cmd_birthday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    m = today_ymd().month
    rows = db.birthdays_in_month(m)
    if not rows:
        await update.effective_message.reply_text("ဒီလအတွက် Birthday စာရင်းမရှိသေးပါ။ Admin က /edbirthday နဲ့ ထည့်နိုင်ပါတယ်။")
        return

    lines = [f"<b>🎂 Birthdays (Month {m})</b>\n"]
    for r in rows:
        note = (r["note"] or "").strip()
        extra = f" — {html.escape(note)}" if note else ""
        lines.append(f"• <b>{html.escape(r['name'])}</b> — <code>{int(r['day']):02d}/{int(r['month']):02d}</code>{extra}")
    await update.effective_message.reply_text("\n".join(lines))

async def cmd_pray(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)

    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.effective_message.reply_text("အသုံးပြုနည်း: <code>/pray ဆုတောင်းချင်တဲ့အချက်</code>")
        return

    full_name = safe_full_name(user.first_name, user.last_name)
    db.add_pray(chat.id, user.id, user.username, full_name, text)

    await update.effective_message.reply_text(
        "<b>✅ Prayer Request လက်ခံပြီးပါပြီ</b>\n"
        "Admin/အဖွဲ့ဝင်များ ဆုတောင်းပေးနိုင်ရန် /praylist မှာကြည့်နိုင်ပါတယ်။"
    )

async def cmd_praylist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    chat = update.effective_chat
    if not chat:
        return

    rows = db.list_prayers(chat.id, limit=30)
    if not rows:
        await update.effective_message.reply_text("Prayer Request မရှိသေးပါ။")
        return

    lines = ["<b>🙏 Prayer List (Latest)</b>\n"]
    for r in rows[::-1]:
        name = (r["full_name"] or "").strip()
        uname = (r["username"] or "").strip()
        who = html.escape(name)
        if uname:
            who += f" (<code>@{html.escape(uname)}</code>)"
        lines.append(f"• <b>{who}</b>\n  {html.escape(r['text'])}")
    msg = "\n".join(lines)
    for part in chunk_text(msg):
        await update.effective_message.reply_text(part)

async def send_quiz_to_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    row = db.random_quiz()
    if not row:
        await context.bot.send_message(chat_id=chat_id, text="Quiz မရှိသေးပါ။ Admin က /edquiz နဲ့ထည့်ပါ။")
        return

    quiz_id = int(row["id"])
    question = str(row["question"])
    options = json.loads(row["options_json"])

    keyboard = [
        [InlineKeyboardButton(f"A) {options[0]}", callback_data=f"quiz:{quiz_id}:0"),
         InlineKeyboardButton(f"B) {options[1]}", callback_data=f"quiz:{quiz_id}:1")],
        [InlineKeyboardButton(f"C) {options[2]}", callback_data=f"quiz:{quiz_id}:2"),
         InlineKeyboardButton(f"D) {options[3]}", callback_data=f"quiz:{quiz_id}:3")],
        [InlineKeyboardButton("📊 Ranking (/tops)", callback_data="noop"),
         InlineKeyboardButton("ℹ️ Help (/helps)", callback_data="noop")]
    ]
    text = "<b>🧠 Quiz Time</b>\n\n" + html.escape(question)
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def cmd_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    chat = update.effective_chat
    if not chat:
        return
    await send_quiz_to_chat(chat.id, context)

async def cmd_tops(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    chat = update.effective_chat
    if not chat:
        return

    rows = db.top_scores(chat.id, limit=10)
    if not rows:
        await update.effective_message.reply_text("Score မရှိသေးပါ။ /quiz ဖြေပြီး စတင်ရယူနိုင်ပါတယ်။")
        return

    lines = ["<b>🏆 Top Scores</b>\n"]
    rank = 1
    for r in rows:
        name = (r["full_name"] or "").strip()
        uname = (r["username"] or "").strip()
        who = html.escape(name if name else "Unknown")
        if uname:
            who += f" (<code>@{html.escape(uname)}</code>)"
        lines.append(f"{rank}. <b>{who}</b> — <b>{int(r['score'])}</b>")
        rank += 1
    await update.effective_message.reply_text("\n".join(lines))

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)

    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return

    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.effective_message.reply_text("အသုံးပြုနည်း: <code>/report မိမိအကြောင်းကြားလိုသောအချက်</code>")
        return

    full_name = safe_full_name(user.first_name, user.last_name)
    db.add_report(chat.id, user.id, user.username, full_name, text)

    admins = db.list_admins()
    msg = (
        "<b>📣 New Report</b>\n"
        f"<b>Chat:</b> <code>{chat.id}</code>\n"
        f"<b>From:</b> {mention_html(user.id, full_name)}"
    )
    if user.username:
        msg += f" (<code>@{html.escape(user.username)}</code>)"
    msg += "\n\n" + html.escape(text)

    delivered = 0
    for aid in admins:
        try:
            await context.bot.send_message(chat_id=aid, text=msg)
            delivered += 1
        except Exception:
            continue

    await update.effective_message.reply_text(
        f"✅ Admin ထံ အကြောင်းကြားပြီးပါပြီ။ (sent to {delivered} admin(s))"
    )

async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    chat = update.effective_chat
    if not chat:
        return
    if not is_group_chat(update):
        await update.effective_message.reply_text("/all ကို Group/Supergroup ထဲမှာသာ အသုံးပြုနိုင်ပါတယ်။")
        return

    rows = db.users_in_chat(chat.id)
    if not rows:
        await update.effective_message.reply_text("Member list မရှိသေးပါ။ အဖွဲ့ဝင်တွေ message ပို့ပြီးမှ track လုပ်နိုင်ပါတယ်။")
        return

    # build mention chunks
    mentions: List[str] = []
    for r in rows:
        uid = int(r["user_id"])
        name = (r["full_name"] or "").strip() or (r["username"] or "").strip() or str(uid)
        mentions.append(mention_html(uid, name))

    # send in chunks to avoid 4096 limit
    chunk: List[str] = []
    size = 0
    sent = 0
    for m in mentions:
        if size + len(m) + 2 > 3500:
            text = "<b>📣 @all</b>\n" + " ".join(chunk)
            await update.effective_message.reply_text(text)
            sent += 1
            chunk = [m]
            size = len(m)
        else:
            chunk.append(m)
            size += len(m) + 1

    if chunk:
        text = "<b>📣 @all</b>\n" + " ".join(chunk)
        await update.effective_message.reply_text(text)
        sent += 1

async def cmd_vote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    chat = update.effective_chat
    if not chat:
        return

    row = db.get_active_vote(chat.id)
    if not row:
        await update.effective_message.reply_text("Active vote မရှိသေးပါ။ Admin က /edvote နဲ့ သတ်မှတ်နိုင်ပါတယ်။")
        return

    vote_id = int(row["id"])
    topic = str(row["topic"])
    options = json.loads(row["options_json"])

    keyboard: List[List[InlineKeyboardButton]] = []
    for i, opt in enumerate(options):
        keyboard.append([InlineKeyboardButton(opt, callback_data=f"vote:{vote_id}:{i}")])

    keyboard.append([
        InlineKeyboardButton("📊 View Results", callback_data=f"vote_res:{vote_id}"),
    ])

    await update.effective_message.reply_text(
        f"<b>🗳 Vote</b>\n\n<b>Topic:</b> {html.escape(topic)}\n\nရွေးချယ်ပြီး မဲပေးနိုင်ပါတယ်။",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# ---------------------------
# Admin Commands
# ---------------------------
@require_admin
async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    text = (
        "<b>🛠 Admin Commands</b>\n"
        "/edit - Admin commands list\n"
        "/edabout - About ပြင်ရန် (next message)\n"
        "/edcontact - Contacts ထည့်/ပြင်ရန် (next message)\n"
        "/edverse - Verses ထည့်ရန် (next message, multiple)\n"
        "/edevents - Events ထည့်/ပြင်ရန် (next message)\n"
        "/edbirthday - Birthday list ထည့်/ပြင်ရန် (next message)\n"
        "/set &lt;number&gt; - Auto Quiz message threshold သတ်မှတ်ရန် (ဒီ chat)\n"
        "/edquiz - Quiz မေးခွန်း/အဖြေများ ထည့်ရန် (next message)\n"
        "/edpoint &lt;username_or_id&gt; &lt;score&gt; - Score သတ်မှတ်/ပြင်ရန်\n"
        "/broadcast - Group အားလုံးထံ message/photo broadcast (next message)\n"
        "/stats - Users/Groups စာရင်း\n"
        "/backup - Database backup file ထုတ်ပေးရန်\n"
        "/restore - Database file ဖြင့် restore (next message = .db document)\n"
        "/allclear - Data အားလုံးဖျက် (confirmation လို)\n"
        "/delete &lt;type&gt; &lt;amount&gt; - verse|quiz ဖျက်\n"
        "/edadmin &lt;id&gt; - Admin add/remove toggle\n"
        "/edvote - Vote topic/options သတ်မှတ် (next message)\n"
    )
    await update.effective_message.reply_text(text)

@require_admin
async def cmd_edabout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    PENDING[update.effective_user.id] = PendingAction(action="edabout", chat_id=update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>✍️ About Edit Mode</b>\n\n"
        "About text ကို next message အနေနဲ့ ပို့ပေးပါ။ (HTML allowed)"
    )

@require_admin
async def cmd_edcontact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    PENDING[update.effective_user.id] = PendingAction(action="edcontact", chat_id=update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>✍️ Contact Edit Mode</b>\n\n"
        "Next message မှာ line တစ်ကြောင်းစီ ဒီပုံစံနဲ့ပို့ပါ:\n"
        "<code>Name - Phone</code>\n\n"
        "ဥပမာ:\n"
        "<code>Leader A - 09xxxxxxxxx\nLeader B - 09yyyyyyyyy</code>"
    )

@require_admin
async def cmd_edverse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    PENDING[update.effective_user.id] = PendingAction(action="edverse", chat_id=update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>✍️ Verse Add Mode</b>\n\n"
        "Next message မှာ line တစ်ကြောင်းစီ ဒီပုံစံနဲ့ပို့ပါ:\n"
        "<code>morning: Verse text</code>\n"
        "<code>night: Verse text</code>\n\n"
        "တစ်ကြောင်းချင်းအများကြီးပို့လို့ရပါတယ်။"
    )

@require_admin
async def cmd_edevents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    PENDING[update.effective_user.id] = PendingAction(action="edevents", chat_id=update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>✍️ Events Edit Mode</b>\n\n"
        "Next message မှာ line တစ်ကြောင်းစီ ဒီပုံစံနဲ့ပို့ပါ:\n"
        "<code>YYYY-MM-DD - Event text</code>\n\n"
        "Date မထည့်ချင်ရင် <code>-</code> မပါဘဲ text လည်းရေးနိုင်ပါတယ်။"
    )

@require_admin
async def cmd_edbirthday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    PENDING[update.effective_user.id] = PendingAction(action="edbirthday", chat_id=update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>✍️ Birthday Edit Mode</b>\n\n"
        "Next message မှာ line တစ်ကြောင်းစီ ဒီပုံစံနဲ့ပို့ပါ:\n"
        "<code>Name - DD/MM</code>\n"
        "Note ထည့်ချင်ရင်:\n"
        "<code>Name - DD/MM - Note</code>\n\n"
        "ဥပမာ:\n"
        "<code>Mg Mg - 05/02\nSu Su - 12/02 - Youth Member</code>"
    )

@require_admin
async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)

    chat = update.effective_chat
    if not chat:
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("အသုံးပြုနည်း: <code>/set 50</code> (message 50 ပြည့်တိုင်း Auto Quiz)")
        return

    n = int(context.args[0])
    db.set_threshold(chat.id, n)
    await update.effective_message.reply_text(
        f"✅ ဒီ chat အတွက် Auto Quiz threshold ကို <b>{n}</b> သတ်မှတ်ပြီးပါပြီ။\n"
        "0 ထားလိုက်ရင် Auto Quiz ပိတ်ပါမယ်။"
    )

@require_admin
async def cmd_edquiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    PENDING[update.effective_user.id] = PendingAction(action="edquiz", chat_id=update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>✍️ Quiz Add Mode</b>\n\n"
        "Next message မှာ Quiz အများကြီးကို block အလိုက်ထည့်နိုင်ပါတယ်။\n\n"
        "Format (block တစ်ခု = Quiz တစ်ခု):\n"
        "<code>Q: Question text\n"
        "A) option1\n"
        "B) option2\n"
        "C) option3\n"
        "D) option4\n"
        "ANS: B\n"
        "EXP: optional explanation</code>\n\n"
        "Quiz တစ်ခုနဲ့တစ်ခုကြားမှာ blank line ထားပေးပါ။"
    )

@require_admin
async def cmd_edpoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    chat = update.effective_chat
    if not chat:
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text("အသုံးပြုနည်း: <code>/edpoint username_or_id score</code>")
        return

    target = context.args[0].strip()
    score_raw = context.args[1].strip()
    if not score_raw.lstrip("-").isdigit():
        await update.effective_message.reply_text("Score က number ဖြစ်ရပါမယ်။")
        return

    score = int(score_raw)
    # try resolve by id
    user_id: Optional[int] = int(target) if target.isdigit() else None
    username = None
    full_name = "Unknown"

    if user_id is None:
        # resolve from tracked users in this chat by username
        rows = db.users_in_chat(chat.id)
        for r in rows:
            u = (r["username"] or "").strip()
            if u and u.lower() == target.lstrip("@").lower():
                user_id = int(r["user_id"])
                username = u
                full_name = (r["full_name"] or "").strip() or "Unknown"
                break

    if user_id is None:
        await update.effective_message.reply_text("User ကို မတွေ့ပါ။ (ဒီ group ထဲမှာ tracked ဖြစ်ဖို့လိုပါတယ်)")
        return

    db.set_points(chat.id, user_id, username, full_name, score)
    await update.effective_message.reply_text(f"✅ Score သတ်မှတ်ပြီးပါပြီ: <b>{score}</b>")

@require_admin
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    PENDING[update.effective_user.id] = PendingAction(action="broadcast", chat_id=update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>📢 Broadcast Mode</b>\n\n"
        "Group အားလုံးထံပို့မယ့် message (စာ/ပုံ) ကို next message အဖြစ် ပို့ပေးပါ။\n"
        "Bot က groups အားလုံးကို copy_message နဲ့ပို့ပါမယ်။"
    )

@require_admin
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    chats = db.conn.execute("SELECT chat_id, title, type FROM chats ORDER BY added_at DESC").fetchall()
    users = db.conn.execute("SELECT COUNT(DISTINCT user_id) AS c FROM users").fetchone()
    user_count = int(users["c"]) if users else 0

    lines = [
        "<b>📊 Stats</b>",
        f"• Total tracked users: <b>{user_count}</b>",
        f"• Total chats/groups: <b>{len(chats)}</b>",
        "",
        "<b>Chats</b>"
    ]
    for r in chats[:30]:
        title = (r["title"] or "").strip()
        ctype = (r["type"] or "").strip()
        lines.append(f"• <code>{int(r['chat_id'])}</code> — {html.escape(title)} (<code>{html.escape(ctype)}</code>)")
    await update.effective_message.reply_text("\n".join(lines))

@require_admin
async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    await send_typing(update, context)

    # copy db
    backup_name = f"backup_{dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
    tmp_path = os.path.join(os.getcwd(), backup_name)
    try:
        db.conn.commit()
        shutil.copyfile(DB_FILE, tmp_path)
        await update.effective_message.reply_document(document=open(tmp_path, "rb"), filename=backup_name, caption="✅ Backup DB")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

@require_admin
async def cmd_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    PENDING[update.effective_user.id] = PendingAction(action="restore", chat_id=update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>♻️ Restore Mode</b>\n\n"
        "Restore လုပ်မယ့် <code>.db</code> file ကို next message အဖြစ် Document နဲ့ပို့ပါ။\n"
        "သတိ: Existing data ကို အစားထိုးပါမယ်။"
    )

@require_admin
async def cmd_allclear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    PENDING[update.effective_user.id] = PendingAction(action="allclear_confirm", chat_id=update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>⚠️ ALL CLEAR</b>\n\n"
        "Data အားလုံးကို ဖျက်မယ်ဆိုရင် next message မှာ အတိအကျ <code>CONFIRM</code> လို့ပို့ပါ။"
    )

@require_admin
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    if len(context.args) < 2:
        await update.effective_message.reply_text("အသုံးပြုနည်း: <code>/delete verse 10</code> သို့ <code>/delete quiz 5</code>")
        return

    dtype = context.args[0].strip().lower()
    amount_raw = context.args[1].strip()
    if not amount_raw.isdigit():
        await update.effective_message.reply_text("amount က number ဖြစ်ရပါမယ်။")
        return

    amount = int(amount_raw)
    if amount <= 0:
        await update.effective_message.reply_text("amount > 0 ဖြစ်ရပါမယ်။")
        return

    if dtype == "verse":
        n = db.delete_verses(amount)
        await update.effective_message.reply_text(f"✅ Verse ဖျက်ပြီးပါပြီ: {n}")
        return
    if dtype == "quiz":
        n = db.delete_quiz(amount)
        await update.effective_message.reply_text(f"✅ Quiz ဖျက်ပြီးပါပြီ: {n}")
        return

    await update.effective_message.reply_text("type မှာ <code>verse</code> သို့ <code>quiz</code> သာလက်ခံပါတယ်။")

@require_admin
async def cmd_edadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("အသုံးပြုနည်း: <code>/edadmin 123456789</code>")
        return

    uid = int(context.args[0])
    enabled = db.toggle_admin(uid)
    state = "ENABLED ✅" if enabled else "DISABLED ⛔"
    await update.effective_message.reply_text(f"Admin {uid} => {state}")

@require_admin
async def cmd_edvote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)
    PENDING[update.effective_user.id] = PendingAction(action="edvote", chat_id=update.effective_chat.id)
    await update.effective_message.reply_text(
        "<b>✍️ Vote Setup Mode</b>\n\n"
        "Next message format:\n"
        "<code>Topic: ....\n"
        "1) Name One\n"
        "2) Name Two\n"
        "3) Name Three\n"
        "4) Optional\n"
        "5) Optional</code>\n\n"
        "Options 3-5 ခု လက်ခံပါတယ်။"
    )

# ---------------------------
# Pending handler
# ---------------------------
async def handle_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg:
        return False

    pending = PENDING.get(user.id)
    if not pending:
        return False

    # Admin guard for pending actions
    if not db.is_admin(user.id):
        PENDING.pop(user.id, None)
        return False

    action = pending.action

    if action == "edabout":
        text = msg.text or ""
        text = text.strip()
        if not text:
            await msg.reply_text("About text မပါဘူး။ ပြန်ပို့ပါ။")
            return True
        db.set_about(text)
        PENDING.pop(user.id, None)
        await msg.reply_text("✅ About ကို update လုပ်ပြီးပါပြီ။")
        return True

    if action == "edcontact":
        raw = msg.text or ""
        items = parse_key_value_lines(raw)
        if not items:
            await msg.reply_text("Format မမှန်ပါ။ <code>Name - Phone</code> နဲ့ပြန်ပို့ပါ။")
            return True
        db.replace_contacts(items)
        PENDING.pop(user.id, None)
        await msg.reply_text(f"✅ Contacts update လုပ်ပြီးပါပြီ။ (count={len(items)})")
        return True

    if action == "edverse":
        raw = msg.text or ""
        verses: List[Tuple[str, str]] = []
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            if ":" not in s:
                continue
            k, v = s.split(":", 1)
            verses.append((k.strip().lower(), v.strip()))
        if not verses:
            await msg.reply_text("Format မမှန်ပါ။ <code>morning: ...</code> / <code>night: ...</code> နဲ့ပြန်ပို့ပါ။")
            return True
        added = db.add_verses(verses)
        PENDING.pop(user.id, None)
        await msg.reply_text(f"✅ Verse ထည့်ပြီးပါပြီ။ (added={added})")
        return True

    if action == "edevents":
        raw = msg.text or ""
        items: List[Tuple[Optional[str], str]] = []
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            if "-" in s and len(s.split("-", 1)[0].strip()) == 10:
                left, right = s.split("-", 1)
                d = left.strip()
                text = right.strip()
                # basic date check
                try:
                    dt.date.fromisoformat(d)
                    items.append((d, text))
                except Exception:
                    items.append((None, s))
            else:
                items.append((None, s))
        if not items:
            await msg.reply_text("Events မပါဘူး။ ပြန်ပို့ပါ။")
            return True
        db.replace_events(items)
        PENDING.pop(user.id, None)
        await msg.reply_text(f"✅ Events update လုပ်ပြီးပါပြီ။ (count={len(items)})")
        return True

    if action == "edbirthday":
        raw = msg.text or ""
        items: List[Tuple[str, int, int, Optional[str]]] = []
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            parts = [p.strip() for p in s.split("-")]
            if len(parts) < 2:
                continue
            name = parts[0]
            dm = parts[1]
            note = parts[2] if len(parts) >= 3 else ""
            if "/" not in dm:
                continue
            d_str, m_str = [x.strip() for x in dm.split("/", 1)]
            if not (d_str.isdigit() and m_str.isdigit()):
                continue
            day = int(d_str)
            month = int(m_str)
            if day < 1 or day > 31 or month < 1 or month > 12:
                continue
            if name:
                items.append((name, day, month, note))
        if not items:
            await msg.reply_text("Format မမှန်ပါ။ <code>Name - DD/MM</code> နဲ့ပြန်ပို့ပါ။")
            return True
        db.replace_birthdays(items)
        PENDING.pop(user.id, None)
        await msg.reply_text(f"✅ Birthday list update လုပ်ပြီးပါပြီ။ (count={len(items)})")
        return True

    if action == "edquiz":
        raw = msg.text or ""
        blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
        items: List[Tuple[str, List[str], int, Optional[str]]] = []

        def parse_block(b: str) -> Optional[Tuple[str, List[str], int, Optional[str]]]:
            q = ""
            opts = {"a": "", "b": "", "c": "", "d": ""}
            ans_letter = ""
            exp = ""
            for line in b.splitlines():
                s = line.strip()
                if not s:
                    continue
                low = s.lower()
                if low.startswith("q:"):
                    q = s[2:].strip()
                elif low.startswith("a)"):
                    opts["a"] = s[2:].strip()
                elif low.startswith("b)"):
                    opts["b"] = s[2:].strip()
                elif low.startswith("c)"):
                    opts["c"] = s[2:].strip()
                elif low.startswith("d)"):
                    opts["d"] = s[2:].strip()
                elif low.startswith("ans:"):
                    ans_letter = s[4:].strip().lower()
                elif low.startswith("exp:"):
                    exp = s[4:].strip()
            if not q:
                return None
            opt_list = [opts["a"], opts["b"], opts["c"], opts["d"]]
            if any(not x for x in opt_list):
                return None
            letter_map = {"a": 0, "b": 1, "c": 2, "d": 3}
            if ans_letter not in letter_map:
                return None
            return (q, opt_list, letter_map[ans_letter], exp or "")

        for b in blocks:
            parsed = parse_block(b)
            if parsed:
                items.append(parsed)

        if not items:
            await msg.reply_text("Quiz format မမှန်ပါ။ /edquiz command မှာ ပြထားတဲ့ format နဲ့ပြန်ပို့ပါ။")
            return True

        added = db.add_quiz_bulk(items)
        PENDING.pop(user.id, None)
        await msg.reply_text(f"✅ Quiz ထည့်ပြီးပါပြီ။ (added={added})")
        return True

    if action == "broadcast":
        # Copy current message to all chats (except private users not started? we track all chats)
        chat_ids = db.chat_ids()
        sent = 0
        failed = 0
        for cid in chat_ids:
            try:
                # avoid copying to the same admin private? it's okay; user requested groups, but we track all chats
                await context.bot.copy_message(
                    chat_id=cid,
                    from_chat_id=chat.id,
                    message_id=msg.message_id,
                )
                sent += 1
            except Exception:
                failed += 1
        PENDING.pop(user.id, None)
        await msg.reply_text(f"✅ Broadcast done. sent={sent}, failed={failed}")
        return True

    if action == "restore":
        doc = msg.document
        if not doc:
            await msg.reply_text("Restore အတွက် <code>.db</code> document file ပို့ပေးရပါမယ်။")
            return True
        filename = (doc.file_name or "").lower()
        if not filename.endswith(".db"):
            await msg.reply_text(".db file ပဲလက်ခံပါတယ်။")
            return True
        try:
            f = await doc.get_file()
            tmp = os.path.join(os.getcwd(), "restore_tmp.db")
            await f.download_to_drive(custom_path=tmp)

            # swap db file
            db.close()
            shutil.copyfile(tmp, DB_FILE)

            # reopen
            global db
            db = DB(DB_FILE)

            PENDING.pop(user.id, None)
            await msg.reply_text("✅ Restore အောင်မြင်ပါတယ်။ Bot data ပြန်လည်အသုံးပြုနိုင်ပါပြီ။")
        except Exception as e:
            logger.exception("restore failed: %s", e)
            await msg.reply_text("❌ Restore မအောင်မြင်ပါ။ file ကိုစစ်ပြီး ပြန်ကြိုးစားပါ။")
        finally:
            try:
                os.remove(os.path.join(os.getcwd(), "restore_tmp.db"))
            except Exception:
                pass
        return True

    if action == "allclear_confirm":
        t = (msg.text or "").strip()
        if t != "CONFIRM":
            PENDING.pop(user.id, None)
            await msg.reply_text("Cancelled ✅ (CONFIRM မရေးသဖြင့် ဖျက်ခြင်းမလုပ်ပါ)")
            return True

        # wipe tables except admins bootstrap + about default
        try:
            for table in [
                "contacts", "verses", "events", "birthdays", "prayers", "quiz",
                "quiz_answers", "scores", "quiz_settings", "reports",
                "votes", "vote_votes", "users", "chats", "settings"
            ]:
                db._exec(f"DELETE FROM {table}")
            # keep admins as-is (or you can wipe too)
            db._exec("UPDATE about SET text=? WHERE id=1", (
                "အသင်းတော်/လူငယ်အဖွဲ့ အကြောင်းကို Admin က /edabout နဲ့ ထည့်သွင်းနိုင်ပါတယ်။",
            ))
            PENDING.pop(user.id, None)
            await msg.reply_text("✅ All data cleared.")
        except Exception as e:
            logger.exception("allclear failed: %s", e)
            await msg.reply_text("❌ Allclear failed.")
        return True

    if action == "edvote":
        raw = msg.text or ""
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        topic = ""
        options: List[str] = []
        for ln in lines:
            low = ln.lower()
            if low.startswith("topic:"):
                topic = ln.split(":", 1)[1].strip()
                continue
            if ln[:2].isdigit() and ")" in ln:
                # 1) Name
                try:
                    right = ln.split(")", 1)[1].strip()
                    if right:
                        options.append(right)
                except Exception:
                    pass

        if not topic or not (3 <= len(options) <= 5):
            await msg.reply_text("Vote format မမှန်ပါ။ Topic + Options (3-5) ထည့်ပေးပါ။")
            return True

        vote_id = db.set_vote(pending.chat_id, topic, options)
        PENDING.pop(user.id, None)
        await msg.reply_text(f"✅ Vote သတ်မှတ်ပြီးပါပြီ။ (vote_id={vote_id})\nUser များ /vote နဲ့ မဲပေးနိုင်ပါပြီ။")
        return True

    return False

# ---------------------------
# Callback Queries
# ---------------------------
async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    data = query.data

    if data == "noop":
        return

    # quiz:quiz_id:choice
    if data.startswith("quiz:"):
        try:
            _, qid_s, choice_s = data.split(":")
            quiz_id = int(qid_s)
            choice = int(choice_s)
        except Exception:
            return

        chat = update.effective_chat
        user = update.effective_user
        if not chat or not user:
            return

        if db.has_answered_quiz(quiz_id, chat.id, user.id):
            await query.answer("Already answered ✅", show_alert=True)
            return

        row = db.conn.execute(
            "SELECT question, options_json, answer_index, explanation FROM quiz WHERE id=?",
            (quiz_id,)
        ).fetchone()
        if not row:
            await query.answer("Quiz not found", show_alert=True)
            return

        ans = int(row["answer_index"])
        options = json.loads(row["options_json"])
        correct = 1 if choice == ans else 0

        db.record_quiz_answer(quiz_id, chat.id, user.id, choice, correct)

        full_name = safe_full_name(user.first_name, user.last_name)
        if correct:
            db.add_points(chat.id, user.id, user.username, full_name, delta=1)
        else:
            # wrong: 0 point, or -1 if you want; keep 0 by default
            pass

        exp = (row["explanation"] or "").strip()
        chosen_text = options[choice] if 0 <= choice < 4 else "?"
        correct_text = options[ans] if 0 <= ans < 4 else "?"

        result_msg = (
            "<b>🧠 Quiz Result</b>\n\n"
            f"<b>User:</b> {mention_html(user.id, full_name)}\n"
            f"<b>Your Answer:</b> {html.escape(chosen_text)}\n"
            f"<b>Correct:</b> {html.escape(correct_text)}\n"
        )
        result_msg += "\n<b>✅ Correct!</b>" if correct else "\n<b>❌ Wrong!</b>"
        if exp:
            result_msg += "\n\n<b>Explanation:</b>\n" + html.escape(exp)

        try:
            await query.message.reply_text(result_msg)
        except Exception:
            pass
        return

    # vote:vote_id:option
    if data.startswith("vote:"):
        try:
            _, vid_s, opt_s = data.split(":")
            vote_id = int(vid_s)
            opt = int(opt_s)
        except Exception:
            return
        chat = update.effective_chat
        user = update.effective_user
        if not chat or not user:
            return

        row = db.conn.execute(
            "SELECT topic, options_json FROM votes WHERE id=? AND chat_id=? AND is_active=1",
            (vote_id, chat.id)
        ).fetchone()
        if not row:
            await query.answer("Vote not active", show_alert=True)
            return

        options = json.loads(row["options_json"])
        if opt < 0 or opt >= len(options):
            return

        db.cast_vote(vote_id, chat.id, user.id, opt)
        await query.answer("Voted ✅", show_alert=True)
        return

    # vote results
    if data.startswith("vote_res:"):
        try:
            _, vid_s = data.split(":")
            vote_id = int(vid_s)
        except Exception:
            return
        chat = update.effective_chat
        if not chat:
            return

        row = db.conn.execute("SELECT topic, options_json FROM votes WHERE id=? AND chat_id=?", (vote_id, chat.id)).fetchone()
        if not row:
            await query.answer("Vote not found", show_alert=True)
            return

        topic = str(row["topic"])
        options = json.loads(row["options_json"])
        res = db.vote_results(vote_id, chat.id)
        counts = {int(r["option_index"]): int(r["c"]) for r in res}
        total = sum(counts.values())

        lines = [f"<b>📊 Vote Results</b>\n<b>Topic:</b> {html.escape(topic)}\n"]
        for i, opt in enumerate(options):
            c = counts.get(i, 0)
            pct = (c / total * 100.0) if total > 0 else 0.0
            lines.append(f"• {html.escape(opt)} — <b>{c}</b> ({pct:.1f}%)")
        lines.append(f"\n<b>Total Votes:</b> <b>{total}</b>")

        await query.message.reply_text("\n".join(lines))
        return

# ---------------------------
# Auto Quiz trigger on messages
# ---------------------------
async def on_any_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await track_update(update, context)

    # 1) handle pending admin input first
    if await handle_pending(update, context):
        return

    # 2) auto quiz logic only in groups
    if not is_group_chat(update):
        return

    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return
    if user.is_bot:
        return

    threshold, msg_count = db.inc_msg_count(chat.id)
    if threshold > 0 and msg_count >= threshold:
        db.reset_msg_count(chat.id)
        try:
            await send_quiz_to_chat(chat.id, context)
        except Exception as e:
            logger.exception("auto quiz send failed: %s", e)

# ---------------------------
# Error handler
# ---------------------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)

# ---------------------------
# Main
# ---------------------------
def main() -> None:
    defaults = Defaults(parse_mode=ParseMode.HTML)

    app = Application.builder().token(BOT_TOKEN).defaults(defaults).build()

    # user
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("helps", cmd_helps))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("contact", cmd_contact))
    app.add_handler(CommandHandler("verse", cmd_verse))
    app.add_handler(CommandHandler("events", cmd_events))
    app.add_handler(CommandHandler("birthday", cmd_birthday))
    app.add_handler(CommandHandler("pray", cmd_pray))
    app.add_handler(CommandHandler("praylist", cmd_praylist))
    app.add_handler(CommandHandler("quiz", cmd_quiz))
    app.add_handler(CommandHandler("tops", cmd_tops))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("vote", cmd_vote))

    # admin
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(CommandHandler("edabout", cmd_edabout))
    app.add_handler(CommandHandler("edcontact", cmd_edcontact))
    app.add_handler(CommandHandler("edverse", cmd_edverse))
    app.add_handler(CommandHandler("edevents", cmd_edevents))
    app.add_handler(CommandHandler("edbirthday", cmd_edbirthday))
    app.add_handler(CommandHandler("set", cmd_set))
    app.add_handler(CommandHandler("edquiz", cmd_edquiz))
    app.add_handler(CommandHandler("edpoint", cmd_edpoint))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("restore", cmd_restore))
    app.add_handler(CommandHandler("allclear", cmd_allclear))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("edadmin", cmd_edadmin))
    app.add_handler(CommandHandler("edvote", cmd_edvote))

    # callbacks
    app.add_handler(CallbackQueryHandler(cb_handler))

    # any text handler (pending + auto quiz)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_any_text))
    # also allow pending actions to accept captions for photo? (broadcast commonly)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, on_any_text))

    app.add_error_handler(on_error)

    logger.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
