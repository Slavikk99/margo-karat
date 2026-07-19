# -*- coding: utf-8 -*-
"""Клиентский бот Margo Karat — диалог, услуги, сбор данных, оплата."""
import logging

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, ReplyKeyboardRemove)
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

import os

import db
from config import PAYMENT_DETAILS, PRICES, PAYMENT_QR_FILE

log = logging.getLogger("oracle.client")

MAIN_FIVE = ["Таро", "Каббала", "Натальная карта", "Кофейная гуща", "Линии руки"]
ALL_SIX = MAIN_FIVE + ["Задать вопрос"]

# ключи для context.user_data
D = "draft"          # черновик заказа
STEP = "step"        # текущий шаг сбора данных


# ---------------- /start ----------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "")
    ctx.user_data.clear()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔮 Один вопрос — {PRICES['1 вопрос']}", callback_data="pkg|1 вопрос")],
        [InlineKeyboardButton(f"✨ Три направления — {PRICES['3 направления']}", callback_data="pkg|3 направления")],
        [InlineKeyboardButton(f"🌙 Полный пакет (5) — {PRICES['Полный пакет']}", callback_data="pkg|Полный пакет")],
        [InlineKeyboardButton("📜 Мои прошлые расклады", callback_data="history")],
        [InlineKeyboardButton("ℹ️ О Маргарите", callback_data="about")],
    ])
    await update.message.reply_text(
        "Здравствуйте. Вы находитесь в пространстве *Margo Karat*.\n\n"
        "Я помогу оформить персональный расклад от Маргариты — тарот, натальная карта, "
        "каббала, кофейная гуща, линии руки.\n\nВыберите интересующую услугу.",
        reply_markup=kb, parse_mode="Markdown")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — начать и выбрать услугу\n"
        "/history — мои прошлые расклады\n\n"
        "Оформление проходит по шагам: услуга → направления → ваши данные → оплата.")


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = db.user_orders(update.effective_user.id)
    if not rows:
        await update.message.reply_text("У вас пока нет заказов. Нажмите /start, чтобы оформить первый расклад.")
        return
    st_names = {"WAITING_PAYMENT": "💳 ожидает оплаты", "PAYMENT_CHECK": "💳 проверка оплаты",
                "APPROVED": "✨ создаётся", "AI_PROCESSING": "✨ создаётся",
                "WAITING_ADMIN": "🌙 проверка Маргаритой", "SENT": "📜 отправлен", "COMPLETED": "📜 готов",
                "REJECTED": "❌ отклонён", "NEW": "🔮 оформление"}
    lines = []
    for o in rows:
        d = ", ".join(o.get("directions") or [])
        lines.append(f"№{o.get('order_no','?')} · {o.get('package','')} ({d}) — {st_names.get(o['status'], o['status'])}")
    await update.message.reply_text("📜 Ваши заказы:\n\n" + "\n".join(lines))


# ---------------- about / history ----------------
async def on_about(q, ctx):
    await q.message.reply_text(
        "Маргарита — практикующий таролог и консультант с более чем двадцатилетним опытом. "
        "Таро, натальные карты, каббала, символические практики. Каждый расклад создаётся лично "
        "для вас и проходит проверку перед отправкой. ✨")


async def on_history(q, ctx):
    rows = db.user_orders(q.from_user.id)
    if not rows:
        await q.message.reply_text("У вас пока нет заказов. Нажмите /start, чтобы оформить первый расклад.")
        return
    lines = []
    st_names = {"WAITING_PAYMENT": "💳 ожидает оплаты", "PAYMENT_CHECK": "💳 проверка оплаты",
                "APPROVED": "✨ создаётся", "AI_PROCESSING": "✨ создаётся",
                "WAITING_ADMIN": "🌙 проверка Маргаритой", "SENT": "📜 отправлен", "COMPLETED": "📜 готов",
                "REJECTED": "❌ отклонён", "NEW": "🔮 оформление"}
    for o in rows:
        d = ", ".join(o.get("directions") or [])
        lines.append(f"№{o.get('order_no','?')} · {o.get('package','')} ({d}) — {st_names.get(o['status'], o['status'])}")
    await q.message.reply_text("📜 Ваши заказы:\n\n" + "\n".join(lines))


# ---------------- выбор пакета ----------------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "about":
        return await on_about(q, ctx)
    if data == "history":
        return await on_history(q, ctx)

    if data.startswith("pkg|"):
        pkg = data.split("|", 1)[1]
        ctx.user_data[D] = {"package": pkg, "directions": [], "photos": []}
        if pkg == "Полный пакет":
            ctx.user_data[D]["directions"] = MAIN_FIVE[:]
            return await _start_collect(q, ctx)
        return await _show_directions(q, ctx)

    if data.startswith("dir|"):
        return await _toggle_direction(q, ctx, data.split("|", 1)[1])

    if data == "dir_done":
        return await _start_collect(q, ctx)

    if data.startswith("coffee|"):
        ctx.user_data[D]["coffee_mode"] = data.split("|", 1)[1]
        await q.message.reply_text(
            "Принято." + (" Пришлите фотографию кофейной чашки одним сообщением."
                          if ctx.user_data[D]["coffee_mode"] == "upload"
                          else " Маргарита приготовит символическую чашку сама."))
        if ctx.user_data[D]["coffee_mode"] == "margo_draws":
            await _ask_next_photo_or_finish(q.message, ctx)
        return

    if data == "paid":
        return await _on_paid(q, ctx)


def _dir_keyboard(chosen, limit):
    rows = []
    pool = ALL_SIX if limit == 1 else MAIN_FIVE
    for d in pool:
        mark = "✅ " if d in chosen else ""
        rows.append([InlineKeyboardButton(f"{mark}{d}", callback_data=f"dir|{d}")])
    if (limit == 1 and len(chosen) == 1) or (limit == 3 and len(chosen) == 3):
        rows.append([InlineKeyboardButton("➡️ Продолжить", callback_data="dir_done")])
    return InlineKeyboardMarkup(rows)


async def _show_directions(q, ctx):
    d = ctx.user_data[D]
    limit = 1 if d["package"] == "1 вопрос" else 3
    txt = ("Выберите одно направление:" if limit == 1
           else f"Выберите три направления (выбрано: {len(d['directions'])}/3):")
    await q.message.reply_text(txt, reply_markup=_dir_keyboard(d["directions"], limit))


async def _toggle_direction(q, ctx, d_name):
    d = ctx.user_data[D]
    limit = 1 if d["package"] == "1 вопрос" else 3
    ch = d["directions"]
    if d_name in ch:
        ch.remove(d_name)
    elif limit == 1:
        ch[:] = [d_name]
    elif len(ch) < 3:
        ch.append(d_name)
    txt = ("Выберите одно направление:" if limit == 1
           else f"Выберите три направления (выбрано: {len(ch)}/3):")
    await q.edit_message_text(txt, reply_markup=_dir_keyboard(ch, limit))


# ---------------- сбор данных ----------------
FIELDS = [
    ("customer_name", "Как вас зовут? (имя)"),
    ("customer_surname", "Ваша фамилия?"),
    ("phone", "Ваш номер телефона?"),
    ("birth_date", "Дата рождения? (например, 14.03.1990)"),
]
NATAL_FIELDS = [
    ("birth_city", "Город рождения?"),
    ("birth_time", "Время рождения? (например, 14:30, или «не знаю»)"),
]


async def _start_collect(q, ctx):
    ctx.user_data[STEP] = 0
    ctx.user_data["_fields"] = FIELDS[:]
    if "Натальная карта" in ctx.user_data[D]["directions"]:
        ctx.user_data["_fields"] += NATAL_FIELDS
    ctx.user_data["_fields"] += [("question", "Что вас сейчас волнует больше всего? Опишите ваш вопрос.")]
    await q.message.reply_text(ctx.user_data["_fields"][0][1], reply_markup=ReplyKeyboardRemove())


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if D not in ctx.user_data or "_fields" not in ctx.user_data:
        return await cmd_start(update, ctx)
    step = ctx.user_data.get(STEP, 0)
    fields = ctx.user_data["_fields"]
    key, _ = fields[step]
    ctx.user_data[D][key] = update.message.text.strip()
    step += 1
    ctx.user_data[STEP] = step
    if step < len(fields):
        await update.message.reply_text(fields[step][1])
    else:
        await _after_data(update.message, ctx)


async def _after_data(message, ctx):
    """Данные собраны → фото (если нужно) → оплата."""
    d = ctx.user_data[D]
    if "Линии руки" in d["directions"] and not any(
            (p.get("kind") == "palm") for p in d["photos"] if isinstance(p, dict)):
        ctx.user_data["_await_photo"] = "palm"
        await message.reply_text("Отправьте, пожалуйста, фотографию вашей ладони одним сообщением. ✋")
        return
    if "Кофейная гуща" in d["directions"] and "coffee_mode" not in d:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📷 Загрузить фото чашки", callback_data="coffee|upload")],
            [InlineKeyboardButton("🌙 Маргарита создаст сама", callback_data="coffee|margo_draws")],
        ])
        await message.reply_text("Кофейная гуща — выберите вариант:", reply_markup=kb)
        return
    await _show_payment(message, ctx)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if D not in ctx.user_data:
        return
    kind = ctx.user_data.pop("_await_photo", None)
    if not kind:
        # фото кофе после выбора upload
        if ctx.user_data[D].get("coffee_mode") == "upload":
            kind = "coffee"
        else:
            return
    fid = update.message.photo[-1].file_id
    ctx.user_data[D]["photos"].append({"file_id": fid, "kind": kind})
    await update.message.reply_text("Фотография принята. 🙏")
    await _ask_next_photo_or_finish(update.message, ctx)


async def _ask_next_photo_or_finish(message, ctx):
    await _after_data(message, ctx)


async def _show_payment(message, ctx):
    d = ctx.user_data[D]
    price = PRICES.get(d["package"], "")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Я оплатил", callback_data="paid")]])
    dirs = ", ".join(d["directions"])
    caption = f"Ваш заказ:\n*{d['package']}* — {price}\nНаправления: {dirs}\n\n{PAYMENT_DETAILS}"
    # если есть картинка с QR/реквизитами — шлём фото с подписью, иначе текст
    if PAYMENT_QR_FILE and os.path.exists(PAYMENT_QR_FILE):
        with open(PAYMENT_QR_FILE, "rb") as img:
            await message.reply_photo(img, caption=caption, reply_markup=kb, parse_mode="Markdown")
    else:
        await message.reply_text(caption, reply_markup=kb, parse_mode="Markdown")


async def _on_paid(q, ctx):
    d = ctx.user_data.get(D)
    if not d:
        return await q.message.reply_text("Заказ не найден. Нажмите /start.")
    u = q.from_user
    order = db.create_order({
        "telegram_id": u.id, "username": u.username or "", "first_name": u.first_name or "",
        "customer_name": d.get("customer_name"), "customer_surname": d.get("customer_surname"),
        "phone": d.get("phone"), "birth_date": d.get("birth_date"),
        "birth_city": d.get("birth_city"), "birth_time": d.get("birth_time"),
        "package": d["package"], "directions": d["directions"],
        "coffee_mode": d.get("coffee_mode"), "question": d.get("question"),
        "photos": d["photos"], "status": "WAITING_PAYMENT",
    })
    ctx.user_data.clear()
    await q.message.reply_text(
        "Спасибо. Ваша оплата отправлена на проверку. 🌙\n\n"
        "Как только Маргарита подтвердит оплату, начнётся создание вашего персонального расклада. "
        "Вы получите его прямо здесь.")
    # уведомление админу (через отдельный админ-бот)
    notify = ctx.application.bot_data.get("notify_admin_new_order")
    if notify:
        await notify(order)


def build_client_app() -> Application:
    from config import CLIENT_BOT_TOKEN
    app = Application.builder().token(CLIENT_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
