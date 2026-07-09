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

INSTAGRAM_URL = "https://www.instagram.com/margo_karat?igsh=MTdvd2s3NDl0NW1pNQ=="
FACEBOOK_URL = "https://www.facebook.com/share/1F5uHBZUhp/?mibextid=wwXIfr"
TIKTOK_URL = "https://www.tiktok.com/@margo.karat.official.777?_r=1&_t=ZS-97tkhglZ1iH"

BASE_DIR = Path(__file__).resolve().parent
CODES_FILE = BASE_DIR / "codes.json"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("margo-bot")

# Состояние диалога: ждём ли от пользователя ник Instagram
WAITING_IG = "waiting_ig"


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
    """Генерирует уникальный одноразовый код MARGO-FREE-XXXX."""
    codes = load_codes()
    while True:
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        code = f"MARGO-FREE-{suffix}"
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
    return code


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

        user = update.effective_user
        code = new_code(user.id, user.username or "", ig_nick)

        await msg.reply_text(
            "🔮 Благодарим за подписку!\n\n"
            f"Ваш персональный код: `{code}`\n\n"
            f"⏳ Он действует {CODE_TTL_MIN} минут и работает один раз.\n"
            "Вернитесь на сайт MARGO KARAT, введите код в разделе "
            "«Бесплатный вопрос» — и форма откроется.",
            parse_mode="Markdown",
        )

        # уведомляем Маргариту/админа
        if ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=int(ADMIN_CHAT_ID),
                    text=(
                        "🆕 Выдан код бесплатного вопроса\n"
                        f"Код: {code}\n"
                        f"Telegram: @{user.username or '—'} (id {user.id})\n"
                        f"Instagram: {ig_nick}\n"
                        f"Время: {dt.datetime.now().strftime('%d.%m.%Y %H:%M')}"
                    ),
                )
            except Exception:
                log.exception("Не удалось уведомить админа")
        return

    # любой другой текст
    await msg.reply_text(
        "Я бот бесплатного вопроса MARGO KARAT ✨\n"
        "Нажмите /start, чтобы начать, или /help — как это работает."
    )


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Запускаю polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
