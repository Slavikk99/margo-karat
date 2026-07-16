# -*- coding: utf-8 -*-
"""
MARGO KARAT — Telegram-бот бесплатного вопроса.

Что делает:
1. Показывает ссылки на Instagram / Facebook / TikTok.
2. Пользователь подтверждает подписки и присылает свой ник в Instagram
   (автоматическая проверка подписок IG/FB/TT технически невозможна —
   официальные API этого не дают, поэтому используется безопасная
   альтернатива: ник сохраняется, Маргарита может выборочно проверить).
3. Бот выдаёт одноразовый код MARGO-FREE-XXXX (действует 30 минут).
4. Пользователь вводит код на сайте и получает бесплатный вопрос.

Админ (ADMIN_CHAT_ID) получает уведомление о каждом выданном коде.

Интеграция с сайтом: сейчас коды хранятся в codes.json.
При подключении Supabase бот пишет коды в таблицу free_codes,
а сайт проверяет их там же (см. TODO ниже).

Запуск: python margo_bot.py  (ключи — в файле .env)
"""

import os
import json
import random
import string
import logging
import datetime as dt
from pathlib import Path

import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

TOKEN = os.getenv("MARGO_BOT_TOKEN", os.getenv("TELEGRAM_TOKEN", "")).strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()  # chat_id Маргариты/админа
CODE_TTL_MIN = int(os.getenv("CODE_TTL_MIN", "30"))     # срок жизни кода, минут
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

INSTAGRAM_URL = "https://www.instagram.com/margo_karat?igsh=MTdvd2s3NDl0NW1pNQ=="
FACEBOOK_URL = "https://www.facebook.com/share/1F5uHBZUhp/?mibextid=wwXIfr"
TIKTOK_URL = "https://www.tiktok.com/@margo.karat.official.777?_r=1&_t=ZS-97tkhglZ1iH"

BASE_DIR = Path(__file__).resolve().parent
CODES_FILE = BASE_DIR / "codes.json"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("margo-bot")

# Состояния диалога
WAITING_IG = "waiting_ig"          # ждём ник Instagram
WAITING_SHOT = "waiting_shot"      # ждём скриншоты подписок


# ---------------------------------------------------------------------------
# Хранилище кодов (codes.json; TODO: заменить на Supabase, таблица free_codes)
# ---------------------------------------------------------------------------
def load_codes() -> dict:
    if CODES_FILE.exists():
        try:
            return json.loads(CODES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_codes(codes: dict) -> None:
    CODES_FILE.write_text(json.dumps(codes, ensure_ascii=False, indent=2), encoding="utf-8")


def new_code(user_id: int, username: str, ig_nick: str) -> str:
    """Генерирует уникальный одноразовый код margo-free-xxxxxxxx-xxxx."""
    codes = load_codes()
    alphabet = string.ascii_lowercase + string.digits
    while True:
        code = (f"margo-free-{''.join(random.choices(alphabet, k=8))}"
                f"-{''.join(random.choices(alphabet, k=4))}")
        if code not in codes:
            break
    codes[code] = {
        "user_id": user_id,
        "tg_username": username,
        "ig_nick": ig_nick,
        "created": dt.datetime.now().isoformat(),
        "used": False,
    }
    save_codes(codes)
    push_code_to_supabase(code, user_id, username, ig_nick)
    return code


def push_code_to_supabase(code: str, user_id: int, username: str, ig_nick: str):
    """Пишет код в Supabase (таблица free_codes) — сайт проверяет его там.
    Если Supabase ещё не настроен — код живёт только в codes.json."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/free_codes",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "code": code,
                "tg_user_id": user_id,
                "tg_username": username,
                "ig_nick": ig_nick,
                "expires_at": (dt.datetime.utcnow()
                               + dt.timedelta(minutes=CODE_TTL_MIN)).isoformat(),
            },
            timeout=15,
        )
    except Exception:
        log.exception("Не удалось записать код в Supabase")


def user_active_code(user_id: int) -> str | None:
    """Есть ли у пользователя действующий (не истёкший, не использованный) код."""
    now = dt.datetime.now()
    for code, info in load_codes().items():
        if info["user_id"] != user_id or info.get("used"):
            continue
        created = dt.datetime.fromisoformat(info["created"])
        if now - created < dt.timedelta(minutes=CODE_TTL_MIN):
            return code
    return None


# ---------------------------------------------------------------------------
# Команды и сценарий
# ---------------------------------------------------------------------------
def subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Instagram", url=INSTAGRAM_URL)],
        [InlineKeyboardButton("👥 Facebook", url=FACEBOOK_URL)],
        [InlineKeyboardButton("🎵 TikTok", url=TIKTOK_URL)],
        [InlineKeyboardButton("✅ Я подписался(-ась)", callback_data="subscribed")],
    ])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(WAITING_IG, None)
    await update.message.reply_text(
        "✨ Добро пожаловать в MARGO KARAT.\n\n"
        "Здесь вы можете получить один бесплатный вопрос — "
        "персональный разбор на основе Таро и вашей натальной карты.\n\n"
        "Условие простое: подпишитесь на наши страницы 👇\n"
        "Затем нажмите «Я подписался(-ась)».",
        reply_markup=subscribe_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "/start — получить бесплатный вопрос\n"
        "/code — показать мой действующий код\n\n"
        "Как это работает: подписка на Instagram, Facebook и TikTok → "
        "подтверждение → одноразовый код → введите его на сайте MARGO KARAT, "
        "и откроется форма бесплатного вопроса. Код действует "
        f"{CODE_TTL_MIN} минут."
    )


async def cmd_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = user_active_code(update.effective_user.id)
    if code:
        await update.message.reply_text(
            f"Ваш действующий код: `{code}`\nВведите его на сайте MARGO KARAT.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "Действующего кода нет. Нажмите /start, чтобы получить новый."
        )


async def on_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка «Я подписался(-ась)»."""
    q = update.callback_query
    await q.answer()

    # уже есть живой код — не плодим новые
    existing = user_active_code(q.from_user.id)
    if existing:
        await q.message.reply_text(
            f"У вас уже есть действующий код: `{existing}`\n"
            "Введите его на сайте MARGO KARAT.",
            parse_mode="Markdown",
        )
        return

    context.user_data[WAITING_IG] = True
    await q.message.reply_text(
        "Прекрасно! 🌙\n\n"
        "Для подтверждения пришлите, пожалуйста, ваш ник в Instagram "
        "(например: @moy_nik) — тот, с которого вы подписались."
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    if context.user_data.get(WAITING_IG):
        ig_nick = msg.text.strip()
        if not (1 < len(ig_nick) <= 40):
            await msg.reply_text("Пришлите просто ник, например: @moy_nik")
            return
        context.user_data.pop(WAITING_IG, None)
        context.user_data[WAITING_SHOT] = ig_nick
        await msg.reply_text(
            "Отлично! 📸 Теперь пришлите скриншот подписки "
            "(достаточно одного — например, страница Instagram, где видна кнопка «Вы подписаны»).\n\n"
            "Это подтверждение для Маргариты."
        )
        return

    if context.user_data.get(WAITING_SHOT):
        await msg.reply_text("Пришлите, пожалуйста, скриншот (фото), а не текст 📸")
        return

    # любой другой текст
    await msg.reply_text(
        "Я бот бесплатного вопроса MARGO KARAT ✨\n"
        "Нажмите /start, чтобы начать, или /help — как это работает."
    )


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скриншот подписки → пересылаем админу → выдаём код."""
    msg = update.message
    ig_nick = context.user_data.get(WAITING_SHOT)
    if not ig_nick:
        await msg.reply_text("Сначала нажмите /start и пройдите шаги подписки ✨")
        return
    context.user_data.pop(WAITING_SHOT, None)

    user = update.effective_user
    code = new_code(user.id, user.username or "", ig_nick)

    await msg.reply_text(
        "🔮 Благодарим за подписку!\n\n"
        f"Ваш персональный код:\n`{code}`\n\n"
        f"⏳ Он действует {CODE_TTL_MIN} минут и работает один раз.\n"
        "Вернитесь на сайт MARGO KARAT, введите код в разделе "
        "«Бесплатный вопрос» — и форма откроется.",
        parse_mode="Markdown",
    )

    if ADMIN_CHAT_ID:
        try:
            await context.bot.forward_message(
                chat_id=int(ADMIN_CHAT_ID),
                from_chat_id=msg.chat_id,
                message_id=msg.message_id,
            )
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=(
                    "🆕 Выдан код бесплатного вопроса (скриншот выше)\n"
                    f"Код: {code}\n"
                    f"Telegram: @{user.username or '—'} (id {user.id})\n"
                    f"Instagram: {ig_nick}\n"
                    f"Время: {dt.datetime.now().strftime('%d.%m.%Y %H:%M')}"
                ),
            )
        except Exception:
            log.exception("Не удалось уведомить админа")


# ---------------------------------------------------------------------------
# Старт
# ---------------------------------------------------------------------------
async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    log.info("Бот MARGO KARAT запущен как @%s", me.username)


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "❌ Не найден токен. Создайте .env и добавьте строку:\n"
            "MARGO_BOT_TOKEN=токен_от_BotFather"
        )
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("code", cmd_code))
    app.add_handler(CallbackQueryHandler(on_subscribed, pattern="^subscribed$"))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Запускаю polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
