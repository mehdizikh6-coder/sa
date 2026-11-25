import json
import logging
import os
import re
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests

# -------------------- تنظیمات --------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR))).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
PLAYERS_FILE = DATA_DIR / "players.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()  # اختیاری؛ می‌توان بعداً تنظیم کرد
MAX_MEDIA_SIZE = int(os.getenv("MAX_MEDIA_SIZE_BYTES", str(2 * 1024 * 1024)))
API = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
MAX_CAPTION_LENGTH = 1024
SAFE_MESSAGE_LENGTH = 3900
POLL_TIMEOUT = 50

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("statement-bot")

if not TOKEN:
    raise RuntimeError("متغیر TELEGRAM_BOT_TOKEN در Environment تنظیم نشده است.")
if not ADMIN_ID.isdigit():
    raise RuntimeError("ADMIN_ID باید آیدی عددی مالک ربات باشد.")

states: dict[str, dict[str, Any]] = {}
players: dict[str, str] = {}
settings = {"enabled": os.getenv("BOT_ENABLED", "true").lower() != "false"}
session = requests.Session()


TEXT = {
    "unauthorized": "کاربر گرامی، شما مجاز به استفاده از ربات نمی‌باشید ❌",
    "disabled": "ربات در حال حاضر غیرفعال است ❌",
    "media_prompt": "👋 کاربر گرامی، خوش آمدید!\n\n🏞 لطفاً رسانه خود را ارسال کنید (عکس یا ویدیو).\n⚠️ حجم رسانه حداکثر ۲ مگابایت باشد.",
    "media_received": "✅ رسانه دریافت شد.\n\n🗣 لطفاً بیانیه خود را ارسال کنید.\n📏 متن بیانیه باید حداقل ۴ خط و ۴۵ حرف داشته باشد.",
    "invalid_media": "❌ فایل شناسایی نشد. لطفاً یک عکس یا ویدیو ارسال کنید.",
    "too_large": "❌ حجم رسانه بیشتر از حد مجاز ۲ مگابایت است.",
    "text_short": "📏 بیانیه باید حداقل ۴ خط و ۴۵ حرف داشته باشد. لطفاً متن بیشتری ارسال کنید.",
}


def load_json(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default.copy()
    except (OSError, json.JSONDecodeError):
        return default.copy()


def save_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def ensure_data() -> None:
    global players, settings
    # در اولین اجرا روی دیسک پایدار، فایل‌های اولیه ریشه مخزن به آن منتقل می‌شوند.
    if not PLAYERS_FILE.exists() and (BASE_DIR / "players.json").exists():
        players_seed = load_json(BASE_DIR / "players.json", {})
        save_json(PLAYERS_FILE, players_seed)
    if not SETTINGS_FILE.exists() and (BASE_DIR / "settings.json").exists():
        settings_seed = load_json(BASE_DIR / "settings.json", {})
        save_json(SETTINGS_FILE, settings_seed)
    players = load_json(PLAYERS_FILE, {})
    settings = {**settings, **load_json(SETTINGS_FILE, {})}
    save_json(PLAYERS_FILE, players)
    save_json(SETTINGS_FILE, settings)


def tg(method: str, payload: dict | None = None) -> Any:
    response = session.post(f"{API}/{method}", json=payload or {}, timeout=65)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method}: {result.get('description', 'خطای ناشناخته')}")
    return result.get("result")


def send_message(chat_id: int | str, text: str, **kwargs) -> Any:
    return tg("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        **kwargs,
    })


def is_admin(user_id: int | str) -> bool:
    return str(user_id) == ADMIN_ID


def is_player(user_id: int | str) -> bool:
    return bool(players.get(str(user_id)))


def esc(value: Any) -> str:
    value = str(value)
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


COUNTRY_FLAGS = {'افغانستان': '🇦🇫', 'afghanistan': '🇦🇫', 'af': '🇦🇫', 'آلبانی': '🇦🇱', 'albania': '🇦🇱', 'al': '🇦🇱', 'الجزایر': '🇩🇿', 'algeria': '🇩🇿', 'dz': '🇩🇿', 'آرژانتین': '🇦🇷', 'argentina': '🇦🇷', 'ar': '🇦🇷', 'ارمنستان': '🇦🇲', 'armenia': '🇦🇲', 'am': '🇦🇲', 'استرالیا': '🇦🇺', 'australia': '🇦🇺', 'au': '🇦🇺', 'اتریش': '🇦🇹', 'austria': '🇦🇹', 'at': '🇦🇹', 'آذربایجان': '🇦🇿', 'azerbaijan': '🇦🇿', 'az': '🇦🇿', 'بحرین': '🇧🇭', 'bahrain': '🇧🇭', 'bh': '🇧🇭', 'بنگلادش': '🇧🇩', 'bangladesh': '🇧🇩', 'bd': '🇧🇩', 'بلاروس': '🇧🇾', 'belarus': '🇧🇾', 'by': '🇧🇾', 'بلژیک': '🇧🇪', 'belgium': '🇧🇪', 'be': '🇧🇪', 'برزیل': '🇧🇷', 'brazil': '🇧🇷', 'br': '🇧🇷', 'بلغارستان': '🇧🇬', 'bulgaria': '🇧🇬', 'bg': '🇧🇬', 'کانادا': '🇨🇦', 'canada': '🇨🇦', 'ca': '🇨🇦', 'شیلی': '🇨🇱', 'chile': '🇨🇱', 'cl': '🇨🇱', 'چین': '🇨🇳', 'china': '🇨🇳', 'cn': '🇨🇳', 'کلمبیا': '🇨🇴', 'colombia': '🇨🇴', 'co': '🇨🇴', 'کرواسی': '🇭🇷', 'croatia': '🇭🇷', 'hr': '🇭🇷', 'کوبا': '🇨🇺', 'cuba': '🇨🇺', 'cu': '🇨🇺', 'قبرس': '🇨🇾', 'cyprus': '🇨🇾', 'cy': '🇨🇾', 'جمهوری چک': '🇨🇿', 'czech republic': '🇨🇿', 'cz': '🇨🇿', 'دانمارک': '🇩🇰', 'denmark': '🇩🇰', 'dk': '🇩🇰', 'مصر': '🇪🇬', 'egypt': '🇪🇬', 'eg': '🇪🇬', 'استونی': '🇪🇪', 'estonia': '🇪🇪', 'ee': '🇪🇪', 'فنلاند': '🇫🇮', 'finland': '🇫🇮', 'fi': '🇫🇮', 'فرانسه': '🇫🇷', 'france': '🇫🇷', 'fr': '🇫🇷', 'گرجستان': '🇬🇪', 'georgia': '🇬🇪', 'ge': '🇬🇪', 'آلمان': '🇩🇪', 'germany': '🇩🇪', 'de': '🇩🇪', 'یونان': '🇬🇷', 'greece': '🇬🇷', 'gr': '🇬🇷', 'هنگ کنگ': '🇭🇰', 'hong kong': '🇭🇰', 'hk': '🇭🇰', 'مجارستان': '🇭🇺', 'hungary': '🇭🇺', 'hu': '🇭🇺', 'ایسلند': '🇮🇸', 'iceland': '🇮🇸', 'is': '🇮🇸', 'هند': '🇮🇳', 'india': '🇮🇳', 'in': '🇮🇳', 'اندونزی': '🇮🇩', 'indonesia': '🇮🇩', 'id': '🇮🇩', 'ایران': '🇮🇷', 'iran': '🇮🇷', 'ir': '🇮🇷', 'عراق': '🇮🇶', 'iraq': '🇮🇶', 'iq': '🇮🇶', 'ایرلند': '🇮🇪', 'ireland': '🇮🇪', 'ie': '🇮🇪', 'اسرائیل': '🇮🇱', 'israel': '🇮🇱', 'il': '🇮🇱', 'ایتالیا': '🇮🇹', 'italy': '🇮🇹', 'it': '🇮🇹', 'ژاپن': '🇯🇵', 'japan': '🇯🇵', 'jp': '🇯🇵', 'اردن': '🇯🇴', 'jordan': '🇯🇴', 'jo': '🇯🇴', 'قزاقستان': '🇰🇿', 'kazakhstan': '🇰🇿', 'kz': '🇰🇿', 'کنیا': '🇰🇪', 'kenya': '🇰🇪', 'ke': '🇰🇪', 'کویت': '🇰🇼', 'kuwait': '🇰🇼', 'kw': '🇰🇼', 'لبنان': '🇱🇧', 'lebanon': '🇱🇧', 'lb': '🇱🇧', 'لیتوانی': '🇱🇹', 'lithuania': '🇱🇹', 'lt': '🇱🇹', 'لوکزامبورگ': '🇱🇺', 'luxembourg': '🇱🇺', 'lu': '🇱🇺', 'مالزی': '🇲🇾', 'malaysia': '🇲🇾', 'my': '🇲🇾', 'مکزیک': '🇲🇽', 'mexico': '🇲🇽', 'mx': '🇲🇽', 'مراکش': '🇲🇦', 'morocco': '🇲🇦', 'ma': '🇲🇦', 'هلند': '🇳🇱', 'netherlands': '🇳🇱', 'nl': '🇳🇱', 'نیوزیلند': '🇳🇿', 'new zealand': '🇳🇿', 'nz': '🇳🇿', 'نیجریه': '🇳🇬', 'nigeria': '🇳🇬', 'ng': '🇳🇬', 'کره شمالی': '🇰🇵', 'north korea': '🇰🇵', 'kp': '🇰🇵', 'نروژ': '🇳🇴', 'norway': '🇳🇴', 'no': '🇳🇴', 'عمان': '🇴🇲', 'oman': '🇴🇲', 'om': '🇴🇲', 'پاکستان': '🇵🇰', 'pakistan': '🇵🇰', 'pk': '🇵🇰', 'فلسطین': '🇵🇸', 'palestine': '🇵🇸', 'ps': '🇵🇸', 'پرو': '🇵🇪', 'peru': '🇵🇪', 'pe': '🇵🇪', 'فیلیپین': '🇵🇭', 'philippines': '🇵🇭', 'ph': '🇵🇭', 'لهستان': '🇵🇱', 'poland': '🇵🇱', 'pl': '🇵🇱', 'پرتغال': '🇵🇹', 'portugal': '🇵🇹', 'pt': '🇵🇹', 'قطر': '🇶🇦', 'qatar': '🇶🇦', 'qa': '🇶🇦', 'رومانی': '🇷🇴', 'romania': '🇷🇴', 'ro': '🇷🇴', 'روسیه': '🇷🇺', 'russia': '🇷🇺', 'ru': '🇷🇺', 'عربستان': '🇸🇦', 'saudi arabia': '🇸🇦', 'sa': '🇸🇦', 'صربستان': '🇷🇸', 'serbia': '🇷🇸', 'rs': '🇷🇸', 'سنگاپور': '🇸🇬', 'singapore': '🇸🇬', 'sg': '🇸🇬', 'اسلواکی': '🇸🇰', 'slovakia': '🇸🇰', 'sk': '🇸🇰', 'اسلوونی': '🇸🇮', 'slovenia': '🇸🇮', 'si': '🇸🇮', 'آفریقای جنوبی': '🇿🇦', 'south africa': '🇿🇦', 'za': '🇿🇦', 'کره جنوبی': '🇰🇷', 'south korea': '🇰🇷', 'kr': '🇰🇷', 'اسپانیا': '🇪🇸', 'spain': '🇪🇸', 'es': '🇪🇸', 'سوئد': '🇸🇪', 'sweden': '🇸🇪', 'se': '🇸🇪', 'سوئیس': '🇨🇭', 'switzerland': '🇨🇭', 'ch': '🇨🇭', 'سوریه': '🇸🇾', 'syria': '🇸🇾', 'sy': '🇸🇾', 'تایوان': '🇹🇼', 'taiwan': '🇹🇼', 'tw': '🇹🇼', 'تاجیکستان': '🇹🇯', 'tajikistan': '🇹🇯', 'tj': '🇹🇯', 'تایلند': '🇹🇭', 'thailand': '🇹🇭', 'th': '🇹🇭', 'ترکیه': '🇹🇷', 'turkey': '🇹🇷', 'tr': '🇹🇷', 'ترکمنستان': '🇹🇲', 'turkmenistan': '🇹🇲', 'tm': '🇹🇲', 'اوکراین': '🇺🇦', 'ukraine': '🇺🇦', 'ua': '🇺🇦', 'امارات': '🇦🇪', 'united arab emirates': '🇦🇪', 'uae': '🇦🇪', 'ae': '🇦🇪', 'انگلستان': '🇬🇧', 'بریتانیا': '🇬🇧', 'united kingdom': '🇬🇧', 'uk': '🇬🇧', 'gb': '🇬🇧', 'آمریکا': '🇺🇸', 'ایالات متحده': '🇺🇸', 'united states': '🇺🇸', 'usa': '🇺🇸', 'us': '🇺🇸', 'ازبکستان': '🇺🇿', 'uzbekistan': '🇺🇿', 'uz': '🇺🇿', 'ونزوئلا': '🇻🇪', 'venezuela': '🇻🇪', 've': '🇻🇪', 'ویتنام': '🇻🇳', 'vietnam': '🇻🇳', 'vn': '🇻🇳', 'یمن': '🇾🇪', 'yemen': '🇾🇪', 'ye': '🇾🇪', 'زیمبابوه': '🇿🇼', 'zimbabwe': '🇿🇼', 'zw': '🇿🇼'}


def country_flag(country: str) -> str:
    value = str(country or "").strip().lower()
    if value in COUNTRY_FLAGS:
        return COUNTRY_FLAGS[value]
    if re.fullmatch(r"[a-z]{2}", value):
        return "".join(chr(127397 + ord(c)) for c in value.upper())
    return "🌍"


def channel_heading(country: str) -> str:
    return f"{country_flag(country)} <b>بیانیه‌ای از طرف کشور {esc(country or 'بدون کشور')}</b>"


def chunks(text: str, limit: int = SAFE_MESSAGE_LENGTH) -> list[str]:
    result = []
    remaining = text.strip()
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        result.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        result.append(remaining)
    return result


def clear_state(user_id: int | str) -> None:
    states.pop(str(user_id), None)


def set_state(user_id: int | str, value: dict) -> None:
    states[str(user_id)] = value


def get_state(user_id: int | str) -> dict | None:
    return states.get(str(user_id))


def main_menu(chat_id: int, user: dict) -> None:
    country = players.get(str(user["id"]), "بدون کشور")
    name = esc(user.get("first_name") or "کاربر")
    text = (
        f'👋 سلام <a href="tg://user?id={user["id"]}">{name}</a>! خوش اومدی!\n\n'
        f"{country_flag(country)} کشور تو: {esc(country)}\n\n"
        "⚡️ آماده‌ای برای انتشار؟ از دکمه زیر شروع کن!"
    )
    send_message(chat_id, text, reply_markup={
        "inline_keyboard": [[{"text": "📜 بیانیه", "callback_data": "statement:start"}]]
    })


def admin_panel(chat_id: int) -> None:
    send_message(chat_id, "🛠 <b>پنل مدیریت</b>\n\nیکی از عملیات زیر را انتخاب کنید:", reply_markup={
        "inline_keyboard": [
            [
                {"text": "➕ ثبت بازیکن", "callback_data": "admin:add"},
                {"text": "➖ حذف بازیکن", "callback_data": "admin:remove"},
            ],
            [
                {"text": "👥 مشاهده بازیکنان", "callback_data": "admin:list"},
                {"text": "✏️ ویرایش کشور", "callback_data": "admin:edit"},
            ],
            [{"text": "⏸ خاموش کردن ربات" if settings["enabled"] else "▶️ روشن کردن ربات",
              "callback_data": "admin:toggle"}],
        ]
    })


def start_statement(chat_id: int, user_id: int) -> None:
    set_state(user_id, {"step": "awaiting_media"})
    send_message(chat_id, TEXT["media_prompt"])


def handle_media(message: dict) -> bool:
    user_id = message["from"]["id"]
    state = get_state(user_id)
    if not state or state.get("step") != "awaiting_media":
        return False
    media = None
    media_type = None
    if message.get("photo"):
        media = message["photo"][-1]
        media_type = "photo"
    elif message.get("video"):
        media = message["video"]
        media_type = "video"
    if not media:
        send_message(message["chat"]["id"], TEXT["invalid_media"])
        return True
    if media.get("file_size", 0) > MAX_MEDIA_SIZE:
        send_message(message["chat"]["id"], TEXT["too_large"])
        return True
    set_state(user_id, {"step": "awaiting_text", "media_type": media_type, "file_id": media["file_id"]})
    send_message(message["chat"]["id"], TEXT["media_received"])
    return True


def quote_html(text: str) -> str:
    return f"<blockquote><b>{esc(text)}</b></blockquote>"


def send_preview(chat_id: int, state: dict, statement: str) -> None:
    country = players.get(str(chat_id), "بدون کشور")
    heading = channel_heading(country)
    caption = f"{heading}\n\n{quote_html(statement)}"
    keyboard = {"inline_keyboard": [[
        {"text": "✅ تأیید و ارسال", "callback_data": "statement:confirm"},
        {"text": "❌ رد", "callback_data": "statement:reject"},
    ]]}
    if len(caption) <= MAX_CAPTION_LENGTH:
        method = "sendPhoto" if state["media_type"] == "photo" else "sendVideo"
        field = "photo" if state["media_type"] == "photo" else "video"
        tg(method, {"chat_id": chat_id, field: state["file_id"], "caption": caption,
                    "parse_mode": "HTML", "reply_markup": keyboard})
    else:
        method = "sendPhoto" if state["media_type"] == "photo" else "sendVideo"
        field = "photo" if state["media_type"] == "photo" else "video"
        tg(method, {"chat_id": chat_id, field: state["file_id"]})
        send_message(chat_id, f"{heading}\n\n{quote_html(chunks(statement, 3500)[0])}",
                     reply_markup=keyboard)


def handle_statement_text(message: dict) -> bool:
    user_id = message["from"]["id"]
    state = get_state(user_id)
    if not state or state.get("step") != "awaiting_text":
        return False
    statement = str(message.get("text") or "").strip()
    if len(statement) < 45 or len([line for line in statement.splitlines() if line.strip()]) < 4:
        send_message(message["chat"]["id"], TEXT["text_short"])
        return True
    updated = {**state, "step": "awaiting_confirmation", "statement": statement}
    set_state(user_id, updated)
    send_preview(message["chat"]["id"], updated, statement)
    return True


def publish_statement(user_id: int, chat_id: int) -> None:
    state = get_state(user_id)
    if not state or state.get("step") != "awaiting_confirmation":
        send_message(chat_id, "❌ پیش‌نمایش فعالی برای ارسال وجود ندارد. دوباره /start را بزنید.")
        return
    if not CHANNEL_ID:
        send_message(chat_id, "⚠️ هنوز CHANNEL_ID تنظیم نشده است. در تنظیمات Render آیدی یا آیدی عمومی کانال را وارد کنید؛ سپس دوباره تأیید کنید.")
        return
    country = players.get(str(user_id), "بدون کشور")
    heading = channel_heading(country)
    statement = state["statement"]
    full_caption = f"{heading}\n\n{quote_html(statement)}"
    fits = len(full_caption) <= MAX_CAPTION_LENGTH
    media_type = state["media_type"]
    method = "sendPhoto" if media_type == "photo" else "sendVideo"
    field = "photo" if media_type == "photo" else "video"
    tg(method, {
        "chat_id": CHANNEL_ID, field: state["file_id"],
        "caption": full_caption if fits else heading,
        "parse_mode": "HTML",
    })
    if not fits:
        for part in chunks(statement):
            send_message(CHANNEL_ID, quote_html(part))
    clear_state(user_id)
    send_message(chat_id, "✅ بیانیه با موفقیت در کانال منتشر شد.")


def list_players(chat_id: int) -> None:
    if not players:
        send_message(chat_id, "👥 هنوز بازیکنی ثبت نشده است.")
        return
    lines = [
        f'{i}. <code>{esc(uid)}</code> — {country_flag(country)} {esc(country)}'
        for i, (uid, country) in enumerate(players.items(), 1)
    ]
    for part in chunks("👥 <b>فهرست بازیکنان:</b>\n\n" + "\n".join(lines), 3500):
        send_message(chat_id, part)


def handle_admin_text(message: dict, state: dict) -> bool:
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    value = str(message.get("text") or "").strip()
    step = state.get("step")
    if step in ("add_player_id", "remove_player_id", "edit_player_id") and not value.isdigit():
        send_message(chat_id, "❌ آیدی باید عددی باشد. دوباره ارسال کنید یا /cancel بزنید.")
        return True
    if step == "add_player_id":
        set_state(user_id, {"step": "add_country", "player_id": value})
        send_message(chat_id, "🌍 نام کشور بازیکن را ارسال کنید.")
        return True
    if step == "add_country":
        if not value:
            send_message(chat_id, "❌ نام کشور نمی‌تواند خالی باشد.")
            return True
        players[state["player_id"]] = value
        save_json(PLAYERS_FILE, players)
        clear_state(user_id)
        send_message(chat_id, f'✅ بازیکن <code>{esc(state["player_id"])}</code> با کشور {esc(value)} ثبت شد.')
        admin_panel(chat_id)
        return True
    if step == "remove_player_id":
        if value not in players:
            send_message(chat_id, "❌ بازیکنی با این آیدی پیدا نشد.")
            return True
        del players[value]
        save_json(PLAYERS_FILE, players)
        clear_state(user_id)
        send_message(chat_id, f'✅ بازیکن <code>{esc(value)}</code> حذف شد و دیگر به ربات دسترسی ندارد.')
        admin_panel(chat_id)
        return True
    if step == "edit_player_id":
        if value not in players:
            send_message(chat_id, "❌ بازیکنی با این آیدی پیدا نشد.")
            return True
        set_state(user_id, {"step": "edit_country", "player_id": value})
        send_message(chat_id, f'🌍 کشور جدید بازیکن <code>{esc(value)}</code> را ارسال کنید.')
        return True
    if step == "edit_country":
        if not value:
            send_message(chat_id, "❌ نام کشور نمی‌تواند خالی باشد.")
            return True
        players[state["player_id"]] = value
        save_json(PLAYERS_FILE, players)
        clear_state(user_id)
        send_message(chat_id, f'✅ کشور بازیکن <code>{esc(state["player_id"])}</code> به {esc(value)} تغییر کرد.')
        admin_panel(chat_id)
        return True
    return False


def handle_command(message: dict) -> bool:
    text = str(message.get("text") or "").strip()
    command = text.split()[0].split("@")[0].lower() if text else ""
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    if command == "/cancel":
        clear_state(user_id)
        send_message(chat_id, "✅ عملیات لغو شد.")
        if is_admin(user_id):
            admin_panel(chat_id)
        return True
    if command == "/admin":
        clear_state(user_id)
        if not is_admin(user_id):
            send_message(chat_id, TEXT["unauthorized"])
        else:
            admin_panel(chat_id)
        return True
    if command == "/start":
        clear_state(user_id)
        if not is_player(user_id):
            send_message(chat_id, TEXT["unauthorized"])
        elif not settings["enabled"]:
            send_message(chat_id, TEXT["disabled"])
        else:
            main_menu(chat_id, message["from"])
        return True
    return False


def handle_callback(query: dict) -> None:
    user_id = query["from"]["id"]
    chat_id = query.get("message", {}).get("chat", {}).get("id", user_id)
    try:
        tg("answerCallbackQuery", {"callback_query_id": query["id"]})
    except Exception:
        log.exception("answerCallbackQuery failed")
    data = query.get("data", "")

    if data == "statement:start":
        if not is_player(user_id):
            send_message(chat_id, TEXT["unauthorized"])
        elif not settings["enabled"]:
            send_message(chat_id, TEXT["disabled"])
        else:
            start_statement(chat_id, user_id)
        return

    if data in ("statement:confirm", "statement:reject"):
        if not is_player(user_id):
            send_message(chat_id, TEXT["unauthorized"])
        elif not settings["enabled"]:
            send_message(chat_id, TEXT["disabled"])
        elif data == "statement:confirm":
            try:
                publish_statement(user_id, chat_id)
            except Exception:
                log.exception("Publishing statement failed")
                send_message(chat_id, "❌ ارسال بیانیه انجام نشد. دسترسی ربات به کانال و CHANNEL_ID را بررسی کنید.")
        else:
            clear_state(user_id)
            send_message(chat_id, "❌ بیانیه رد شد و ارسال نشد.")
            main_menu(chat_id, query["from"])
        return

    # دکمه پنل برای همه قابل مشاهده است؛ اما تمام عملیات مدیریتی فقط برای مالک اجرا می‌شود.
    if not is_admin(user_id):
        send_message(chat_id, TEXT["unauthorized"])
        return

    if data == "admin:add":
        set_state(user_id, {"step": "add_player_id"})
        send_message(chat_id, "🆔 آیدی عددی بازیکن را ارسال کنید.")
    elif data == "admin:remove":
        set_state(user_id, {"step": "remove_player_id"})
        send_message(chat_id, "🆔 آیدی عددی بازیکنی که می‌خواهید حذف شود را ارسال کنید.")
    elif data == "admin:list":
        list_players(chat_id)
        admin_panel(chat_id)
    elif data == "admin:edit":
        set_state(user_id, {"step": "edit_player_id"})
        send_message(chat_id, "🆔 آیدی عددی بازیکن را ارسال کنید.")
    elif data == "admin:toggle":
        settings["enabled"] = not settings["enabled"]
        save_json(SETTINGS_FILE, settings)
        send_message(chat_id, "✅ ربات روشن شد." if settings["enabled"] else TEXT["disabled"])
        admin_panel(chat_id)


def process_update(update: dict) -> None:
    if update.get("callback_query"):
        handle_callback(update["callback_query"])
        return
    message = update.get("message")
    if not message or not message.get("from"):
        return
    if handle_command(message):
        return
    user_id = message["from"]["id"]
    if is_admin(user_id):
        state = get_state(user_id)
        if state and handle_admin_text(message, state):
            return
    if not is_player(user_id):
        send_message(message["chat"]["id"], TEXT["unauthorized"])
        return
    if not settings["enabled"]:
        send_message(message["chat"]["id"], TEXT["disabled"])
        return
    if handle_media(message):
        return
    if handle_statement_text(message):
        return
    if message.get("text"):
        send_message(message["chat"]["id"], "⚡️ از دکمه‌های ربات استفاده کنید یا /start را بزنید.")


def configure_menu() -> None:
    # عمداً دستورات برای همه کاربران تعریف می‌شوند؛ /admin فقط با کنترل دسترسی اجرا می‌شود.
    tg("setMyCommands", {"commands": [
        {"command": "start", "description": "شروع ربات"},
        {"command": "admin", "description": "پنل مدیریت"},
        {"command": "cancel", "description": "لغو عملیات جاری"},
    ]})
    tg("setChatMenuButton", {"menu_button": {"type": "commands"}})


def polling_loop() -> None:
    offset = 0
    while True:
        try:
            updates = tg("getUpdates", {
                "offset": offset,
                "timeout": POLL_TIMEOUT,
                "allowed_updates": ["message", "callback_query"],
            })
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    process_update(update)
                except Exception:
                    log.exception("خطا هنگام پردازش update=%s", update.get("update_id"))
        except Exception:
            log.exception("خطای دریافت آپدیت؛ ۵ ثانیه دیگر تلاش می‌شود.")
            time.sleep(5)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return
        body = json.dumps({
            "status": "ok",
            "service": "telegram-bot",
            "bot_enabled": bool(settings.get("enabled", True)),
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        log.info("HTTP %s - %s", self.address_string(), format % args)


def run() -> None:
    ensure_data()
    tg("deleteWebhook", {"drop_pending_updates": False})
    configure_menu()
    me = tg("getMe")
    log.info("ربات @%s اجرا شد | %s بازیکن | enabled=%s", me.get("username"), len(players), settings["enabled"])
    if not CHANNEL_ID:
        log.warning("CHANNEL_ID تنظیم نشده؛ ربات اجرا می‌شود اما انتشار در کانال تا زمان تنظیم آن غیرفعال است.")

    worker = threading.Thread(target=polling_loop, name="telegram-polling", daemon=True)
    worker.start()

    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("HTTP health server listening on 0.0.0.0:%s", port)
    server.serve_forever()


if __name__ == "__main__":
    run()
