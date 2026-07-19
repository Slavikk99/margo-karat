# -*- coding: utf-8 -*-
"""Клиентский бот Margo Karat — кнопочный интерфейс уровня приложения."""
import io
import logging

try:
    import qrcode
except ImportError:
    qrcode = None

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, ReplyKeyboardRemove, BotCommand)
try:
    from telegram import CopyTextButton
except ImportError:
    CopyTextButton = None

from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters, PicklePersistence)

import db
from config import PAYMENT_DETAILS, PRICES

log = logging.getLogger("oracle.client")

MAIN_FIVE = ["Таро", "Каббала", "Натальная карта", "Кофейная гуща", "Линии руки"]
ALL_SIX = MAIN_FIVE + ["Задать вопрос"]
CONSULT = "Приватная консультация"

CARD_MONO = "4874070017821875"
IBAN_DE = "DE77330700240135590805"
USDT_TRON = "TEACgeadY6kjsNANTn3z5oDfddwn9MVEkZ"

D = "draft"
STEP = "step"

# постоянная нижняя клавиатура (главное меню)
MENU_NEW = "🔮 Новый расклад"
MENU_ORDERS = "📦 Мои заказы"
MENU_STATUS = "🕐 Статус заказа"
MENU_REVIEW = "⭐ Оставить отзыв"
MENU_CONTACT = "👤 Связаться с Маргаритой"
MENU_LABELS = {MENU_NEW, MENU_ORDERS, MENU_STATUS, MENU_REVIEW, MENU_CONTACT}

MENU_KB = ReplyKeyboardMarkup(
    [[MENU_NEW], [MENU_ORDERS, MENU_STATUS], [MENU_REVIEW, MENU_CONTACT]],
    resize_keyboard=True)


def _price_num(pkg):
    try:
        return float(PRICES.get(pkg, "0").split()[0])
    except Exception:
        return 0.0


def _order_total(pkg, add_consult):
    total = _price_num(pkg) + (_price_num(CONSULT) if add_consult and pkg != CONSULT else 0)
    return round(total, 2)


def _welcome():
    return (
        "✨ *Добро пожаловать в Margo Karat!*\n\n"
        "Меня зовут Маргарита. Более двадцати лет я помогаю людям находить ответы — "
        "мягко, честно и по делу. Здесь вы получите персональный разбор именно вашей ситуации.\n\n"
        "*Направления:*\n"
        "🔮 Таро — что происходит и как поступить\n"
        "✡️ Каббала — ваша духовная задача и сильные стороны\n"
        "🌙 Натальная карта — характер, судьба, важные периоды\n"
        "☕ Кофейная гуща — символы вашей чашки и будущее\n"
        "✋ Линии руки — характер и путь по ладони\n"
        "💬 Личный вопрос — прямой ответ на то, что волнует\n\n"
        "Нажмите кнопку ниже, чтобы начать 👇"
    )


def _main_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔮 Начать расклад", callback_data="menu|new")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="menu|orders"),
         InlineKeyboardButton("🕐 Статус заказа", callback_data="menu|status")],
        [InlineKeyboardButton("👑 Приватная консультация", callback_data="pkg|Приватная консультация")],
        [InlineKeyboardButton("👤 Связаться с Маргаритой", callback_data="menu|contact")],
    ])


# ---------------- команды ----------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "")
    ctx.user_data.clear()
    await update.message.reply_text("Меню всегда доступно снизу 👇", reply_markup=MENU_KB)
    await update.message.reply_text(_welcome(), reply_markup=_main_inline(), parse_mode="Markdown")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пользуйтесь кнопками меню снизу. /start — вернуться в начало.",
                                    reply_markup=MENU_KB)


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_history(update.message, update.effective_user.id)


# ---------------- приветственные действия ----------------
async def _open_services(message, ctx):
    ctx.user_data.clear()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔮 Один вопрос — {PRICES['1 вопрос']}", callback_data="pkg|1 вопрос")],
        [InlineKeyboardButton(f"✨ Три направления — {PRICES['3 направления']}", callback_data="pkg|3 направления")],
        [InlineKeyboardButton(f"🌙 Полный пакет (5) — {PRICES['Полный пакет']}", callback_data="pkg|Полный пакет")],
        [InlineKeyboardButton(f"👑 Приватная консультация — {PRICES[CONSULT]}", callback_data="pkg|Приватная консультация")],
    ])
    await message.reply_text("Выберите услугу:", reply_markup=kb)


async def _send_history(message, tg_id):
    rows = db.user_orders(tg_id)
    if not rows:
        await message.reply_text("У вас пока нет заказов. Нажмите «🔮 Новый расклад», чтобы оформить первый.",
                                 reply_markup=MENU_KB)
        return
    blocks = []
    for o in rows:
        code = o.get("order_code") or db.make_order_code(o.get("order_no", 0))
        dirs = ", ".join(o.get("directions") or []) or "—"
        created = (o.get("created_at", "") or "").replace("T", " ")
        date, time = (created[:10], created[11:16]) if len(created) >= 16 else (created, "")
        amount = o.get("amount")
        price = f"{amount:.2f} €" if amount else PRICES.get(o.get("package", ""), "")
        st = db.STATUS_RU.get(o.get("status"), o.get("status"))
        blocks.append(
            f"*{code}*\n📅 {date}   🕐 {time}\n"
            f"Услуга: {o.get('package','')}{' + консультация' if o.get('add_consult') else ''}\n"
            f"Направления: {dirs}\nСтоимость: {price}\nСтатус: {st}")
    await message.reply_text("📦 *Ваши заказы:*\n\n" + "\n\n———\n\n".join(blocks),
                             parse_mode="Markdown", reply_markup=MENU_KB)


async def _show_status(message, tg_id):
    act = db.active_order(tg_id)
    if not act:
        rows = db.user_orders(tg_id, limit=1)
        if not rows:
            return await message.reply_text("Активных заказов нет. Нажмите «🔮 Новый расклад».", reply_markup=MENU_KB)
        o = rows[0]
        code = o.get("order_code", "")
        return await message.reply_text(
            f"Последний заказ *{code}*\nСтатус: {db.STATUS_RU.get(o['status'], o['status'])}",
            parse_mode="Markdown", reply_markup=MENU_KB)
    await _show_active_order(message, act)


async def _show_active_order(message, o):
    code = o.get("order_code", "")
    st = db.STATUS_RU.get(o["status"], o["status"])
    rows = []
    if o["status"] == "WAITING_PAYMENT":
        rows.append([InlineKeyboardButton("💳 Реквизиты оплаты", callback_data=f"payinfo|{o['id']}")])
        rows.append([InlineKeyboardButton("📤 Загрузить подтверждение", callback_data=f"proof|{o['id']}")])
    rows.append([InlineKeyboardButton("❌ Отменить заказ", callback_data=f"cancel|{o['id']}")])
    await message.reply_text(
        f"Ваш текущий заказ:\n\n*{code}*\nСтатус: {st}",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")


# ---------------- callbacks ----------------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "menu|new" or data == "menu|services":
        return await _open_services(q.message, ctx)
    if data == "menu|orders":
        return await _send_history(q.message, q.from_user.id)
    if data == "menu|status":
        return await _show_status(q.message, q.from_user.id)
    if data == "menu|contact":
        ctx.user_data["support"] = True
        return await q.message.reply_text(
            "Напишите ваше сообщение одним текстом — Маргарита увидит его и ответит здесь. 💬")

    if data.startswith("pkg|"):
        return await _choose_package(q, ctx, data.split("|", 1)[1])
    if data.startswith("dir|"):
        return await _toggle_direction(q, ctx, data.split("|", 1)[1])
    if data == "dir_done":
        return await _offer_consult(q.message, ctx)
    if data.startswith("consult_add|"):
        return await _on_consult_choice(q, ctx, data.split("|", 1)[1])
    if data.startswith("hand|"):
        return await _on_hand(q, ctx, data.split("|", 1)[1])
    if data.startswith("skip|"):
        key = data.split("|", 1)[1]
        if "_fields" not in ctx.user_data:
            return
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return await _advance(q.message, ctx, "не знаю" if key == "birth_time" else "")
    if data == "cancel_new":
        ctx.user_data.clear()
        return await q.message.reply_text("Оформление отменено.", reply_markup=MENU_KB)
    if data == "paid":
        return await _on_paid(q, ctx)
    if data.startswith("payinfo|"):
        return await _resend_payment(q, ctx, data.split("|", 1)[1])
    if data.startswith("proof|"):
        ctx.user_data["_await_payment_shot"] = data.split("|", 1)[1]
        return await q.message.reply_text("Пришлите скриншот перевода одним фото. 📤")
    if data.startswith("cancel|"):
        oid = data.split("|", 1)[1]
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Да, отменить", callback_data=f"cancelyes|{oid}"),
            InlineKeyboardButton("Нет, оставить", callback_data="cancelno")]])
        return await q.message.reply_text("Вы уверены, что хотите отменить заказ?", reply_markup=kb)
    if data.startswith("cancelyes|"):
        oid = data.split("|", 1)[1]
        db.set_order(oid, {"status": "CANCELLED"})
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return await q.message.reply_text("Заказ отменён. Вы можете оформить новый в любой момент. 🌙",
                                          reply_markup=MENU_KB)
    if data == "cancelno":
        return await q.message.reply_text("Хорошо, заказ остаётся активным. 🙏")
    if data.startswith("rate|"):
        return await _on_rate(q, ctx, data)


# ---------------- выбор пакета (с проверкой активного заказа) ----------------
async def _choose_package(q, ctx, pkg):
    act = db.active_order(q.from_user.id)
    if act:
        code = act.get("order_code", "")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🕐 Подождать", callback_data="cancelno")],
            [InlineKeyboardButton("❌ Отменить заказ", callback_data=f"cancel|{act['id']}")],
        ])
        return await q.message.reply_text(
            f"У вас уже есть активный заказ *{code}* — «{db.STATUS_RU.get(act['status'], act['status'])}».\n\n"
            "Можно подождать его завершения или отменить и оформить новый.",
            reply_markup=kb, parse_mode="Markdown")

    ctx.user_data.clear()
    ctx.user_data[D] = {"package": pkg, "directions": [], "photos": [], "add_consult": False}

    if pkg == CONSULT:
        await q.message.reply_text(
            "👑 *Приватная консультация Маргариты* — 49.99 €\n\n"
            "Полная персональная работа один на один: анализ прошлого, настоящего и будущего, "
            "ответы на личные вопросы, рекомендации, возможность заказа личного амулета, "
            "живое общение. После оплаты согласуем удобные дату и время.",
            parse_mode="Markdown")
        return await _start_collect(q.message, ctx)

    if pkg == "Полный пакет":
        ctx.user_data[D]["directions"] = MAIN_FIVE[:]
        return await _offer_consult(q.message, ctx)

    return await _show_directions(q.message, ctx)


def _dir_keyboard(chosen, limit):
    pool = ALL_SIX if limit == 1 else MAIN_FIVE
    rows = [[InlineKeyboardButton(("✅ " if d in chosen else "") + d, callback_data=f"dir|{d}")] for d in pool]
    if (limit == 1 and len(chosen) == 1) or (limit == 3 and len(chosen) == 3):
        rows.append([InlineKeyboardButton("➡️ Продолжить", callback_data="dir_done")])
    rows.append([InlineKeyboardButton("❌ Отменить", callback_data="cancel_new")])
    return InlineKeyboardMarkup(rows)


async def _show_directions(message, ctx):
    d = ctx.user_data[D]
    limit = 1 if d["package"] == "1 вопрос" else 3
    txt = ("Выберите одно направление:" if limit == 1
           else f"Выберите три направления (выбрано: {len(d['directions'])}/3):")
    await message.reply_text(txt, reply_markup=_dir_keyboard(d["directions"], limit))


async def _toggle_direction(q, ctx, name):
    d = ctx.user_data[D]
    limit = 1 if d["package"] == "1 вопрос" else 3
    ch = d["directions"]
    if name in ch:
        ch.remove(name)
    elif limit == 1:
        ch[:] = [name]
    elif len(ch) < 3:
        ch.append(name)
    txt = ("Выберите одно направление:" if limit == 1
           else f"Выберите три направления (выбрано: {len(ch)}/3):")
    await q.edit_message_text(txt, reply_markup=_dir_keyboard(ch, limit))


# ---------------- допродажа консультации к пакету ----------------
async def _offer_consult(message, ctx):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить консультацию (+49.99 €)", callback_data="consult_add|yes")],
        [InlineKeyboardButton("Продолжить без неё", callback_data="consult_add|no")],
    ])
    await message.reply_text(
        "Хотите добавить к заказу *приватную консультацию* Маргариты (живое общение один на один, +49.99 €)?",
        reply_markup=kb, parse_mode="Markdown")


async def _on_consult_choice(q, ctx, choice):
    ctx.user_data[D]["add_consult"] = (choice == "yes")
    await _start_collect(q.message, ctx)


# ---------------- сбор данных ----------------
BASE_FIELDS = [
    ("customer_name", "Как вас зовут? (имя)"),
    ("customer_surname", "Ваша фамилия?"),
    ("phone", "Ваш номер телефона? (с кодом страны, например +49…)"),
    ("birth_date", "Дата рождения? (например, 14.03.1990)"),
]
NATAL_FIELDS = [
    ("birth_city", "Город рождения?"),
    ("birth_country", "Страна рождения?"),
    ("birth_time", "Точное время рождения? (например, 14:30)"),
]
QUESTION_FIELD = ("question", "Дополните свой вопрос или задайте свой вопрос.")


def _field_kb(key):
    rows = []
    if key == "birth_time":
        rows.append([InlineKeyboardButton("Не знаю время рождения", callback_data="skip|birth_time")])
    if key == "question":
        rows.append([InlineKeyboardButton("➡️ Продолжить без вопроса", callback_data="skip|question")])
    rows.append([InlineKeyboardButton("❌ Отменить", callback_data="cancel_new")])
    return InlineKeyboardMarkup(rows)


async def _ask_field(message, ctx):
    step = ctx.user_data[STEP]
    fields = ctx.user_data["_fields"]
    key, prompt = fields[step]
    await message.reply_text(f"Шаг {step+1} из {len(fields)}:\n\n{prompt}", reply_markup=_field_kb(key))


async def _advance(message, ctx, value):
    step = ctx.user_data.get(STEP, 0)
    fields = ctx.user_data["_fields"]
    ctx.user_data[D][fields[step][0]] = value
    step += 1
    ctx.user_data[STEP] = step
    if step < len(fields):
        await _ask_field(message, ctx)
    else:
        ctx.user_data.pop("_fields", None)
        await _after_data(message, ctx)


async def _start_collect(message, ctx):
    ctx.user_data[STEP] = 0
    fields = BASE_FIELDS[:]
    if "Натальная карта" in ctx.user_data[D]["directions"]:
        fields += NATAL_FIELDS
    fields += [QUESTION_FIELD]
    ctx.user_data["_fields"] = fields
    await message.reply_text("Заполним короткую анкету по шагам. В любой момент можно нажать «Отменить».")
    await _ask_field(message, ctx)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    # поддержка / связь с Маргаритой
    if ctx.user_data.get("support"):
        ctx.user_data.pop("support")
        db.log_message(update.effective_user.id, "client", text)
        return await update.message.reply_text(
            "Спасибо, я передала ваше сообщение Маргарите. Она ответит здесь. 🌙", reply_markup=MENU_KB)

    # кнопки нижнего меню
    if text in MENU_LABELS and "_fields" not in ctx.user_data:
        if text == MENU_NEW:
            return await _open_services(update.message, ctx)
        if text == MENU_ORDERS:
            return await _send_history(update.message, update.effective_user.id)
        if text == MENU_STATUS:
            return await _show_status(update.message, update.effective_user.id)
        if text == MENU_REVIEW:
            return await update.message.reply_text(
                "Оставить отзыв можно после получения расклада — я сама предложу оценку. 🙏", reply_markup=MENU_KB)
        if text == MENU_CONTACT:
            ctx.user_data["support"] = True
            return await update.message.reply_text(
                "Напишите ваше сообщение — Маргарита увидит его и ответит здесь. 💬")

    db.log_message(update.effective_user.id, "client", text)

    if D not in ctx.user_data or "_fields" not in ctx.user_data:
        return await cmd_start(update, ctx)

    await update.message.reply_text("✅ Принято.")
    await _advance(update.message, ctx, text.strip())


# ---------------- фото ----------------
async def _after_data(message, ctx):
    d = ctx.user_data[D]
    if "Линии руки" in d["directions"] and not d.get("hand_side"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤚 Левая рука", callback_data="hand|Левая")],
            [InlineKeyboardButton("✋ Правая рука", callback_data="hand|Правая")]])
        await message.reply_text(
            "В хиромантии каждая рука имеет своё значение:\n\n"
            "✋ *Активная рука* — обычно правая у правшей и левая у левшей. "
            "Показывает текущий жизненный путь, изменения и то, как человек реализует свой потенциал.\n\n"
            "✋ *Пассивная рука* — обычно левая у правшей и правая у левшей. "
            "Показывает врождённые качества, характер и заложенный потенциал.\n\n"
            "Какую руку сфотографируем?",
            reply_markup=kb, parse_mode="Markdown")
        return
    if "Линии руки" in d["directions"] and not _has_photo(d, "palm"):
        ctx.user_data["_await_photo"] = "palm"
        await message.reply_text("Отправьте чёткое фото вашей ладони одним сообщением. ✋")
        return
    if "Кофейная гуща" in d["directions"] and not _has_photo(d, "coffee"):
        ctx.user_data["_await_photo"] = "coffee"
        await message.reply_text(
            "☕ *Гадание по кофейной гуще*\n\n"
            "Используйте натуральный кофе (не растворимый). Когда выпьете:\n"
            "1. Возьмите чашку левой рукой.\n2. Переверните от себя на блюдце.\n"
            "3. Подождите минуту.\n4. Сфотографируйте рисунок внутри чашки.\n5. Загрузите фото сюда.",
            parse_mode="Markdown")
        return
    await _show_payment(message, ctx)


def _has_photo(d, kind):
    return any(isinstance(p, dict) and p.get("kind") == kind for p in d.get("photos", []))


async def _on_hand(q, ctx, side):
    ctx.user_data[D]["hand_side"] = side
    ctx.user_data["_await_photo"] = "palm"
    await q.message.reply_text(f"Выбрана {side.lower()} рука. Теперь отправьте её фото. ✋")


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("_await_payment_shot"):
        return await _on_payment_screenshot(update, ctx)
    if D not in ctx.user_data:
        return
    kind = ctx.user_data.pop("_await_photo", None)
    if not kind:
        return
    ctx.user_data[D]["photos"].append({"file_id": update.message.photo[-1].file_id, "kind": kind})
    await update.message.reply_text("Фотография принята. 🙏")
    await _after_data(update.message, ctx)


# ---------------- оплата ----------------
def _qr_photo(data):
    if not qrcode:
        return None
    img = qrcode.make(data)
    bio = io.BytesIO(); img.save(bio, format="PNG"); bio.seek(0)
    return bio


def _copy_btn(label, value):
    if CopyTextButton:
        return InlineKeyboardButton(label, copy_text=CopyTextButton(text=value))
    return InlineKeyboardButton(label, callback_data="noop")


def _pay_markup():
    return InlineKeyboardMarkup([
        [_copy_btn("📋 Копировать карту Monobank", CARD_MONO)],
        [_copy_btn("📋 Копировать IBAN (Deutsche Bank)", IBAN_DE)],
        [_copy_btn("📋 Копировать USDT (TRON)", USDT_TRON)],
        [InlineKeyboardButton("✅ Я оплатил", callback_data="paid")],
    ])


async def _show_payment(message, ctx):
    d = ctx.user_data[D]
    total = _order_total(d["package"], d.get("add_consult"))
    dirs = ", ".join(d["directions"]) if d["directions"] else d["package"]
    extra = " + приватная консультация" if d.get("add_consult") else ""
    if qrcode:
        try:
            await message.reply_photo(_qr_photo(CARD_MONO), caption="QR — карта Monobank")
            await message.reply_photo(_qr_photo(USDT_TRON), caption="QR — USDT (TRON)")
        except Exception:
            pass
    await message.reply_text(
        f"Ваш заказ: *{d['package']}{extra}*\nНаправления: {dirs}\nК оплате: *{total:.2f} €*\n\n"
        f"{PAYMENT_DETAILS}\n\nСкопируйте реквизиты кнопкой или отсканируйте QR выше.",
        reply_markup=_pay_markup(), parse_mode="Markdown")


async def _resend_payment(q, ctx, oid):
    o = db.get_order(oid)
    if not o:
        return
    total = o.get("amount") or _order_total(o.get("package"), o.get("add_consult"))
    await q.message.reply_text(f"Реквизиты по заказу {o.get('order_code','')} (к оплате {total:.2f} €):\n\n{PAYMENT_DETAILS}",
                               reply_markup=_pay_markup(), parse_mode="Markdown")


async def _on_paid(q, ctx):
    d = ctx.user_data.get(D)
    if not d:
        return await q.message.reply_text("Заказ не найден. Нажмите «🔮 Новый расклад».", reply_markup=MENU_KB)
    if ctx.user_data.get("_creating"):
        return
    ctx.user_data["_creating"] = True
    try:
        if db.active_order(q.from_user.id):
            return await q.message.reply_text("У вас уже есть активный заказ в обработке. 🙏")
        if db.recent_duplicate(q.from_user.id, d["package"]):
            return await q.message.reply_text("Такой заказ уже создан минуту назад и в обработке. 🙏")
        u = q.from_user
        total = _order_total(d["package"], d.get("add_consult"))
        try:
            order = db.create_order({
                "telegram_id": u.id, "username": u.username or "", "first_name": u.first_name or "",
                "customer_name": d.get("customer_name"), "customer_surname": d.get("customer_surname"),
                "phone": d.get("phone"), "birth_date": d.get("birth_date"),
                "birth_city": d.get("birth_city"), "birth_country": d.get("birth_country"),
                "birth_time": d.get("birth_time"),
                "package": d["package"], "directions": d["directions"],
                "hand_side": d.get("hand_side"), "question": d.get("question"),
                "photos": d["photos"], "add_consult": d.get("add_consult", False),
                "amount": total, "status": "WAITING_PAYMENT",
            })
            code = db.make_order_code(order.get("order_no", 0))
            db.set_order(order["id"], {"order_code": code})
        except Exception:
            log.exception("Не удалось создать заказ для %s", u.id)
            return await q.message.reply_text("Ошибка при создании заказа. Попробуйте ещё раз через минуту.")
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        ctx.user_data["_await_payment_shot"] = order["id"]
        await q.message.reply_text(
            f"Заказ *{code}* создан. 📎\n\nТеперь пришлите *скриншот перевода* — "
            "без него заявка не уйдёт на проверку.", parse_mode="Markdown")
    finally:
        ctx.user_data["_creating"] = False


async def _on_payment_screenshot(update, ctx):
    order_id = ctx.user_data.get("_await_payment_shot")
    if not order_id:
        return
    order = db.get_order(order_id)
    if order and order.get("payment_screenshot"):
        return await update.message.reply_text("Подтверждение уже загружено и ожидает проверки. 🙏")
    fid = update.message.photo[-1].file_id
    db.set_order(order_id, {"payment_screenshot": fid, "status": "PAYMENT_CHECK"})
    ctx.user_data.pop("_await_payment_shot", None)
    order = db.get_order(order_id)
    await update.message.reply_text(
        f"Спасибо! 🌙 Скриншот получен, заказ *{order.get('order_code','')}* отправлен на проверку.\n\n"
        "Как только Маргарита подтвердит оплату, начнётся создание вашего разбора.",
        parse_mode="Markdown", reply_markup=MENU_KB)
    ctx.user_data.clear()
    notify = ctx.application.bot_data.get("notify_admin_new_order")
    if notify:
        try:
            await notify(order)
        except Exception:
            log.exception("Не удалось уведомить админа о заказе %s", order_id)


# ---------------- отзыв ----------------
async def _on_rate(q, ctx, data):
    _, order_id, stars = data.split("|")
    db.set_order(order_id, {"rating": int(stars)})
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "👑 Записаться на приватную консультацию — 49.99 €", callback_data="pkg|Приватная консультация")]])
    await q.message.reply_text(
        f"Спасибо за вашу оценку ({stars}⭐)! Это очень ценно. 🙏\n\n"
        "Если хотите более глубокий разбор — можно записаться на приватную консультацию Маргариты.",
        reply_markup=kb)


async def setup_commands(app):
    await app.bot.set_my_commands([
        BotCommand("start", "🔮 Главное меню"),
        BotCommand("history", "📦 Мои заказы"),
        BotCommand("help", "❓ Помощь"),
    ])


def build_client_app() -> Application:
    from config import CLIENT_BOT_TOKEN
    persistence = PicklePersistence(filepath="client_state.pickle")
    app = (Application.builder().token(CLIENT_BOT_TOKEN)
           .persistence(persistence).post_init(setup_commands).build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
