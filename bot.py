import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime
from functools import wraps
from typing import Optional
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

load_dotenv()

# ─────────────────────────── CONFIG ───────────────────────────
BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))
DB_PATH       = os.getenv("DB_PATH", "church_bot.db")

logging.basicConfig(
    format="%(asctime)s │ %(name)s │ %(levelname)s │ %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ──────────────── ConversationHandler States ──────────────────
(
    EDABOUT,
    EDCONTACT,
    EDVERSE,
    EDEVENTS,
    EDBIRTHDAY,
    EDQUIZ,
    BROADCAST,
    EDVOTE_TITLE,
    EDVOTE_OPTIONS,
    REPORT_TEXT,
    RESTORE_FILE,
) = range(11)


# ═══════════════════════════ DATABASE ═════════════════════════
def init_db() -> None:
    """Create all tables and seed default settings / super-admin."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id   INTEGER PRIMARY KEY,
            username  TEXT,
            added_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            last_name  TEXT,
            chat_id    INTEGER,
            joined_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS groups (
            chat_id   INTEGER PRIMARY KEY,
            title     TEXT,
            joined_at TEXT
        );
        CREATE TABLE IF NOT EXISTS group_members (
            chat_id    INTEGER,
            user_id    INTEGER,
            username   TEXT,
            first_name TEXT,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS about (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            content    TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT,
            phone      TEXT,
            role       TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS verses (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            content    TEXT,
            verse_type TEXT DEFAULT 'general',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT,
            description TEXT,
            event_date  TEXT,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS birthdays (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT,
            birth_month INTEGER,
            birth_day   INTEGER,
            birth_year  INTEGER,
            created_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS prayer_requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            username   TEXT,
            first_name TEXT,
            content    TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            question   TEXT,
            option_a   TEXT,
            option_b   TEXT,
            option_c   TEXT,
            option_d   TEXT,
            correct    TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS quiz_scores (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            score      INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS votes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT,
            options    TEXT,
            is_active  INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS vote_records (
            vote_id      INTEGER,
            user_id      INTEGER,
            option_index INTEGER,
            voted_at     TEXT,
            PRIMARY KEY (vote_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS message_counters (
            chat_id INTEGER PRIMARY KEY,
            count   INTEGER DEFAULT 0
        );
    """)

    c.execute("INSERT OR IGNORE INTO settings VALUES ('quiz_interval', '20')")
    c.execute("INSERT OR IGNORE INTO settings VALUES ('bot_name', 'Church Community Bot')")

    if SUPER_ADMIN_ID:
        c.execute(
            "INSERT OR IGNORE INTO admins VALUES (?, 'SuperAdmin', ?)",
            (SUPER_ADMIN_ID, _now()),
        )

    conn.commit()
    conn.close()
    logger.info("Database initialised at %s", DB_PATH)


def _db():
    return sqlite3.connect(DB_PATH)


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


# ═══════════════════════════ HELPERS ══════════════════════════
def is_admin(user_id: int) -> bool:
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
        ).fetchone()
    return row is not None


def get_setting(key: str) -> Optional[str]:
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(key: str, value: str) -> None:
    with _db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))
        conn.commit()


def track_user(user, chat_id=None) -> None:
    with _db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO users
               (user_id, username, first_name, last_name, chat_id, joined_at)
               VALUES (?,?,?,?,?,?)""",
            (
                user.id,
                user.username or "",
                user.first_name or "",
                user.last_name or "",
                chat_id,
                _now(),
            ),
        )
        conn.commit()


def track_group(chat) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO groups (chat_id, title, joined_at) VALUES (?,?,?)",
            (chat.id, chat.title or "", _now()),
        )
        conn.commit()


def track_member(chat_id: int, user) -> None:
    with _db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO group_members
               (chat_id, user_id, username, first_name)
               VALUES (?,?,?,?)""",
            (chat_id, user.id, user.username or "", user.first_name or ""),
        )
        conn.commit()


def _is_group(chat_type: str) -> bool:
    return chat_type in ("group", "supergroup")


MONTH_NAMES = {
    1: "ဇန်နဝါရီ", 2: "ဖေဖော်ဝါရီ", 3: "မတ်",    4: "ဧပြီ",
    5: "မေ",       6: "ဇွန်",      7: "ဇူလိုင်", 8: "သြဂုတ်",
    9: "စက်တင်ဘာ", 10: "အောက်တိုဘာ", 11: "နိုဝင်ဘာ", 12: "ဒီဇင်ဘာ",
}


# ══════════════════════════ ADMIN CHECK ═══════════════════════
def admin_only(func):
    """Decorator: rejects non-admins silently or with a message."""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *a, **kw):
        if not is_admin(update.effective_user.id):
            if update.message:
                await update.message.reply_text("⛔ Admin များသာ အသုံးပြုနိုင်သည်။")
            return ConversationHandler.END
        return await func(update, ctx, *a, **kw)
    return wrapper


# ═══════════════════════ USER COMMANDS ════════════════════════

# ─── /start ───
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    track_user(user, chat.id)
    if _is_group(chat.type):
        track_group(chat)
        track_member(chat.id, user)

    text = (
        f"🙏 မင်္ဂလာပါ *{user.first_name}*\\!\n\n"
        "✝️ *Church Community Bot* မှ ကြိုဆိုပါသည်\\.\n\n"
        "ဤ Bot သည် ကျွန်ုပ်တို့ အသင်းတော် လူငယ်အဖွဲ့၏\n"
        "ဆက်သွယ်ရေး Bot ဖြစ်သည်\\.\n\n"
        "📌 Commands → /helps\n"
        "ℹ️ အဖွဲ့အကြောင်း → /about\n\n"
        "🙌 ဘုရားသခင် ကောင်းချီးပေးပါစေ\\!"
    )
    kb = [
        [
            InlineKeyboardButton("📋 Commands", callback_data="cb_helps"),
            InlineKeyboardButton("ℹ️ About",    callback_data="cb_about"),
        ],
        [
            InlineKeyboardButton("📖 Daily Verse", callback_data="cb_verse"),
            InlineKeyboardButton("📅 Events",      callback_data="cb_events"),
        ],
    ]
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ─── /helps ───
async def cmd_helps(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 *User Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/start \\- Bot စတင် / နှုတ်ဆက်\n"
        "/helps \\- Commands စာရင်း\n"
        "/about \\- အဖွဲ့ သမိုင်းကြောင်း\n"
        "/contact \\- တာဝန်ခံ ဆက်သွယ်ရန်\n"
        "/verse \\- ယနေ့ ကျမ်းချက်\n"
        "/events \\- လာမည့် အစီအစဉ်\n"
        "/birthday \\- ယခုလ မွေးနေ့ရှင်\n"
        "/pray \\<text\\> \\- ဆုတောင်းချက် ပေးပို့\n"
        "/praylist \\- ဆုတောင်းချက် စာရင်း\n"
        "/quiz \\- Quiz ဖြေဆို\n"
        "/Tops \\- Quiz Ranking\n"
        "/report \\- Admin ထံ တင်ပြ\n"
        "/all \\- Members Mention\n"
        "/vote \\- မဲပေး\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ Admin Commands → /edit"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# ─── /about ───
async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with _db() as conn:
        row = conn.execute(
            "SELECT content FROM about ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row:
        text = f"ℹ️ *အဖွဲ့အကြောင်း*\n━━━━━━━━━━━━━━━━\n{row[0]}"
    else:
        text = (
            "ℹ️ *Church Community*\n━━━━━━━━━━━━━━━━\n"
            "ကျွန်ုပ်တို့သည် ဘုရားသခင်ကို ဝတ်ပြုကိုးကွယ်ကာ\n"
            "အချင်းချင်း ချစ်ကြည်ရင်းနှီးသော လူငယ်အဖွဲ့ဖြစ်သည်။\n\n"
            "_Admin → /edabout ဖြင့် ပြင်ဆင်နိုင်သည်_"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── /contact ───
async def cmd_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with _db() as conn:
        rows = conn.execute(
            "SELECT name, phone, role FROM contacts ORDER BY id"
        ).fetchall()
    if rows:
        text = "📞 *တာဝန်ခံများ ဆက်သွယ်ရန်*\n━━━━━━━━━━━━━━━━\n\n"
        for name, phone, role in rows:
            text += f"👤 *{name}*"
            if role:
                text += f"  \\({role}\\)"
            text += f"\n📱 `{phone}`\n\n"
    else:
        text = (
            "📞 ဆက်သွယ်ရန် အချက်အလက် မရှိသေးပါ\n"
            "_Admin → /edcontact ဖြင့် ထည့်သွင်းနိုင်သည်_"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── /verse ───
async def cmd_verse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hour = datetime.now().hour
    vtype = "morning" if 5 <= hour < 17 else "night"
    with _db() as conn:
        row = conn.execute(
            """SELECT content FROM verses
               WHERE verse_type=? OR verse_type='general'
               ORDER BY RANDOM() LIMIT 1""",
            (vtype,),
        ).fetchone()
    emoji = "🌅" if vtype == "morning" else "🌙"
    label = "မနက်ခင်း" if vtype == "morning" else "ညနေ"
    if row:
        text = f"{emoji} *{label} ကျမ်းချက်*\n━━━━━━━━━━━━━━━━\n\n📖 {row[0]}"
    else:
        text = f"{emoji} ကျမ်းချက် မရှိသေးပါ\n_Admin → /edverse ဖြင့် ထည့်သွင်းနိုင်သည်_"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── /events ───
async def cmd_events(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with _db() as conn:
        rows = conn.execute(
            """SELECT title, description, event_date FROM events
               WHERE date(event_date) >= date('now')
               ORDER BY event_date LIMIT 10"""
        ).fetchall()
    if rows:
        text = "📅 *လာမည့် အစီအစဉ်များ*\n━━━━━━━━━━━━━━━━\n"
        for title, desc, ev_date in rows:
            text += f"\n🗓 *{title}*\n"
            if ev_date:
                text += f"📆 {ev_date}\n"
            if desc:
                text += f"📝 {desc}\n"
    else:
        text = "📅 လာမည့် အစီအစဉ် မရှိသေးပါ\n_Admin → /edevents ဖြင့် ထည့်သွင်းနိုင်သည်_"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── /birthday ───
async def cmd_birthday(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    month, today_day = now.month, now.day
    with _db() as conn:
        rows = conn.execute(
            "SELECT name, birth_day, birth_year FROM birthdays WHERE birth_month=? ORDER BY birth_day",
            (month,),
        ).fetchall()
    month_name = MONTH_NAMES.get(month, str(month))
    if rows:
        text = f"🎂 *{month_name}လ မွေးနေ့ရှင်များ*\n━━━━━━━━━━━━━━━━\n\n"
        for name, day, year in rows:
            is_today = day == today_day
            emo = "🎉" if is_today else "🎂"
            age_txt = f" ({now.year - year} နှစ်)" if year else ""
            text += f"{emo} *{name}*{age_txt} — {month_name} {day}\n"
            if is_today:
                text += "   ╰ 🥳 ယနေ့ မွေးနေ့ဖြစ်သည်!\n"
    else:
        text = f"🎂 {month_name}လတွင် မွေးနေ့ရှင် မရှိသေးပါ\n_Admin → /edbirthday_"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── /pray ───
async def cmd_pray(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "🙏 ဆုတောင်းချက်ကို ရေးပါ:\n"
            "ဥပမာ: `/pray ကျွန်ုပ်မိသားစုအတွက် ဆုတောင်းပေးပါ`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    prayer_text = " ".join(ctx.args)
    with _db() as conn:
        conn.execute(
            """INSERT INTO prayer_requests
               (user_id, username, first_name, content, created_at)
               VALUES (?,?,?,?,?)""",
            (user.id, user.username or "", user.first_name or "", prayer_text, _now()),
        )
        conn.commit()
    await update.message.reply_text(
        f"🙏 *ဆုတောင်းချက် လက်ခံပြီး*\n\n📝 {prayer_text}\n\nကျွန်ုပ်တို့ ဆုတောင်းပေးပါမည် 🙌",
        parse_mode=ParseMode.MARKDOWN,
    )


# ─── /praylist ───
async def cmd_praylist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with _db() as conn:
        rows = conn.execute(
            """SELECT first_name, username, content, created_at
               FROM prayer_requests ORDER BY id DESC LIMIT 20"""
        ).fetchall()
    if rows:
        text = "🙏 *ဆုတောင်းချက် စာရင်း*\n━━━━━━━━━━━━━━━━\n"
        for i, (fname, uname, content, ts) in enumerate(rows, 1):
            display = f"@{uname}" if uname else fname
            text += f"\n*{i}.* {display}\n📝 {content}\n🗓 {ts[:10]}\n"
    else:
        text = "🙏 ဆုတောင်းချက် မရှိသေးပါ\n`/pray <text>` ဖြင့် ပေးပို့နိုင်သည်"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── /quiz ───
async def cmd_quiz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_quiz(update.effective_chat.id, ctx)


async def _send_quiz(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE):
    with _db() as conn:
        row = conn.execute(
            """SELECT id, question, option_a, option_b, option_c, option_d, correct
               FROM quiz_questions ORDER BY RANDOM() LIMIT 1"""
        ).fetchone()
    if not row:
        await ctx.bot.send_message(
            chat_id,
            "📝 Quiz မေးခွန်း မရှိသေးပါ\n_Admin → /edquiz ဖြင့် ထည့်သွင်းနိုင်သည်_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    qid, question, a, b, c, d, correct = row
    text = f"📝 *Quiz မေးခွန်း*\n━━━━━━━━━━━━━━━━\n\n❓ {question}"
    opts = [("A", a), ("B", b), ("C", c), ("D", d)]
    kb = [
        [InlineKeyboardButton(f"{k}. {v}", callback_data=f"quiz_{qid}_{k}")]
        for k, v in opts if v
    ]
    await ctx.bot.send_message(
        chat_id,
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ─── /Tops ───
async def cmd_tops(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with _db() as conn:
        rows = conn.execute(
            "SELECT username, first_name, score FROM quiz_scores ORDER BY score DESC LIMIT 10"
        ).fetchall()
    if rows:
        medals = ["🥇", "🥈", "🥉"]
        text = "🏆 *Quiz Ranking*\n━━━━━━━━━━━━━━━━\n"
        for i, (uname, fname, score) in enumerate(rows, 1):
            medal = medals[i - 1] if i <= 3 else f"*{i}.*"
            display = f"@{uname}" if uname else fname
            text += f"{medal} {display} — *{score}* pts\n"
    else:
        text = "🏆 Ranking မရှိသေးပါ\n`/quiz` ဖြင့် ဖြေဆိုနိုင်သည်"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── /report (ConversationHandler) ───
async def cmd_report_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📢 *Report ပေးပို့ရန်*\n\nမိမိ Report လိုသောအကြောင်းအရာကို ရေးပေးပါ:\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return REPORT_TEXT


async def cmd_report_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rtext = update.message.text
    with _db() as conn:
        admins = conn.execute("SELECT user_id FROM admins").fetchall()
    msg = (
        "📢 *Report လက်ခံပြီး*\n━━━━━━━━━━━━━━━━\n"
        f"👤 From: *{user.first_name}*"
        f"{' (@' + user.username + ')' if user.username else ''}\n"
        f"🆔 ID: `{user.id}`\n━━━━━━━━━━━━━━━━\n"
        f"📝 {rtext}"
    )
    for (aid,) in admins:
        try:
            await ctx.bot.send_message(aid, msg, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
    await update.message.reply_text("✅ Report ပေးပို့ပြီးပါပြီ။ Admin စစ်ဆေးပါမည်")
    return ConversationHandler.END


# ─── /all ───
async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not _is_group(chat.type):
        await update.message.reply_text("⚠️ Group Chat တွင်သာ အသုံးပြုနိုင်သည်")
        return
    with _db() as conn:
        members = conn.execute(
            "SELECT user_id, username, first_name FROM group_members WHERE chat_id=?",
            (chat.id,),
        ).fetchall()
    if not members:
        await update.message.reply_text("⚠️ Members မရှိသေးပါ")
        return
    mentions = []
    for uid, uname, fname in members:
        mentions.append(f"@{uname}" if uname else f"[{fname}](tg://user?id={uid})")
    for i in range(0, len(mentions), 20):
        await ctx.bot.send_message(
            chat.id,
            "📢 " + " ".join(mentions[i : i + 20]),
            parse_mode=ParseMode.MARKDOWN,
        )


# ─── /vote ───
async def cmd_vote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with _db() as conn:
        vote = conn.execute(
            "SELECT id, title, options FROM votes WHERE is_active=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not vote:
        await update.message.reply_text(
            "🗳️ လက်ရှိ မဲပေးရမည့် အကြောင်းအရာ မရှိသေးပါ\n_Admin → /edvote_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    vote_id, title, options_json = vote
    options = json.loads(options_json)
    with _db() as conn:
        kb = []
        for i, opt in enumerate(options):
            cnt = conn.execute(
                "SELECT COUNT(*) FROM vote_records WHERE vote_id=? AND option_index=?",
                (vote_id, i),
            ).fetchone()[0]
            kb.append(
                [InlineKeyboardButton(f"{opt}  ({cnt} မဲ)", callback_data=f"vote_{vote_id}_{i}")]
            )
        kb.append(
            [InlineKeyboardButton("📊 မဲရလဒ် ကြည့်ရန်", callback_data=f"voteresult_{vote_id}")]
        )
    await update.message.reply_text(
        f"🗳️ *{title}*\n━━━━━━━━━━━━━━━━\n\nမဲပေးရန် ရွေးချယ်ပါ:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ═══════════════════════ ADMIN COMMANDS ═══════════════════════

# ─── /edit ───
async def cmd_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = (
        "⚙️ *Admin Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/edit \\- Admin commands\n"
        "/edabout \\- About ပြင်ဆင်\n"
        "/edcontact \\- Contact ထည့်/ပြင်\n"
        "/edverse \\- Verse ထည့်\n"
        "/edevents \\- Events ထည့်\n"
        "/edbirthday \\- Birthday ထည့်/ပြင်\n"
        "/set \\<number\\> \\- Auto Quiz Interval\n"
        "/edquiz \\- Quiz မေးခွန်း ထည့်\n"
        "/edpoint \\<username\\> \\<score\\> \\- Score ပြင်\n"
        "/broadcast \\- Broadcast ပေးပို့\n"
        "/stats \\- Stats ကြည့်\n"
        "/backup \\- DB Backup\n"
        "/restore \\- DB Restore\n"
        "/allclear \\- Data အားလုံးဖျက်\n"
        "/delete \\<type\\> \\<amount\\> \\- Data ဖျက်\n"
        "/eadmin \\<id\\> \\- Admin ထည့်/ဖယ်\n"
        "/edvote \\- Vote သတ်မှတ်"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# ─── /edabout ───
@admin_only
async def cmd_edabout_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✏️ *About ပြင်ဆင်ရန်*\n\nအဖွဲ့အကြောင်း အသစ် ရေးပေးပါ:\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return EDABOUT


async def cmd_edabout_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with _db() as conn:
        conn.execute(
            "INSERT INTO about (content, updated_at) VALUES (?,?)",
            (update.message.text, _now()),
        )
        conn.commit()
    await update.message.reply_text("✅ About သိမ်းဆည်းပြီးပါပြီ!")
    return ConversationHandler.END


# ─── /edcontact ───
@admin_only
async def cmd_edcontact_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 *Contact ထည့်သွင်းရန်*\n\n"
        "Format (တစ်ကြောင်းတစ်ဦး):\n"
        "```\nအမည် | ဖုန်းနံပါတ် | တာဝန်\n"
        "ကိုထူး | 09-123456 | ဦးဆောင်\n```\n"
        "_တာဝန် column ကို မထည့်လည်း ရသည်_\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return EDCONTACT


async def cmd_edcontact_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    count = 0
    with _db() as conn:
        for line in update.message.text.strip().splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                conn.execute(
                    "INSERT INTO contacts (name, phone, role, created_at) VALUES (?,?,?,?)",
                    (parts[0], parts[1], parts[2] if len(parts) > 2 else "", _now()),
                )
                count += 1
        conn.commit()
    await update.message.reply_text(f"✅ Contact {count} ခု ထည့်သွင်းပြီးပါပြီ!")
    return ConversationHandler.END


# ─── /edverse ───
@admin_only
async def cmd_edverse_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Verse ထည့်သွင်းရန်*\n\n"
        "Format: `[morning/night/general] | ကျမ်းချက်`\n"
        "```\nmorning | ယောဟန် ၃:၁၆ — ဘုရားသခင်...\n"
        "night   | ဆာလံ ၂၃:၁ — ထာဝရဘုရား...\n```\n"
        "_type မထည့်ပါက general ဖြစ်သည်_\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return EDVERSE


async def cmd_edverse_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    count = 0
    with _db() as conn:
        for line in update.message.text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                parts = [p.strip() for p in line.split("|", 1)]
                vtype = parts[0].lower() if parts[0].lower() in ("morning", "night", "general") else "general"
                content = parts[1] if len(parts) > 1 else ""
            else:
                vtype, content = "general", line
            if content:
                conn.execute(
                    "INSERT INTO verses (content, verse_type, created_at) VALUES (?,?,?)",
                    (content, vtype, _now()),
                )
                count += 1
        conn.commit()
    await update.message.reply_text(f"✅ Verse {count} ခု ထည့်သွင်းပြီးပါပြီ!")
    return ConversationHandler.END


# ─── /edevents ───
@admin_only
async def cmd_edevents_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📅 *Events ထည့်သွင်းရန်*\n\n"
        "Format (--- ဖြင့် ခွဲပါ):\n"
        "```\nEvent Title\n"
        "Date: 2025-12-25\n"
        "Description: အကြောင်းအရာ\n"
        "---\n```\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return EDEVENTS


async def cmd_edevents_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    count = 0
    with _db() as conn:
        for block in update.message.text.strip().split("---"):
            lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
            if not lines:
                continue
            title = lines[0]
            ev_date = desc = ""
            for line in lines[1:]:
                if line.lower().startswith("date:"):
                    ev_date = line[5:].strip()
                elif line.lower().startswith("description:"):
                    desc = line[12:].strip()
                else:
                    desc += " " + line
            conn.execute(
                "INSERT INTO events (title, description, event_date, created_at) VALUES (?,?,?,?)",
                (title, desc.strip(), ev_date, _now()),
            )
            count += 1
        conn.commit()
    await update.message.reply_text(f"✅ Event {count} ခု ထည့်သွင်းပြီးပါပြီ!")
    return ConversationHandler.END


# ─── /edbirthday ───
@admin_only
async def cmd_edbirthday_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎂 *Birthday ထည့်သွင်းရန်*\n\n"
        "Format: `အမည် | လ/ရက် | နှစ်(optional)`\n"
        "```\nကိုထူး | 3/15 | 2000\n"
        "မနန်းနွေ | 12/25\n```\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return EDBIRTHDAY


async def cmd_edbirthday_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    count = 0
    with _db() as conn:
        for line in update.message.text.strip().splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2:
                continue

            dp = parts[1].split("/")
            if len(dp) < 2:
                continue

            try:
                month, day = int(dp[0]), int(dp[1])
                year = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

                conn.execute(
                    """INSERT INTO birthdays
                       (name, birth_month, birth_day, birth_year, created_at)
                       VALUES (?,?,?,?,?)""",
                    (parts[0], month, day, year, _now()),
                )
                count += 1
            except Exception:
                continue

        conn.commit()

    await update.message.reply_text(f"✅ Birthday {count} ခု ထည့်သွင်းပြီးပါပြီ!")
    return ConversationHandler.END


# ─── /set ───
async def cmd_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin များသာ အသုံးပြုနိုင်သည်")
        return
    if ctx.args and ctx.args[0].isdigit():
        set_setting("quiz_interval", ctx.args[0])
        await update.message.reply_text(f"✅ Quiz Interval → {ctx.args[0]} messages")
    else:
        cur = get_setting("quiz_interval") or "20"
        await update.message.reply_text(
            f"⚙️ Auto Quiz Interval\nလက်ရှိ: *{cur}* messages\n\n"
            "သတ်မှတ်ရန်: `/set <number>`",
            parse_mode=ParseMode.MARKDOWN,
        )


# ─── /edquiz ───
@admin_only
async def cmd_edquiz_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 *Quiz မေးခွန်း ထည့်သွင်းရန်*\n\n"
        "Format (--- ဖြင့် ခွဲပါ):\n"
        "```\nမေးခွန်းစာသား\n"
        "A. ရွေးချယ်မဲ A\n"
        "B. ရွေးချယ်မဲ B\n"
        "C. ရွေးချယ်မဲ C\n"
        "D. ရွေးချယ်မဲ D\n"
        "Answer: A\n"
        "---\n```\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return EDQUIZ


async def cmd_edquiz_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    count = 0
    with _db() as conn:
        for block in update.message.text.strip().split("---"):
            lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
            if len(lines) < 3:
                continue
            question = lines[0]
            a = b = c = d = correct = ""
            for line in lines[1:]:
                ll = line.lower()
                if ll.startswith(("a.", "a)")):
                    a = line[2:].strip()
                elif ll.startswith(("b.", "b)")):
                    b = line[2:].strip()
                elif ll.startswith(("c.", "c)")):
                    c = line[2:].strip()
                elif ll.startswith(("d.", "d)")):
                    d = line[2:].strip()
                elif ll.startswith("answer:"):
                    correct = line[7:].strip().upper()
            if question and correct:
                conn.execute(
                    """INSERT INTO quiz_questions
                       (question, option_a, option_b, option_c, option_d, correct, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (question, a, b, c, d, correct, _now()),
                )
                count += 1
        conn.commit()
    await update.message.reply_text(f"✅ Quiz {count} ခု ထည့်သွင်းပြီးပါပြီ!")
    return ConversationHandler.END


# ─── /edpoint ───
async def cmd_edpoint(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin များသာ အသုံးပြုနိုင်သည်")
        return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "📊 Score ပြင်ဆင်ရန်:\n`/edpoint <username> <score>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    uname = ctx.args[0].lstrip("@")
    try:
        score = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Score ဂဏန်းဖြစ်ရမည်")
        return
    with _db() as conn:
        if conn.execute("SELECT 1 FROM quiz_scores WHERE username=?", (uname,)).fetchone():
            conn.execute("UPDATE quiz_scores SET score=? WHERE username=?", (score, uname))
            msg = f"✅ @{uname} ၏ Score → *{score}* pts"
        else:
            conn.execute(
                "INSERT INTO quiz_scores (username, first_name, score) VALUES (?,?,?)",
                (uname, uname, score),
            )
            msg = f"✅ @{uname} ကို *{score}* pts ဖြင့် ထည့်သွင်းပြီး"
        conn.commit()
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# ─── /broadcast ───
@admin_only
async def cmd_broadcast_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📢 *Broadcast ပေးပို့ရန်*\n\nMessage (စာ / ပုံ) ကို ပေးပို့ပါ:\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return BROADCAST


async def cmd_broadcast_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with _db() as conn:
        groups = [r[0] for r in conn.execute("SELECT chat_id FROM groups").fetchall()]
        users  = [r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()]
    targets = list(set(groups + users))
    ok = fail = 0
    for cid in targets:
        try:
            if update.message.photo:
                await ctx.bot.send_photo(
                    cid,
                    update.message.photo[-1].file_id,
                    caption=update.message.caption or "",
                )
            else:
                await ctx.bot.send_message(cid, update.message.text)
            ok += 1
        except Exception:
            fail += 1
    await update.message.reply_text(
        f"📢 Broadcast ပြီးပါပြီ!\n✅ Success: {ok}\n❌ Failed: {fail}"
    )
    return ConversationHandler.END


# ─── /stats ───
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin များသာ အသုံးပြုနိုင်သည်")
        return
    with _db() as conn:
        u = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        g = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        p = conn.execute("SELECT COUNT(*) FROM prayer_requests").fetchone()[0]
        q = conn.execute("SELECT COUNT(*) FROM quiz_questions").fetchone()[0]
        v = conn.execute("SELECT COUNT(*) FROM verses").fetchone()[0]
        b = conn.execute("SELECT COUNT(*) FROM birthdays").fetchone()[0]
        grp_list = conn.execute(
            "SELECT title FROM groups ORDER BY joined_at DESC LIMIT 5"
        ).fetchall()
    gnames = "\n".join(f"  • {r[0]}" for r in grp_list) if grp_list else "  _မရှိသေးပါ_"
    text = (
        "📊 *Bot Statistics*\n━━━━━━━━━━━━━━━━\n"
        f"👥 Users     : {u}\n"
        f"👥 Groups    : {g}\n"
        f"🙏 Prayers   : {p}\n"
        f"📝 Quizzes   : {q}\n"
        f"📖 Verses    : {v}\n"
        f"🎂 Birthdays : {b}\n\n"
        f"*Groups List:*\n{gnames}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ─── /backup ───
async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin များသာ အသုံးပြုနိုင်သည်")
        return
    fname = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    tmp   = f"/tmp/{fname}"
    try:
        shutil.copy2(DB_PATH, tmp)
        with open(tmp, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=fname,
                caption=f"✅ Database Backup\n📅 {_now()}",
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Backup မအောင်မြင်ပါ: {e}")


# ─── /restore ───
@admin_only
async def cmd_restore_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔄 *Database Restore*\n\nBackup *.db* ဖိုင်ကို ပေးပို့ပါ:\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return RESTORE_FILE


async def cmd_restore_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text("❌ Database ဖိုင် မပေးပို့ပါ")
        return RESTORE_FILE
    if not update.message.document.file_name.endswith(".db"):
        await update.message.reply_text("❌ .db ဖိုင်သာ လက်ခံသည်")
        return RESTORE_FILE
    try:
        tg_file = await ctx.bot.get_file(update.message.document.file_id)
        await tg_file.download_to_drive(DB_PATH)
        await update.message.reply_text(f"✅ Database ကို အောင်မြင်စွာ ပြန်လည်သိမ်းဆည်းပြီးပါပြီ!\n📅 {_now()}")
    except Exception as e:
        await update.message.reply_text(f"❌ Restore မအောင်မြင်ပါ: {e}")
        return RESTORE_FILE
    return ConversationHandler.END


# ─── /allclear ───
async def cmd_allclear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin များသာ အသုံးပြုနိုင်သည်")
        return
    kb = [
        [
            InlineKeyboardButton("✅ အတည်ပြုမည်", callback_data="allclear_confirm"),
            InlineKeyboardButton("❌ ပယ်ဖျက်မည်", callback_data="allclear_cancel"),
        ]
    ]
    await update.message.reply_text(
        "⚠️ *သတိပြုပါ!*\n\nData အားလုံးကို ဖျက်ပစ်မည်။ သေချာပါသလား?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ─── /delete ───
async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin များသာ အသုံးပြုနိုင်သည်")
        return
    TYPE_MAP = {
        "verse": "verses", "quiz": "quiz_questions", "event": "events",
        "birthday": "birthdays", "prayer": "prayer_requests", "contact": "contacts",
    }
    if len(ctx.args) < 2:
        types = " / ".join(TYPE_MAP.keys())
        await update.message.reply_text(
            f"🗑️ `/delete <type> <amount>`\nTypes: `{types}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    dtype = ctx.args[0].lower()
    if dtype not in TYPE_MAP:
        await update.message.reply_text(f"❌ Type မမှန်ကန်ပါ: {dtype}")
        return
    try:
        amount = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Amount ဂဏန်းဖြစ်ရမည်")
        return
    table = TYPE_MAP[dtype]
    with _db() as conn:
        conn.execute(
            f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} ORDER BY id DESC LIMIT ?)",
            (amount,),
        )
        deleted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
    await update.message.reply_text(f"✅ {dtype} {deleted} ခု ဖျက်ပြီးပါပြီ!")


# ─── /eadmin ───
async def cmd_eadmin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID:
        await update.message.reply_text("⛔ Super Admin သာ Admin စီမံနိုင်သည်")
        return
    if not ctx.args:
        with _db() as conn:
            rows = conn.execute("SELECT user_id, username FROM admins").fetchall()
        text = "👑 *Admin List*\n━━━━━━━━━━━━━━━━\n"
        for uid, uname in rows:
            text += f"• `{uid}` — @{uname or 'Unknown'}\n"
        text += "\nAdmin ထည့်/ဖယ်ရှားရန်: `/eadmin <user_id>`"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return
    try:
        tid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID ဂဏန်းဖြစ်ရမည်")
        return
    with _db() as conn:
        if conn.execute("SELECT 1 FROM admins WHERE user_id=?", (tid,)).fetchone():
            if tid == SUPER_ADMIN_ID:
                await update.message.reply_text("⛔ Super Admin ကို ဖယ်ရှားမရပါ!")
                return
            conn.execute("DELETE FROM admins WHERE user_id=?", (tid,))
            msg = f"✅ ID `{tid}` ကို Admin မှ ဖယ်ရှားပြီး"
        else:
            conn.execute(
                "INSERT INTO admins (user_id, username, added_at) VALUES (?,?,?)",
                (tid, "", _now()),
            )
            msg = f"✅ ID `{tid}` ကို Admin ထည့်ပြီး"
        conn.commit()
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# ─── /edvote ───
@admin_only
async def cmd_edvote_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🗳️ *Vote သတ်မှတ်ရန်*\n\nVote ၏ ခေါင်းစဉ် ရေးပါ:\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return EDVOTE_TITLE


async def cmd_edvote_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["vote_title"] = update.message.text
    await update.message.reply_text(
        "🗳️ ရွေးချယ်မဲ Options (တစ်ကြောင်းတစ်ဦး၊ 2–10 ဦး):\n"
        "```\nကိုထူး\nမနန်း\nဒေါ်သိန်း\n```\n\n_Cancel: /cancel_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return EDVOTE_OPTIONS


async def cmd_edvote_options(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    title   = ctx.user_data.pop("vote_title", "Vote")
    options = [o.strip() for o in update.message.text.strip().splitlines() if o.strip()]
    if len(options) < 2:
        await update.message.reply_text("❌ Options အနည်းဆုံး ၂ ခု လိုအပ်သည်")
        return EDVOTE_OPTIONS
    if len(options) > 10:
        await update.message.reply_text("❌ Options အများဆုံး ၁၀ ခုသာ ခွင့်ပြုသည်")
        return EDVOTE_OPTIONS
    with _db() as conn:
        conn.execute("UPDATE votes SET is_active=0")
        conn.execute(
            "INSERT INTO votes (title, options, is_active, created_at) VALUES (?,?,1,?)",
            (title, json.dumps(options, ensure_ascii=False), _now()),
        )
        conn.commit()
    opts_txt = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options))
    await update.message.reply_text(
        f"✅ Vote သတ်မှတ်ပြီး!\n\n📋 *{title}*\n{opts_txt}",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


# ─── /cancel ───
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ ပယ်ဖျက်ပြီးပါပြီ!")
    return ConversationHandler.END


# ═══════════════════ CALLBACK QUERY HANDLER ═══════════════════
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    await q.answer()

    # ── Inline buttons from /start ──
    if data == "cb_helps":
        await q.edit_message_text(
            "📋 *Commands*\n/start /helps /about /contact\n"
            "/verse /events /birthday /pray /praylist\n"
            "/quiz /Tops /report /all /vote",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif data == "cb_about":
        with _db() as conn:
            row = conn.execute("SELECT content FROM about ORDER BY id DESC LIMIT 1").fetchone()
        text = f"ℹ️ *About*\n\n{row[0]}" if row else "ℹ️ About မရှိသေးပါ"
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

    elif data == "cb_verse":
        hour  = datetime.now().hour
        vtype = "morning" if 5 <= hour < 17 else "night"
        with _db() as conn:
            row = conn.execute(
                "SELECT content FROM verses WHERE verse_type=? OR verse_type='general' ORDER BY RANDOM() LIMIT 1",
                (vtype,),
            ).fetchone()
        emoji = "🌅" if vtype == "morning" else "🌙"
        text  = f"{emoji} *Daily Verse*\n\n{row[0]}" if row else "📖 Verse မရှိသေးပါ"
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

    elif data == "cb_events":
        with _db() as conn:
            rows = conn.execute(
                "SELECT title, event_date FROM events WHERE date(event_date)>=date('now') ORDER BY event_date LIMIT 5"
            ).fetchall()
        text = "📅 *Events*\n\n" + "\n".join(f"🗓 {t} — {d}" for t, d in rows) if rows else "📅 Events မရှိသေးပါ"
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

       # ── Quiz answer ──
    elif data.startswith("quiz_"):
        parts = data.split("_")               # quiz_<qid>_<key>
        if len(parts) < 3:
            return
        try:
            qid, sel = int(parts[1]), parts[2]
        except ValueError:
            return
        user = q.from_user
        with _db() as conn:
            row = conn.execute(
                "SELECT correct FROM quiz_questions WHERE id=?", (qid,)
            ).fetchone()
            if not row:
                await q.edit_message_text("❌ မေးခွန်း မတွေ့ပါ")
                return
            correct = row[0]

            if sel.upper() == correct.upper():
                # Robust upsert (works even if SQLite ON CONFLICT syntax not available)
                exists = conn.execute(
                    "SELECT 1 FROM quiz_scores WHERE user_id=?", (user.id,)
                ).fetchone()
                if exists:
                    conn.execute(
                        "UPDATE quiz_scores SET score = score + 1, username = ?, first_name = ? WHERE user_id = ?",
                        (user.username or "", user.first_name or "", user.id),
                    )
                else:
                    conn.execute(
                        "INSERT INTO quiz_scores (user_id, username, first_name, score) VALUES (?,?,?,1)",
                        (user.id, user.username or "", user.first_name or ""),
                    )
                conn.commit()
                resp = f"✅ *မှန်သည်!* 🎉\n*{user.first_name}* +1 pt ရရှိသည်!"
            else:
                resp = f"❌ *မှားသည်!*\nမှန်သောအဖြေ: *{correct}*"
        await q.edit_message_text(resp, parse_mode=ParseMode.MARKDOWN)

    # ── Vote ──
    elif data.startswith("voteresult_"):
        vid = int(data.split("_")[1])
        await _show_vote_results(q, vid)

    elif data.startswith("vote_"):
        parts = data.split("_")               # vote_<vid>_<idx>
        if len(parts) < 3:
            return
        vid, idx = int(parts[1]), int(parts[2])
        user = q.from_user
        with _db() as conn:
            if conn.execute(
                "SELECT 1 FROM vote_records WHERE vote_id=? AND user_id=?", (vid, user.id)
            ).fetchone():
                await q.answer("⚠️ သင် မဲပေးပြီးပါပြီ!", show_alert=True)
                return
            conn.execute(
                "INSERT INTO vote_records (vote_id, user_id, option_index, voted_at) VALUES (?,?,?,?)",
                (vid, user.id, idx, _now()),
            )
            conn.commit()
            row = conn.execute("SELECT options FROM votes WHERE id=?", (vid,)).fetchone()
        if row:
            opts = json.loads(row[0])
            sel_opt = opts[idx] if idx < len(opts) else "?"
            await q.answer(f"✅ '{sel_opt}' ကို မဲပေးပြီး!", show_alert=True)

    # ── AllClear confirm ──
    elif data == "allclear_confirm":
        if not is_admin(q.from_user.id):
            await q.answer("⛔ Admin သာ ဖျက်နိုင်သည်!", show_alert=True)
            return
        tables = [
            "about", "contacts", "verses", "events", "birthdays",
            "prayer_requests", "quiz_questions", "quiz_scores",
            "votes", "vote_records",
        ]
        with _db() as conn:
            for t in tables:
                conn.execute(f"DELETE FROM {t}")
            conn.commit()
        await q.edit_message_text("✅ Data အားလုံး ဖျက်ပြီးပါပြီ!")

    elif data == "allclear_cancel":
        await q.edit_message_text("❌ ဖျက်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ!")


async def _show_vote_results(q, vote_id: int):
    with _db() as conn:
        vrow = conn.execute("SELECT title, options FROM votes WHERE id=?", (vote_id,)).fetchone()
        if not vrow:
            await q.edit_message_text("❌ မဲပေးခြင်း မတွေ့ပါ")
            return
        title, options_json = vrow
        options = json.loads(options_json)
        total = conn.execute(
            "SELECT COUNT(*) FROM vote_records WHERE vote_id=?", (vote_id,)
        ).fetchone()[0]
        text = f"📊 *{title} — မဲရလဒ်*\n━━━━━━━━━━━━━━━━\n\n"
        for i, opt in enumerate(options):
            cnt = conn.execute(
                "SELECT COUNT(*) FROM vote_records WHERE vote_id=? AND option_index=?",
                (vote_id, i),
            ).fetchone()[0]
            pct  = cnt / total * 100 if total else 0
            bars = int(pct / 10)
            bar  = "█" * bars + "░" * (10 - bars)
            text += f"👤 *{opt}*\n{bar}  {cnt} မဲ ({pct:.1f}%)\n\n"
        text += f"📊 စုစုပေါင်း: *{total}* မဲ"
    await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)


# ══════════════════════ MESSAGE TRACKER ═══════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Track users/groups and handle auto-quiz countdown."""
    chat = update.effective_chat
    user = update.effective_user
    if not user:
        return
    track_user(user, chat.id)
    if _is_group(chat.type):
        track_group(chat)
        track_member(chat.id, user)

        with _db() as conn:
            conn.execute(
                "INSERT INTO message_counters (chat_id, count) VALUES (?,1)"
                " ON CONFLICT(chat_id) DO UPDATE SET count=count+1",
                (chat.id,),
            )
            cnt = conn.execute(
                "SELECT count FROM message_counters WHERE chat_id=?", (chat.id,)
            ).fetchone()[0]
            interval = int(get_setting("quiz_interval") or "20")
            if cnt >= interval:
                conn.execute(
                    "UPDATE message_counters SET count=0 WHERE chat_id=?", (chat.id,)
                )
            conn.commit()
        if cnt >= interval:
            await _send_quiz(chat.id, ctx)


# ═══════════════════════════ MAIN ═════════════════════════════
def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in .env")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Conversation Handlers ──
    def conv(entry_cmd, entry_fn, state_id, state_fn, extra_filter=None):
        msg_filter = extra_filter or (filters.TEXT & ~filters.COMMAND)
        return ConversationHandler(
            entry_points=[CommandHandler(entry_cmd, entry_fn)],
            states={state_id: [MessageHandler(msg_filter, state_fn)]},
            fallbacks=[CommandHandler("cancel", cmd_cancel)],
        )

    # multi-state edvote
    edvote_conv = ConversationHandler(
        entry_points=[CommandHandler("edvote", cmd_edvote_start)],
        states={
            EDVOTE_TITLE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_edvote_title)],
            EDVOTE_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_edvote_options)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    report_conv = ConversationHandler(
        entry_points=[CommandHandler("report", cmd_report_start)],
        states={REPORT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_report_receive)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    restore_conv = ConversationHandler(
        entry_points=[CommandHandler("restore", cmd_restore_start)],
        states={RESTORE_FILE: [MessageHandler(filters.Document.ALL, cmd_restore_receive)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", cmd_broadcast_start)],
        states={BROADCAST: [MessageHandler(filters.ALL & ~filters.COMMAND, cmd_broadcast_send)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    for handler in [
        conv("edabout",   cmd_edabout_start,   EDABOUT,   cmd_edabout_save),
        conv("edcontact", cmd_edcontact_start, EDCONTACT, cmd_edcontact_save),
        conv("edverse",   cmd_edverse_start,   EDVERSE,   cmd_edverse_save),
        conv("edevents",  cmd_edevents_start,  EDEVENTS,  cmd_edevents_save),
        conv("edbirthday",cmd_edbirthday_start,EDBIRTHDAY,cmd_edbirthday_save),
        conv("edquiz",    cmd_edquiz_start,    EDQUIZ,    cmd_edquiz_save),
        edvote_conv, report_conv, restore_conv, broadcast_conv,
    ]:
        app.add_handler(handler)

    # ── User commands ──
    user_cmds = [
        ("start",    cmd_start),
        ("helps",    cmd_helps),
        ("about",    cmd_about),
        ("contact",  cmd_contact),
        ("verse",    cmd_verse),
        ("events",   cmd_events),
        ("birthday", cmd_birthday),
        ("pray",     cmd_pray),
        ("praylist", cmd_praylist),
        ("quiz",     cmd_quiz),
        ("all",      cmd_all),
        ("vote",     cmd_vote),
        ("cancel",   cmd_cancel),
    ]
    for cmd, fn in user_cmds:
        app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(CommandHandler(["Tops", "tops"], cmd_tops))

    # ── Admin commands ──
    admin_cmds = [
        ("edit",     cmd_edit),
        ("set",      cmd_set),
        ("edpoint",  cmd_edpoint),
        ("stats",    cmd_stats),
        ("backup",   cmd_backup),
        ("allclear", cmd_allclear),
        ("delete",   cmd_delete),
        ("eadmin",   cmd_eadmin),
    ]
    for cmd, fn in admin_cmds:
        app.add_handler(CommandHandler(cmd, fn))

    # ── Callback & message ──
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))

    logger.info("✝️  Church Community Bot is running …")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main() 
