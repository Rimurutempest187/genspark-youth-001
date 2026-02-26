#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════════
#   ✝️  CHURCH COMMUNITY TELEGRAM BOT
#       Full-Featured Bot with User & Admin Commands
#       python-telegram-bot v20+ | SQLite | Async
# ═══════════════════════════════════════════════════════════════════════════════

import os, json, asyncio, logging, sqlite3, io, random
from datetime import datetime
from functools import wraps
from dotenv import load_dotenv

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode

load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
#  CONFIG  (from .env)
# ──────────────────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))
DB_PATH        = os.getenv("DB_PATH", "church_bot.db")
CHURCH_NAME    = os.getenv("CHURCH_NAME", "Church Community")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in .env!")

# ──────────────────────────────────────────────────────────────────────────────
#  CONVERSATION STATES
# ──────────────────────────────────────────────────────────────────────────────
(
    ST_EDABOUT,
    ST_EDCONTACT,
    ST_EDVERSE,
    ST_EDEVENTS,
    ST_EDBIRTHDAY,
    ST_EDQUIZ,
    ST_EDVOTE,
    ST_BROADCAST,
) = range(8)

# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE  LAYER
# ══════════════════════════════════════════════════════════════════════════════

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    with _conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER UNIQUE NOT NULL,
            username   TEXT,
            first_name TEXT,
            last_name  TEXT,
            joined_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS admins (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER UNIQUE NOT NULL,
            username  TEXT,
            added_by  INTEGER,
            added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS bot_groups (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id   INTEGER UNIQUE NOT NULL,
            title     TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS group_members (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            username   TEXT,
            first_name TEXT,
            UNIQUE(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS about (
            id      INTEGER PRIMARY KEY DEFAULT 1,
            content TEXT NOT NULL DEFAULT '✝️ Church Community Bot မှ ကြိုဆိုပါသည်။'
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name  TEXT NOT NULL,
            phone TEXT NOT NULL,
            role  TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS verses (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            reference  TEXT DEFAULT '',
            verse_text TEXT NOT NULL,
            verse_type TEXT DEFAULT 'morning',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT DEFAULT '',
            event_date  TEXT DEFAULT '',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS birthdays (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            birth_day   INTEGER NOT NULL,
            birth_month INTEGER NOT NULL,
            birth_year  INTEGER
        );
        CREATE TABLE IF NOT EXISTS pray_requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            username   TEXT,
            first_name TEXT,
            text       TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            question       TEXT NOT NULL,
            option_a       TEXT NOT NULL,
            option_b       TEXT NOT NULL,
            option_c       TEXT DEFAULT '',
            option_d       TEXT DEFAULT '',
            correct_answer TEXT NOT NULL,
            explanation    TEXT DEFAULT '',
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS quiz_scores (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER UNIQUE NOT NULL,
            username   TEXT,
            first_name TEXT,
            score      INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS quiz_settings (
            id            INTEGER PRIMARY KEY DEFAULT 1,
            trigger_count INTEGER DEFAULT 10,
            message_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS votes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL,
            options    TEXT NOT NULL,
            is_active  INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS vote_responses (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            vote_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            username   TEXT,
            choice     INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(vote_id, user_id)
        );
        """)
        # ── Seed default rows ──
        db.execute(
            "INSERT OR IGNORE INTO about (id, content) VALUES (1, ?)",
            (
                "✝️ Church Community Bot မှ ကြိုဆိုပါသည်။\n\n"
                "ဤ Bot သည် ညီအကိုမောင်နှမများကို ကျမ်းဖတ်ချက်၊ "
                "ဆုတောင်းချက်များ၊ Community သတင်းများ ချိတ်ဆက်ပေးပါသည်။",
            ),
        )
        db.execute(
            "INSERT OR IGNORE INTO quiz_settings (id, trigger_count, message_count) VALUES (1, 10, 0)"
        )
    logger.info("✅ Database initialised — %s", DB_PATH)


# ── DB Helpers ──────────────────────────────────────────────────────────────

def db_is_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return True
    with _conn() as db:
        row = db.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
    return row is not None


def db_register_user(user) -> None:
    with _conn() as db:
        db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?,?,?,?)",
            (user.id, user.username, user.first_name, user.last_name),
        )


def db_register_group(chat) -> None:
    if chat.type in ("group", "supergroup"):
        with _conn() as db:
            db.execute(
                "INSERT OR IGNORE INTO bot_groups (chat_id, title) VALUES (?,?)",
                (chat.id, chat.title),
            )


def db_register_member(chat_id: int, user) -> None:
    with _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO group_members (chat_id, user_id, username, first_name) VALUES (?,?,?,?)",
            (chat_id, user.id, user.username, user.first_name),
        )


# ── Quiz helpers ─────────────────────────────────────────────────────────────

def db_increment_msg() -> sqlite3.Row:
    with _conn() as db:
        db.execute("UPDATE quiz_settings SET message_count=message_count+1 WHERE id=1")
        return db.execute("SELECT trigger_count, message_count FROM quiz_settings WHERE id=1").fetchone()


def db_reset_msg() -> None:
    with _conn() as db:
        db.execute("UPDATE quiz_settings SET message_count=0 WHERE id=1")


def db_quiz_settings() -> sqlite3.Row:
    with _conn() as db:
        return db.execute("SELECT trigger_count, message_count FROM quiz_settings WHERE id=1").fetchone()


def db_set_trigger(n: int) -> None:
    with _conn() as db:
        db.execute("UPDATE quiz_settings SET trigger_count=? WHERE id=1", (n,))


def db_random_quiz() -> sqlite3.Row:
    with _conn() as db:
        return db.execute("SELECT * FROM quiz_questions ORDER BY RANDOM() LIMIT 1").fetchone()


def db_upsert_score(user_id, username, first_name, delta: int = 1) -> None:
    with _conn() as db:
        db.execute(
            """INSERT INTO quiz_scores (user_id, username, first_name, score)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 score      = score + excluded.score,
                 username   = excluded.username,
                 first_name = excluded.first_name,
                 updated_at = CURRENT_TIMESTAMP""",
            (user_id, username, first_name, delta),
        )


def db_set_score(user_id: int, score: int) -> bool:
    with _conn() as db:
        cur = db.execute(
            "UPDATE quiz_scores SET score=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (score, user_id),
        )
    return cur.rowcount > 0


def db_top_scores(limit: int = 10):
    with _conn() as db:
        return db.execute(
            "SELECT user_id, username, first_name, score FROM quiz_scores ORDER BY score DESC LIMIT ?",
            (limit,),
        ).fetchall()


# ── Vote helpers ─────────────────────────────────────────────────────────────

def db_active_vote() -> sqlite3.Row:
    with _conn() as db:
        return db.execute("SELECT * FROM votes WHERE is_active=1 ORDER BY id DESC LIMIT 1").fetchone()


def db_vote_results(vote_id: int):
    with _conn() as db:
        return db.execute(
            "SELECT choice, COUNT(*) AS cnt FROM vote_responses WHERE vote_id=? GROUP BY choice",
            (vote_id,),
        ).fetchall()


def db_cast_vote(vote_id: int, user_id: int, username: str, choice: int) -> bool:
    try:
        with _conn() as db:
            db.execute(
                "INSERT INTO vote_responses (vote_id, user_id, username, choice) VALUES (?,?,?,?)",
                (vote_id, user_id, username, choice),
            )
        return True
    except sqlite3.IntegrityError:
        return False


# ── Stats helper ─────────────────────────────────────────────────────────────

def db_stats() -> dict:
    with _conn() as db:
        s = {}
        for tbl, key in [
            ("users", "users"), ("bot_groups", "groups"),
            ("pray_requests", "prayers"), ("quiz_questions", "quizzes"),
            ("verses", "verses"), ("events", "events"),
            ("birthdays", "birthdays"), ("contacts", "contacts"),
        ]:
            s[key] = db.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()["c"]
        qs = db.execute("SELECT trigger_count, message_count FROM quiz_settings WHERE id=1").fetchone()
        s["trigger"] = qs["trigger_count"]
        s["msg_count"] = qs["message_count"]
    return s


# ── Delete / clear helpers ───────────────────────────────────────────────────

_TYPE_TABLE = {
    "verse": "verses", "quiz": "quiz_questions", "event": "events",
    "birthday": "birthdays", "contact": "contacts", "pray": "pray_requests",
}


def db_delete(item_type: str, amount: int):
    tbl = _TYPE_TABLE.get(item_type.lower())
    if not tbl:
        return False, f"Unknown type `{item_type}`. Use: " + ", ".join(_TYPE_TABLE)
    with _conn() as db:
        rows = db.execute(f"SELECT id FROM {tbl} ORDER BY id DESC LIMIT ?", (amount,)).fetchall()
        if not rows:
            return True, 0
        ids = [r["id"] for r in rows]
        db.execute(f"DELETE FROM {tbl} WHERE id IN ({','.join(['?']*len(ids))})", ids)
    return True, len(ids)


def db_allclear() -> None:
    with _conn() as db:
        for tbl in _TYPE_TABLE.values():
            db.execute(f"DELETE FROM {tbl}")
        db.execute("DELETE FROM votes")
        db.execute("DELETE FROM vote_responses")
        db.execute(
            "UPDATE about SET content=? WHERE id=1",
            ("✝️ Church Community Bot မှ ကြိုဆိုပါသည်။",),
        )
        db.execute("UPDATE quiz_settings SET trigger_count=10, message_count=0 WHERE id=1")


# ══════════════════════════════════════════════════════════════════════════════
#  DECORATORS
# ══════════════════════════════════════════════════════════════════════════════

def admin_required(func):
    """For plain CommandHandlers (not inside ConversationHandler)."""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not db_is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Admin သာ ဤ command ကို အသုံးပြုနိုင်သည်။")
            return
        return await func(update, ctx)
    return wrapper


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _track(update: Update) -> None:
    """Register user + group + member silently."""
    u = update.effective_user
    c = update.effective_chat
    if u and not u.is_bot:
        db_register_user(u)
        if c and c.type in ("group", "supergroup"):
            db_register_group(c)
            db_register_member(c.id, u)


async def _send_quiz(context: ContextTypes.DEFAULT_TYPE, chat_id: int, update: Update = None):
    """Build & send a quiz question with inline A/B/C/D buttons."""
    q = db_random_quiz()
    if not q:
        if update:
            await update.message.reply_text("🧠 Quiz မေးခွန်းများ မရှိသေးပါ။ Admin မှ /edquiz ဖြင့် ထည့်ပါ။")
        return

    opts = []
    for letter in ("A", "B", "C", "D"):
        val = q[f"option_{letter.lower()}"]
        if val and val.strip():
            opts.append((letter, val.strip()))

    correct = q["correct_answer"].strip().upper()
    qid = q["id"]

    kb = []
    row = []
    for i, (ltr, txt) in enumerate(opts):
        row.append(
            InlineKeyboardButton(
                f"{ltr}. {txt[:28]}",
                callback_data=f"quiz|{qid}|{ltr}|{correct}",
            )
        )
        if len(row) == 2 or i == len(opts) - 1:
            kb.append(row)
            row = []

    body = "\n".join(f"  *{l}.* {t}" for l, t in opts)
    text = f"🧠 *Quiz မေးခွန်း* #{qid}\n\n❓ {q['question']}\n\n{body}"

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN,
    )


def _vote_text_and_kb(vote_row: sqlite3.Row):
    """Return (text, InlineKeyboardMarkup) for a vote."""
    options = json.loads(vote_row["options"])
    results = db_vote_results(vote_row["id"])
    rdict = {r["choice"]: r["cnt"] for r in results}
    total = sum(rdict.values())

    lines = [f"🗳️ *{vote_row['title']}*\n"]
    kb = []
    for i, opt in enumerate(options):
        cnt = rdict.get(i, 0)
        pct = (cnt / total * 100) if total else 0
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"{i+1}. *{opt}*\n   {bar} {cnt} votes ({pct:.1f}%)")
        kb.append([
            InlineKeyboardButton(
                f"{i+1}. {opt} ({cnt})",
                callback_data=f"vote|{vote_row['id']}|{i}",
            )
        ])

    lines.append(f"\n📊 Total: {total} votes")
    kb.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"voteref|{vote_row['id']}")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)


# ══════════════════════════════════════════════════════════════════════════════
#  ░░  USER COMMAND HANDLERS  ░░
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    u = update.effective_user
    text = (
        f"🕊️ *{CHURCH_NAME} Bot မှ ကြိုဆိုပါသည်!*\n\n"
        f"ညီအကိုမောင်နှမ *{u.first_name}* ✨\n\n"
        "📋 Commands များကြည့်ရန် → /helps\n\n"
        "_ဘုရားသခင် ကောင်းချီးပေးပါစေ 🙏_"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_helps(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    text = (
        f"📋 *{CHURCH_NAME} — User Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🕊️ /start — Bot ကို စတင်ရန်\n"
        "ℹ️ /about — အသင်းတော် သမိုင်း/ရည်ရွယ်ချက်\n"
        "📞 /contact — တာဝန်ခံများ ဖုန်းနံပါတ်\n"
        "📖 /verse — ယနေ့ ကျမ်းချက် (Morning/Night)\n"
        "📅 /events — လာမည့် အစီအစဉ်များ\n"
        "🎂 /birthday — ဤလ မွေးနေ့ရှင်များ\n"
        "🙏 /pray `<text>` — ဆုတောင်းချက် ပေးပို့ရန်\n"
        "📜 /praylist — ဆုတောင်းချက် စာရင်း\n"
        "🧠 /quiz — Quiz ဖြေဆိုရန်\n"
        "🏆 /Tops — Quiz Top Ranking\n"
        "📢 /report `<text>` — Admin ထံ အကြောင်းကြားရန်\n"
        "📣 /all — Member အားလုံး Mention (Group)\n"
        "🗳️ /vote — မဲပေးရန် / ရလဒ်ကြည့်ရန်\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    with _conn() as db:
        row = db.execute("SELECT content FROM about WHERE id=1").fetchone()
    content = row["content"] if row else "About မရှိသေးပါ။"
    await update.message.reply_text(
        f"ℹ️ *{CHURCH_NAME} အကြောင်း*\n\n{content}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    with _conn() as db:
        rows = db.execute("SELECT name, phone, role FROM contacts ORDER BY id").fetchall()
    if not rows:
        await update.message.reply_text("📞 ဆက်သွယ်ရန် အချက်အလက် မရှိသေးပါ။")
        return
    lines = [f"📞 *တာဝန်ခံ လူငယ်ခေါင်းဆောင်များ*\n━━━━━━━━━━━━━━━━━"]
    for r in rows:
        role = f" ᛫ _{r['role']}_" if r["role"] else ""
        lines.append(f"👤 *{r['name']}*{role}\n📱 `{r['phone']}`")
    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_verse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    hour = datetime.now().hour
    vtype = "morning" if 5 <= hour < 17 else "night"
    emoji, label = ("🌅", "Morning Verse") if vtype == "morning" else ("🌙", "Night Verse")

    with _conn() as db:
        row = db.execute(
            "SELECT * FROM verses WHERE verse_type=? ORDER BY RANDOM() LIMIT 1", (vtype,)
        ).fetchone()
        if not row:
            row = db.execute("SELECT * FROM verses ORDER BY RANDOM() LIMIT 1").fetchone()

    if not row:
        await update.message.reply_text("📖 Verse မရှိသေးပါ။ Admin မှ /edverse ဖြင့် ထည့်ပါ။")
        return

    text = (
        f"{emoji} *{label}*\n"
        f"📅 {datetime.now().strftime('%d %B %Y')}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"_{row['verse_text']}_\n\n"
        f"✝️ *{row['reference']}*"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_events(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    with _conn() as db:
        rows = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        await update.message.reply_text("📅 အစီအစဉ် မရှိသေးပါ။")
        return
    lines = [f"📅 *{CHURCH_NAME} — လာမည့် အစီအစဉ်များ*\n━━━━━━━━━━━━━━━━━"]
    for e in rows:
        date_s = f"\n📆 {e['event_date']}" if e["event_date"] else ""
        desc_s = f"\n📝 {e['description']}" if e["description"] else ""
        lines.append(f"🔔 *{e['title']}*{date_s}{desc_s}")
    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_birthday(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    m = datetime.now().month
    d = datetime.now().day
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM birthdays WHERE birth_month=? ORDER BY birth_day", (m,)
        ).fetchall()
    month_str = datetime.now().strftime("%B")
    if not rows:
        await update.message.reply_text(f"🎂 {month_str} လတွင် မွေးနေ့ရှင် မရှိသေးပါ။")
        return
    lines = [f"🎂 *{month_str} လ မွေးနေ့ရှင်များ*\n━━━━━━━━━━━━━━━━━"]
    for b in rows:
        yr = f" ({b['birth_year']})" if b["birth_year"] else ""
        today = " 🎉 *Happy Birthday!*" if b["birth_day"] == d else ""
        lines.append(f"🎈 *{b['name']}*{yr}  —  {b['birth_day']:02d}/{b['birth_month']:02d}{today}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_pray(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    u = update.effective_user
    text = " ".join(ctx.args).strip() if ctx.args else ""
    if not text:
        await update.message.reply_text(
            "🙏 ဆုတောင်းချက် ပေးပို့ရန်:\n`/pray <ဆုတောင်းချက်>`\n\nဥပမာ: `/pray ကျန်းမာရေး ကောင်းပါစေ`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    with _conn() as db:
        db.execute(
            "INSERT INTO pray_requests (user_id, username, first_name, text) VALUES (?,?,?,?)",
            (u.id, u.username, u.first_name, text),
        )
    await update.message.reply_text(
        "🙏 *ဆုတောင်းချက် ရောက်ပြီ!*\n\nညီအကိုမောင်နှမများ ဆုတောင်းပေးကြမည်ဖြစ်ပါသည်။\nဘုရားသခင် ကောင်းချီးပေးပါစေ 🙌",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_praylist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM pray_requests ORDER BY created_at DESC LIMIT 25"
        ).fetchall()
    if not rows:
        await update.message.reply_text("📜 ဆုတောင်းချက် မရှိသေးပါ။")
        return
    lines = ["🙏 *ဆုတောင်းချက် စာရင်း*\n━━━━━━━━━━━━━━━━━"]
    for i, r in enumerate(rows, 1):
        name = r["first_name"] or r["username"] or "Anonymous"
        uname = f" (@{r['username']})" if r["username"] else ""
        lines.append(f"*{i}. {name}*{uname}\n💬 {r['text']}")
    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_quiz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    await _send_quiz(ctx, update.effective_chat.id, update=update)


async def cmd_tops(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    rows = db_top_scores(10)
    if not rows:
        await update.message.reply_text("🏆 Quiz Ranking မရှိသေးပါ။")
        return
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = ["🏆 *Quiz Top Ranking*\n━━━━━━━━━━━━━━━━━"]
    for i, s in enumerate(rows):
        name = s["first_name"] or s["username"] or f"User#{s['user_id']}"
        un = f" (@{s['username']})" if s["username"] else ""
        lines.append(f"{medals[i]} #{i+1}  *{name}*{un}  →  `{s['score']}` pts")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    u = update.effective_user
    text = " ".join(ctx.args).strip() if ctx.args else ""
    if not text:
        await update.message.reply_text(
            "📢 Admin ထံ အကြောင်းကြားရန်:\n`/report <message>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    report = (
        f"📢 *Report*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 {u.first_name}{f' (@{u.username})' if u.username else ''}\n"
        f"🆔 `{u.id}`\n"
        f"💬 {text}\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    try:
        await ctx.bot.send_message(SUPER_ADMIN_ID, report, parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text("✅ Report ပေးပို့ပြီးပါပြီ!")
    except Exception as e:
        logger.warning("Report forward failed: %s", e)
        await update.message.reply_text("❌ Report ပေးပို့ရာတွင် အမှားဖြစ်ပါသည်။")


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("❌ Group Chat တွင်သာ အသုံးပြုနိုင်သည်။")
        return
    with _conn() as db:
        members = db.execute(
            "SELECT user_id, username, first_name FROM group_members WHERE chat_id=?", (chat.id,)
        ).fetchall()
    if not members:
        await update.message.reply_text("❌ Member စာရင်း မရှိသေးပါ။ Bot ကို group ထဲ add ပြီး members /start ဦးသုံးပါ။")
        return
    mentions = []
    for m in members:
        if m["username"]:
            mentions.append(f"@{m['username']}")
        else:
            fn = m["first_name"] or "Member"
            mentions.append(f"<a href='tg://user?id={m['user_id']}'>{fn}</a>")

    header = "📣 <b>Member အားလုံး</b>\n\n"
    chunk_size = 30
    for i in range(0, len(mentions), chunk_size):
        chunk = mentions[i : i + chunk_size]
        prefix = header if i == 0 else ""
        await update.message.reply_text(prefix + " ".join(chunk), parse_mode=ParseMode.HTML)


async def cmd_vote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _track(update)
    vote = db_active_vote()
    if not vote:
        await update.message.reply_text("🗳️ လောလောဆယ် မဲပေးရန် မရှိသေးပါ။")
        return
    text, kb = _vote_text_and_kb(vote)
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════════
#  ░░  ADMIN — SIMPLE COMMANDS  ░░
# ══════════════════════════════════════════════════════════════════════════════

@admin_required
async def cmd_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        f"⚙️ *Admin Commands — {CHURCH_NAME}*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 /edabout — About ပြင်ဆင်ရန်\n"
        "📞 /edcontact — Contact ထည့်ရန်\n"
        "📖 /edverse — Verse ထည့်ရန်\n"
        "📅 /edevents — Event ထည့်ရန်\n"
        "🎂 /edbirthday — Birthday ထည့်ရန်\n"
        "🧠 /edquiz — Quiz ထည့်ရန်\n"
        "🗳️ /edvote — Vote ပြင်ဆင်ရန်\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ /set `<n>` — Auto Quiz Message Count\n"
        "🏆 /edpoint `<id|@user> <score>` — Score ပြင်ရန်\n"
        "📢 /broadcast — Group များသို့ Broadcast\n"
        "📊 /stats — Statistics\n"
        "💾 /backup — Database Backup\n"
        "📥 /restore — Backup မှ Restore\n"
        "🗑️ /allclear — Data အားလုံး ဖျက်ရန်\n"
        "❌ /delete `<type> <n>` — Data ဖျက်ရန်\n"
        "👤 /eadmin `<id>` — Admin ထည့်/ဖယ်ရှားရန်\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@admin_required
async def cmd_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        s = db_quiz_settings()
        await update.message.reply_text(
            f"⚙️ *Auto Quiz Settings*\n\n"
            f"📨 Trigger every: `{s['trigger_count']}` messages\n"
            f"📊 Current count: `{s['message_count']}`\n\n"
            f"ပြောင်းရန်: `/set <number>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        n = int(ctx.args[0])
        if n < 1:
            raise ValueError
        db_set_trigger(n)
        await update.message.reply_text(
            f"✅ Auto Quiz: every *{n}* messages ဟု သတ်မှတ်ပြီးပါပြီ!",
            parse_mode=ParseMode.MARKDOWN,
        )
    except ValueError:
        await update.message.reply_text("❌ ကျေးဇူးပြု၍ ၁ နှင့်အထက် နံပါတ် ထည့်ပါ။")


@admin_required
async def cmd_edpoint(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "🏆 *Quiz Score ပြင်ဆင်ရန်*\n\n`/edpoint <user_id|@username> <score>`\n\nဥပမာ: `/edpoint 123456 50`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    target, score_str = ctx.args[0], ctx.args[1]
    try:
        score = int(score_str)
    except ValueError:
        await update.message.reply_text("❌ Score တွင် နံပါတ်သာ ထည့်ပါ။")
        return

    with _conn() as db:
        if target.startswith("@"):
            row = db.execute("SELECT user_id FROM quiz_scores WHERE username=?", (target[1:],)).fetchone()
        else:
            try:
                uid = int(target)
                row = db.execute("SELECT user_id FROM quiz_scores WHERE user_id=?", (uid,)).fetchone()
            except ValueError:
                await update.message.reply_text("❌ user_id (နံပါတ်) သို့မဟုတ် @username ထည့်ပါ။")
                return

    if not row:
        await update.message.reply_text("❌ User မတွေ့ပါ (Quiz မဖြေဆိုသေးသူ ဖြစ်နိုင်သည်)။")
        return
    ok = db_set_score(row["user_id"], score)
    if ok:
        await update.message.reply_text(f"✅ Score → *{score}* pts ဟု ပြင်ဆင်ပြီးပါပြီ!", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("❌ Update မအောင်မြင်ပါ။")


@admin_required
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = db_stats()
    text = (
        f"📊 *{CHURCH_NAME} — Statistics*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👥 Users: *{s['users']}*\n"
        f"🏘️ Groups: *{s['groups']}*\n"
        f"🙏 Pray Requests: *{s['prayers']}*\n"
        f"🧠 Quiz Questions: *{s['quizzes']}*\n"
        f"📖 Verses: *{s['verses']}*\n"
        f"📅 Events: *{s['events']}*\n"
        f"🎂 Birthdays: *{s['birthdays']}*\n"
        f"📞 Contacts: *{s['contacts']}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚙️ Auto Quiz: every *{s['trigger']}* msgs\n"
        f"📨 Current count: *{s['msg_count']}*"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@admin_required
async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💾 Backup ပြုလုပ်နေပါသည်...")
    try:
        # ── SQLite .db file ──
        with open(DB_PATH, "rb") as f:
            db_bytes = f.read()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        await update.message.reply_document(
            document=io.BytesIO(db_bytes),
            filename=f"church_backup_{ts}.db",
            caption=f"💾 SQLite Backup — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )
        # ── JSON export ──
        tables = [
            "users","admins","bot_groups","about","contacts","verses",
            "events","birthdays","pray_requests","quiz_questions",
            "quiz_scores","votes","vote_responses","quiz_settings",
        ]
        data: dict = {}
        with _conn() as db:
            for tbl in tables:
                rows = db.execute(f"SELECT * FROM {tbl}").fetchall()
                data[tbl] = [dict(r) for r in rows]
        json_bytes = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode()
        await update.message.reply_document(
            document=io.BytesIO(json_bytes),
            filename=f"church_backup_{ts}.json",
            caption="📋 JSON Export (human-readable)",
        )
    except Exception as e:
        logger.error("Backup error: %s", e)
        await update.message.reply_text(f"❌ Backup error: {e}")


@admin_required
async def cmd_restore(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not (update.message.reply_to_message and update.message.reply_to_message.document):
        await update.message.reply_text(
            "📥 *Restore လုပ်ရန်*\n\n"
            "① Backup file (`.db` / `.json`) ကို Chat ထဲ ပေးပို့ပါ\n"
            "② ထို message ကို reply ပြု၍ `/restore` ရေးပါ",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    doc = update.message.reply_to_message.document
    file = await ctx.bot.get_file(doc.file_id)
    raw = bytes(await file.download_as_bytearray())
    await update.message.reply_text("📥 Restore ပြုလုပ်နေပါသည်...")
    try:
        if doc.file_name.endswith(".db"):
            with open(DB_PATH, "wb") as f:
                f.write(raw)
            await update.message.reply_text("✅ SQLite DB restore ပြီးပါပြီ!")
        elif doc.file_name.endswith(".json"):
            payload: dict = json.loads(raw.decode())
            with _conn() as db:
                for tbl, rows in payload.items():
                    if not rows:
                        continue
                    try:
                        db.execute(f"DELETE FROM {tbl}")
                        cols = list(rows[0].keys())
                        ph = ",".join(["?"] * len(cols))
                        col_str = ",".join(cols)
                        for row in rows:
                            db.execute(
                                f"INSERT OR REPLACE INTO {tbl} ({col_str}) VALUES ({ph})",
                                [row[c] for c in cols],
                            )
                    except Exception as ex:
                        logger.warning("restore table %s: %s", tbl, ex)
            await update.message.reply_text("✅ JSON restore ပြီးပါပြီ!")
        else:
            await update.message.reply_text("❌ .db သို့မဟုတ် .json file ကိုသာ support ပြုသည်။")
    except Exception as e:
        logger.error("Restore error: %s", e)
        await update.message.reply_text(f"❌ Restore error: {e}")


@admin_required
async def cmd_allclear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, ဖျက်မည်", callback_data="ac|confirm"),
        InlineKeyboardButton("❌ မဖျက်ဘူး",    callback_data="ac|cancel"),
    ]])
    await update.message.reply_text(
        "⚠️ *WARNING!*\n\nData *အားလုံး* ဖျက်မည်\n(Users / Groups / Admins မဖျက်ပါ)\n\nသေချာပါသလား?",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_required
async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usage = (
        "🗑️ *Data ဖျက်ရန်*\n\n"
        "`/delete <type> <amount>`\n\n"
        "Types: `verse` `quiz` `event` `birthday` `contact` `pray`\n\n"
        "ဥပမာ: `/delete verse 3`"
    )
    if len(ctx.args) < 2:
        await update.message.reply_text(usage, parse_mode=ParseMode.MARKDOWN)
        return
    try:
        n = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Amount တွင် နံပါတ်သာ ထည့်ပါ။")
        return
    ok, result = db_delete(ctx.args[0], n)
    if ok:
        await update.message.reply_text(f"✅ `{ctx.args[0]}` {result} ခု ဖျက်ပြီးပါပြီ!", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ {result}", parse_mode=ParseMode.MARKDOWN)


@admin_required
async def cmd_eadmin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "👤 *Admin စီမံရန်*\n\n"
            "ထည့်ရန် / ဖယ်ရန် (toggle):\n`/eadmin <user_id>`\n\n"
            "ဥပမာ: `/eadmin 123456789`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        tid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID (နံပါတ်) ထည့်ပါ။")
        return
    if tid == SUPER_ADMIN_ID:
        await update.message.reply_text("❌ Super Admin ကို ပြောင်းလဲ၍ မရပါ။")
        return
    with _conn() as db:
        existing = db.execute("SELECT id FROM admins WHERE user_id=?", (tid,)).fetchone()
        if existing:
            db.execute("DELETE FROM admins WHERE user_id=?", (tid,))
            msg = f"✅ User `{tid}` ကို Admin မှ ဖယ်ရှားပြီးပါပြီ!"
        else:
            db.execute(
                "INSERT INTO admins (user_id, added_by) VALUES (?,?)",
                (tid, update.effective_user.id),
            )
            msg = f"✅ User `{tid}` ကို Admin အဖြစ် ထည့်ပြီးပါပြီ!"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════════════════
#  ░░  ADMIN — CONVERSATION HANDLERS  ░░
# ══════════════════════════════════════════════════════════════════════════════

# ── /edabout ─────────────────────────────────────────────────────────────────
async def conv_edabout_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not db_is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin သာ ဤ command ကို အသုံးပြုနိုင်သည်။")
        return ConversationHandler.END
    await update.message.reply_text(
        "✏️ *About ပြင်ဆင်ရန်*\n\nအဖွဲ့အစည်း သမိုင်းကြောင်း / ရည်ရွယ်ချက် ရေးပါ:\n\n_/cancel ပယ်ဖျက်ရန်_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_EDABOUT


async def conv_edabout_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with _conn() as db:
        db.execute("UPDATE about SET content=? WHERE id=1", (update.message.text,))
    await update.message.reply_text("✅ About ပြင်ဆင်ပြီးပါပြီ!")
    return ConversationHandler.END


# ── /edcontact ────────────────────────────────────────────────────────────────
async def conv_edcontact_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not db_is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin သာ ဤ command ကို အသုံးပြုနိုင်သည်။")
        return ConversationHandler.END
    await update.message.reply_text(
        "📞 *Contact ထည့်သွင်းရန်*\n\n"
        "Format (တစ်ကြောင်းစီ):\n`နာမည် | ဖုန်းနံပါတ် | တာဝန်`\n\n"
        "ဥပမာ:\n```\nဦးသန်းထွေး | 09-1234-5678 | Pastor\nမနှင်းသိဂ္ဂီ | 09-9876-5432 | Youth Leader\n```\n_/cancel ပယ်ဖျက်ရန်_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_EDCONTACT


async def conv_edcontact_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split("\n")
    ok, err = 0, []
    with _conn() as db:
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                db.execute(
                    "INSERT INTO contacts (name, phone, role) VALUES (?,?,?)",
                    (parts[0], parts[1], parts[2] if len(parts) > 2 else ""),
                )
                ok += 1
            else:
                err.append(line)
    msg = f"✅ Contact {ok} ခု ထည့်ပြီးပါပြီ!"
    if err:
        msg += "\n⚠️ Format မမှန်ကြောင်း ကျော်: " + "; ".join(err[:3])
    await update.message.reply_text(msg)
    return ConversationHandler.END


# ── /edverse ──────────────────────────────────────────────────────────────────
async def conv_edverse_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not db_is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin သာ ဤ command ကို အသုံးပြုနိုင်သည်။")
        return ConversationHandler.END
    await update.message.reply_text(
        "📖 *Verse ထည့်သွင်းရန်*\n\n"
        "Format: `ကျမ်းကိုး | ကျမ်းချက် | morning or night`\n\n"
        "ဥပမာ:\n```\nဆာလံ ၂၃:၁ | ထာဝရဘုရားသည် ငါ၏ ဆိတ်ထိန်းဖြစ်သည် | morning\nယောဟန် ၃:၁၆ | ဘုရားသခင်သည် လောကကို ချစ်တော်မူ... | night\n```\n_/cancel ပယ်ဖျက်ရန်_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_EDVERSE


async def conv_edverse_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split("\n")
    ok = 0
    with _conn() as db:
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                vtype = "morning"
                if len(parts) > 2 and parts[2].lower() in ("morning", "night"):
                    vtype = parts[2].lower()
                db.execute(
                    "INSERT INTO verses (reference, verse_text, verse_type) VALUES (?,?,?)",
                    (parts[0], parts[1], vtype),
                )
                ok += 1
    await update.message.reply_text(f"✅ Verse {ok} ခု ထည့်ပြီးပါပြီ!")
    return ConversationHandler.END


# ── /edevents ─────────────────────────────────────────────────────────────────
async def conv_edevents_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not db_is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin သာ ဤ command ကို အသုံးပြုနိုင်သည်။")
        return ConversationHandler.END
    await update.message.reply_text(
        "📅 *Event ထည့်သွင်းရန်*\n\n"
        "Format: `ခေါင်းစဉ် | ဖော်ပြချက် | ရက်စွဲ`\n\n"
        "ဥပမာ:\n```\nနှစ်ပတ်လည် ဝတ်ပြုပွဲ | အားလုံး ကြွပါ | 15/03/2025\nBible Study | ဗျာဒိတ်ကျမ်း | 20/03/2025\n```\n_/cancel ပယ်ဖျက်ရန်_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_EDEVENTS


async def conv_edevents_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split("\n")
    ok = 0
    with _conn() as db:
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if parts[0]:
                db.execute(
                    "INSERT INTO events (title, description, event_date) VALUES (?,?,?)",
                    (parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""),
                )
                ok += 1
    await update.message.reply_text(f"✅ Event {ok} ခု ထည့်ပြီးပါပြီ!")
    return ConversationHandler.END


# ── /edbirthday ───────────────────────────────────────────────────────────────
async def conv_edbirthday_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not db_is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin သာ ဤ command ကို အသုံးပြုနိုင်သည်။")
        return ConversationHandler.END
    await update.message.reply_text(
        "🎂 *မွေးနေ့ ထည့်သွင်းရန်*\n\n"
        "Format: `နာမည် | DD/MM/YYYY`\n\n"
        "ဥပမာ:\n```\nမောင်ကောင်းဖြူ | 15/03/2000\nမနွဲ့ဝါ | 22/07/1998\n```\n_/cancel ပယ်ဖျက်ရန်_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_EDBIRTHDAY


async def conv_edbirthday_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split("\n")
    ok, err = 0, []
    with _conn() as db:
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                try:
                    dp = parts[1].split("/")
                    dd, mm = int(dp[0]), int(dp[1])
                    yy = int(dp[2]) if len(dp) > 2 else None
                    db.execute(
                        "INSERT INTO birthdays (name, birth_day, birth_month, birth_year) VALUES (?,?,?,?)",
                        (parts[0], dd, mm, yy),
                    )
                    ok += 1
                except Exception:
                    err.append(parts[0])
            else:
                err.append(line[:20])
    msg = f"✅ မွေးနေ့ {ok} ခု ထည့်ပြီးပါပြီ!"
    if err:
        msg += "\n⚠️ Error ကျောင်: " + ", ".join(err[:5])
    await update.message.reply_text(msg)
    return ConversationHandler.END


# ── /edquiz ───────────────────────────────────────────────────────────────────
async def conv_edquiz_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not db_is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin သာ ဤ command ကို အသုံးပြုနိုင်သည်။")
        return ConversationHandler.END
    await update.message.reply_text(
        "🧠 *Quiz မေးခွန်း ထည့်သွင်းရန်*\n\n"
        "Format:\n`မေးခွန်း | A | B | C | D | မှန်သောအဖြေ | ရှင်းလင်းချက်`\n\n"
        "ဥပမာ:\n```\nကမ္ဘာကို ဖန်ဆင်းသည်မှာ ရက်ဘယ်နှစ်ရက်? | ၃ | ၆ | ၇ | ၁၀ | B | ကမ္ဘာ ၁:၃၁\n```\n"
        "_(C, D နှင့် ရှင်းလင်းချက် optional ဖြစ်ပါသည်)_\n_/cancel ပယ်ဖျက်ရန်_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_EDQUIZ


async def conv_edquiz_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split("\n")
    ok, err = 0, []
    with _conn() as db:
        for line in lines:
            p = [x.strip() for x in line.split("|")]
            if len(p) >= 6:
                db.execute(
                    """INSERT INTO quiz_questions
                       (question, option_a, option_b, option_c, option_d, correct_answer, explanation)
                       VALUES (?,?,?,?,?,?,?)""",
                    (p[0], p[1], p[2],
                     p[3] if len(p) > 3 else "",
                     p[4] if len(p) > 4 else "",
                     p[5].upper(),
                     p[6] if len(p) > 6 else ""),
                )
                ok += 1
            else:
                err.append(f"'{p[0][:20]}...' — pipe {len(p)} ခုသာ ရှိသည်")
    msg = f"✅ Quiz {ok} ခု ထည့်ပြီးပါပြီ!"
    if err:
        msg += "\n⚠️ Skip: " + "\n".join(err[:3])
    await update.message.reply_text(msg)
    return ConversationHandler.END


# ── /edvote ───────────────────────────────────────────────────────────────────
async def conv_edvote_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not db_is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin သာ ဤ command ကို အသုံးပြုနိုင်သည်။")
        return ConversationHandler.END
    await update.message.reply_text(
        "🗳️ *Vote ပြင်ဆင်ရန်*\n\n"
        "ပထမကြောင်းတွင် ခေါင်းစဉ်၊ နောက်ကြောင်းများတွင် ရွေးချယ်ခွင့်\n\n"
        "ဥပမာ:\n```\nဤ Sunday ဘယ်မှာ ကျင်းပမလဲ?\nဘုရားကျောင်း\nသင်တန်းကျောင်း\nPark\n```\n_/cancel ပယ်ဖျက်ရန်_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_EDVOTE


async def conv_edvote_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = [l.strip() for l in update.message.text.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        await update.message.reply_text("❌ ခေါင်းစဉ် + ရွေးချယ်ခွင့် ၁ ခုနှင့်အထက် ထည့်ပါ။")
        return ST_EDVOTE
    title, opts = lines[0], lines[1:]
    with _conn() as db:
        db.execute("UPDATE votes SET is_active=0")
        db.execute(
            "INSERT INTO votes (title, options, is_active) VALUES (?,?,1)",
            (title, json.dumps(opts, ensure_ascii=False)),
        )
    preview = "\n".join(f"{i+1}. {o}" for i, o in enumerate(opts))
    await update.message.reply_text(
        f"✅ Vote ထည့်ပြီးပါပြီ!\n\n📋 *{title}*\n{preview}",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


# ── /broadcast ────────────────────────────────────────────────────────────────
async def conv_broadcast_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not db_is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin သာ ဤ command ကို အသုံးပြုနိုင်သည်။")
        return ConversationHandler.END
    await update.message.reply_text(
        "📢 *Broadcast*\n\nGroups အားလုံးထံ ပေးပို့မည့် Message ကို ရေးပါ:\n_(ပုံ / text ပေးပို့နိုင်သည်)_\n_/cancel ပယ်ဖျက်ရန်_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ST_BROADCAST


async def conv_broadcast_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with _conn() as db:
        groups = db.execute("SELECT chat_id FROM bot_groups").fetchall()
    ok = fail = 0
    for g in groups:
        try:
            if update.message.photo:
                await ctx.bot.send_photo(
                    chat_id=g["chat_id"],
                    photo=update.message.photo[-1].file_id,
                    caption=(f"📢 *Broadcast*\n\n{update.message.caption or ''}"),
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await ctx.bot.send_message(
                    chat_id=g["chat_id"],
                    text=f"📢 *Broadcast*\n\n{update.message.text}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            ok += 1
        except Exception as e:
            logger.warning("Broadcast → %s failed: %s", g["chat_id"], e)
            fail += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(
        f"📢 *Broadcast ပြီးပါပြီ!*\n✅ {ok} groups\n❌ {fail} failed",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


# ── Universal cancel ──────────────────────────────────────────────────────────
async def conv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ ပယ်ဖျက်ပြီးပါပြီ။")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
#  ░░  CALLBACK QUERY HANDLERS  ░░
# ══════════════════════════════════════════════════════════════════════════════

async def cb_quiz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user
    # data: "quiz|{qid}|{selected}|{correct}"
    try:
        _, qid, selected, correct = q.data.split("|")
    except ValueError:
        await q.answer("Invalid data")
        return

    orig_text = q.message.text or ""
    with _conn() as db:
        row = db.execute("SELECT explanation FROM quiz_questions WHERE id=?", (qid,)).fetchone()
    expl = (row["explanation"] if row and row["explanation"] else "").strip()

    if selected.upper() == correct.upper():
        db_upsert_score(user.id, user.username, user.first_name, 1)
        await q.answer("✅ မှန်ပါသည်! +1 point 🎉", show_alert=True)
        suffix = f"\n\n✅ *{user.first_name}* မှန်ပါသည်! +1 pt"
    else:
        await q.answer(f"❌ မမှန်ပါ! မှန်သောအဖြေ: {correct}", show_alert=True)
        suffix = f"\n\n❌ *{user.first_name}* မမှန်ပါ\n✅ မှန်သောအဖြေ: *{correct}*"

    if expl:
        suffix += f"\n💡 {expl}"

    try:
        await q.edit_message_text(
            orig_text + suffix,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=None,
        )
    except Exception:
        pass


async def cb_vote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user
    data = q.data

    if data.startswith("voteref|"):
        vid = int(data.split("|")[1])
        with _conn() as db:
            vote = db.execute("SELECT * FROM votes WHERE id=?", (vid,)).fetchone()
        if not vote:
            await q.answer("Vote မတွေ့ပါ", show_alert=True)
            return
        text, kb = _vote_text_and_kb(vote)
        try:
            await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        await q.answer("🔄 Updated!")
        return

    # "vote|{vid}|{choice}"
    try:
        _, vid_s, choice_s = data.split("|")
        vid, choice = int(vid_s), int(choice_s)
    except ValueError:
        await q.answer("Invalid data")
        return

    ok = db_cast_vote(vid, user.id, user.username, choice)
    if ok:
        await q.answer("✅ မဲပေးပြီးပါပြီ!", show_alert=True)
        # Refresh the vote message
        with _conn() as db:
            vote = db.execute("SELECT * FROM votes WHERE id=?", (vid,)).fetchone()
        if vote:
            text, kb = _vote_text_and_kb(vote)
            try:
                await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
    else:
        await q.answer("⚠️ ကိုယ်တိုင် မဲပေးပြီးပါပြီ!", show_alert=True)


async def cb_allclear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not db_is_admin(q.from_user.id):
        await q.answer("Admin သာ ဤ action လုပ်နိုင်သည်", show_alert=True)
        return
    action = q.data.split("|")[1]
    if action == "confirm":
        db_allclear()
        await q.edit_message_text("✅ Data အားလုံး ဖျက်ပြီးပါပြီ!")
    else:
        await q.edit_message_text("❌ ဖျက်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")


# ══════════════════════════════════════════════════════════════════════════════
#  ░░  MESSAGE HANDLER (auto-quiz + member tracking)  ░░
# ══════════════════════════════════════════════════════════════════════════════

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    u = update.effective_user
    c = update.effective_chat
    if not u or u.is_bot:
        return

    _track(update)

    if c.type in ("group", "supergroup"):
        settings = db_increment_msg()
        if settings and settings["message_count"] >= settings["trigger_count"]:
            db_reset_msg()
            await _send_quiz(ctx, c.id)


# ══════════════════════════════════════════════════════════════════════════════
#  ░░  BOT SETUP & MAIN  ░░
# ══════════════════════════════════════════════════════════════════════════════

def _make_conv(cmd: str, start_fn, receive_fn, state_id: int) -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler(cmd, start_fn)],
        states={
            state_id: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_fn),
                MessageHandler(filters.PHOTO,                   receive_fn),
            ]
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
        allow_reentry=True,
    )


def main() -> None:
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # ── User Commands ─────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("helps",    cmd_helps))
    app.add_handler(CommandHandler("about",    cmd_about))
    app.add_handler(CommandHandler("contact",  cmd_contact))
    app.add_handler(CommandHandler("verse",    cmd_verse))
    app.add_handler(CommandHandler("events",   cmd_events))
    app.add_handler(CommandHandler("birthday", cmd_birthday))
    app.add_handler(CommandHandler("pray",     cmd_pray))
    app.add_handler(CommandHandler("praylist", cmd_praylist))
    app.add_handler(CommandHandler("quiz",     cmd_quiz))
    app.add_handler(CommandHandler("Tops",     cmd_tops))
    app.add_handler(CommandHandler("tops",     cmd_tops))
    app.add_handler(CommandHandler("report",   cmd_report))
    app.add_handler(CommandHandler("all",      cmd_all))
    app.add_handler(CommandHandler("vote",     cmd_vote))

    # ── Admin Simple Commands ─────────────────────────────────────────────────
    app.add_handler(CommandHandler("edit",     cmd_edit))
    app.add_handler(CommandHandler("set",      cmd_set))
    app.add_handler(CommandHandler("edpoint",  cmd_edpoint))
    app.add_handler(CommandHandler("stats",    cmd_stats))
    app.add_handler(CommandHandler("backup",   cmd_backup))
    app.add_handler(CommandHandler("restore",  cmd_restore))
    app.add_handler(CommandHandler("allclear", cmd_allclear))
    app.add_handler(CommandHandler("delete",   cmd_delete))
    app.add_handler(CommandHandler("eadmin",   cmd_eadmin))

    # ── Admin Conversation Handlers ───────────────────────────────────────────
    app.add_handler(_make_conv("edabout",   conv_edabout_start,   conv_edabout_receive,   ST_EDABOUT))
    app.add_handler(_make_conv("edcontact", conv_edcontact_start, conv_edcontact_receive, ST_EDCONTACT))
    app.add_handler(_make_conv("edverse",   conv_edverse_start,   conv_edverse_receive,   ST_EDVERSE))
    app.add_handler(_make_conv("edevents",  conv_edevents_start,  conv_edevents_receive,  ST_EDEVENTS))
    app.add_handler(_make_conv("edbirthday",conv_edbirthday_start,conv_edbirthday_receive,ST_EDBIRTHDAY))
    app.add_handler(_make_conv("edquiz",    conv_edquiz_start,    conv_edquiz_receive,    ST_EDQUIZ))
    app.add_handler(_make_conv("edvote",    conv_edvote_start,    conv_edvote_receive,    ST_EDVOTE))
    app.add_handler(_make_conv("broadcast", conv_broadcast_start, conv_broadcast_receive, ST_BROADCAST))

    # ── Callback Query Handlers ───────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_quiz,     pattern=r"^quiz\|"))
    app.add_handler(CallbackQueryHandler(cb_vote,     pattern=r"^vote"))
    app.add_handler(CallbackQueryHandler(cb_allclear, pattern=r"^ac\|"))

    # ── General Message Handler (auto-quiz + tracking) ────────────────────────
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Sticker.ALL)
            & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info("🕊️  %s Bot is running…", CHURCH_NAME)
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
