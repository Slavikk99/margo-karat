# -*- coding: utf-8 -*-
"""Клиентский бот Margo Karat — диалог, услуги, оплата, скриншот, антидубли."""
import io
import logging

try:
    import qrcode
except ImportError:
    qrcode = None

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardRemove)
try:
    from telegram import CopyTextButton
except ImportError:
    CopyTextButton = None

from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

import db
from config import PAYMENT_DETAILS, PRICES

log = logging.getLogger("oracle.client")

MAIN_FIVE = ["Таро", "Каббала", "Натальная карта", "Кофейная гуща", "Линии руки"]
ALL_SIX = MAIN_FIVE + ["Задать вопрос"]

# Реквизиты (для кнопок копирования и QR)
CARD_MONO = "4874070017821875"
IBAN_DE = "DE77330700240135590805"
USDT_TRON = "TEACgeadY6kjsNANTn3z5oDfddwn9MVEkZ"

D = "draft"
STEP = "step"

# ---------- описания направлений (для приветствия) ----------
DIR_DESC = {
    "Таро": "🔮 Таро — расклад на вашу ситуацию: что происходит и куда двигаться.",
    "Каббала": "✡️ Каббала — духовная задача и сильные стороны по имени и дате рождения.",
    "Натальная карта": "🌙 Натальная карта — характер, судьба и жизненные периоды по дате рождения.",
    "Кофейная гуща": "☕ Кофейная гуща — символы вашей чашки и ближайшее будущее.",
    "Линии руки": "✋ Линии руки — характер и путь по вашей ладони.",
}


def _welcome():
    return (
        "✨ *Здравствуйте! Вы в пространстве Margo Karat.*\n\n"
        "Меня зовут Маргарита. Более двадцати лет я помогаю людям находить ответы — "
        "мягко, честно и по делу. Здесь вы можете получить персональный разбор именно вашей ситуации.\n\n"
        "Что я могу для вас сделать:\n"
        "🔮 *Таро* — что происходит в вашей ситуации и как поступить.\n"
        "✡️ *Каббала* — ваша духовная задача и сильные стороны.\n"
        "🌙 *Натальная карта* — характер, судьба и важные периоды жизни.\n"
        "☕ *Кофейная гуща* — символы вашей чашки и ближайшее будущее.\n"
        "✋ *Линии руки* — ваш характер и путь по ладони.\n"
        "💬 *Личный вопрос* — прямой ответ на то, что вас волнует.\n\n"
        "Выберите, с чего начнём 👇"
    )


# ---------------- /start ----------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.upsert_user(u.id, u.username or "", u.first_name or "", u.last_name or "")
    ctx.user_data.clear()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔮 Один вопрос — {PRICES['1 вопрос']}", callback_data="pkg|1 вопрос")],
        [InlineKeyboardButton(f"✨ Три направления — {PRICES['3 направления']}", callback_data="pkg|3 направления")],
        [InlineKeyboardButton(f"🌙 Полный пакет (5) — {PRICES['Полный пакет']}", callback_data="pkg|Полный пакет")],
        [InlineKeyboardButton(f"👑 Приватная консультация — {PRICES['Приватная консультация']}", callback_data="pkg|Приватная консультация")],
        [InlineKeyboardButton("📜 Мои заказы", callback_data="history")],
        [InlineKeyboardButton("ℹ️ О Маргарите", callback_data="about")],
    ])
    await update.message.reply_text(_welcome(), reply_markup=kb, parse_mode="Markdown")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — начать и выбрать услугу\n/history — мои заказы\n\n"
        "Оформление: услуга → направления → ваши данные → оплата → скриншот перевода.")


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _send_history(update.message, update.effective_user.id)


async def _send_history(message, tg_id):
    rows = db.user_orders(tg_id)
    if not rows:
        await message.reply_text("У вас пока нет заказов. Нажмите /start, чтобы оформить первый разбор.")
        return
    blocks = []
    for o in rows:
        code = o.get("order_code") or db.make_order_code(o.get("order_no", 0))
        dirs = ", ".join(o.get("directions") or []) or "—"
        price = PRICES.get(o.get("package", ""), "")
        created = (o.get("created_at", "") or "")[:16].replace("T", " ")
        st = db.STATUS_RU.get(o.get("status"), o.get("status"))
        blocks.append(
            f"*{code}*\n"
            f"📅 {created}\n"
            f"Услуга: {o.get('package','')} ({price})\n"
            f"Направления: {dirs}\n"
            f"Статус: {st}"
        )
    await message.reply_text("📜 *Ваши заказы:*\n\n" + "\n\n———\n\n".join(blocks), parse_mode="Markdown")


# ---------------- callbacks ----------------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "about":
        return await q.message.reply_text(
            "Маргарита — практикующий таролог и консультант с более чем двадцатилетним опытом. "
            "Таро, натальные карты, каббала, символические практики. Каждый разбор создаётся лично "
            "для вас и проходит проверку перед отправкой. ✨")
    if data == "history":
        return await _send_history(q.message, q.from_user.id)

    if data.startswith("pkg|"):
        return await _choose_package(q, ctx, data.split("|", 1)[1])
    if data.startswith("dir|"):
        return await _toggle_direction(q, ctx, data.split("|", 1)[1])
    if data == "dir_done":
        return await _start_collect(q.message, ctx)
    if data.startswith("hand|"):
        return await _on_hand(q, ctx, data.split("|", 1)[1])
    if data == "paid":
        return await _on_paid(q, ctx)
    if data.startswith("rate|"):
        return await _on_rate(q, ctx, data)


async def _choose_package(q, ctx, pkg):
    # антидубль: есть активный заказ?
    act = db.active_order(q.from_user.id)
    if act:
        code = act.get("order_code") or db.make_order_code(act.get("order_no", 0))
        return await q.message.reply_text(
            f"У вас уже есть активный заказ *{code}* со статусом «{db.STATUS_RU.get(act['status'], act['status'])}».\n"
            "Дождитесь его завершения, прежде чем оформлять новый. 🙏", parse_mode="Markdown")

    ctx.user_data.clear()
    ctx.user_data[D] = {"package": pkg, "directions": [], "photos": []}

    if pkg == "Приватная консультация":
        await q.message.reply_text(
            "👑 *Приватная консультация Маргариты* — 49.99 €\n\n"
            "Полная персональная работа один на один:\n"
            "• анализ прошлого, настоящего и будущего;\n"
            "• ответы на ваши личные вопросы;\n"
            "• индивидуальные рекомендации;\n"
            "• возможность заказа личного амулета;\n"
            "• живое общение с Маргаритой.\n\n"
            "Оставьте данные — и после оплаты мы согласуем удобные дату и время.",
            parse_mode="Markdown")
        return await _start_collect(q.message, ctx)

    if pkg == "Полный пакет":
        ctx.user_data[D]["directions"] = MAIN_FIVE[:]
        return await _start_collect(q.message, ctx)

    return await _show_directions(q.message, ctx)


def _dir_keyboard(chosen, limit):
    pool = ALL_SIX if limit == 1 else MAIN_FIVE
    rows = [[InlineKeyboardButton(("✅ " if d in chosen else "") + d, callback_data=f"dir|{d}")] for d in pool]
    if (limit == 1 and len(chosen) == 1) or (limit == 3 and len(chosen) == 3):
        rows.append([InlineKeyboardButton("➡️ Продолжить", callback_data="dir_done")])
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


# ---------------- сбор данных ----------------
BASE_FIELDS = [
    ("customer_name", "Как вас зовут? (имя)"),
    ("customer_surname", "Ваша фамилия?"),
    ("phone", "Ваш номер телефона? (с кодом страны, например +49…)"),
    ("birth_date", "Дата рождения? (например, 14.03.1990)"),
]
NATAL_FIELDS = [
    ("birth_city", "Город рождения?"),
    ("birth_time", "Время рождения? (например, 14:30, или «не знаю»)"),
]


async def _start_collect(message, ctx):
    ctx.user_data[STEP] = 0
    fields = BASE_FIELDS[:]
    if "Натальная карта" in ctx.user_data[D]["directions"]:
        fields += NATAL_FIELDS
    fields += [("question", "Что вас сейчас волнует больше всего? Опишите ваш вопрос.")]
    ctx.user_data["_fields"] = fields
    await message.reply_text(fields[0][1], reply_markup=ReplyKeyboardRemove())


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db.log_message(update.effective_user.id, "client", update.message.text)
    if D not in ctx.user_data or "_fields" not in ctx.user_data:
        return await cmd_start(update, ctx)
    step = ctx.user_data.get(STEP, 0)
    fields = ctx.user_data["_fields"]
    ctx.user_data[D][fields[step][0]] = update.message.text.strip()
    step += 1
    ctx.user_data[STEP] = step
    if step < len(fields):
        await update.message.reply_text(fields[step][1])
    else:
        ctx.user_data.pop("_fields", None)
        await _after_data(update.message, ctx)


# ---------------- фото (ладонь / кофе) ----------------
async def _after_data(message, ctx):
    d = ctx.user_data[D]
    # ладонь: сначала выбор руки, потом фото
    if "Линии руки" in d["directions"] and not d.get("hand_side"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤚 Левая рука", callback_data="hand|Левая")],
            [InlineKeyboardButton("✋ Правая рука", callback_data="hand|Правая")],
        ])
        await message.reply_text(
            "В хиромантии правая и левая руки означают разное:\n\n"
            "• *Пассивная* рука — врождённые качества, склонности и потенциал.\n"
            "• *Активная* рука — как этот потенциал реализуется сейчас и какие перемены идут на пути.\n\n"
            "Какую руку сфотографируем?", reply_markup=kb, parse_mode="Markdown")
        return
    if "Линии руки" in d["directions"] and not _has_photo(d, "palm"):
        ctx.user_data["_await_photo"] = "palm"
        await message.reply_text("Отправьте чёткую фотографию вашей ладони одним сообщением. ✋")
        return
    if "Кофейная гуща" in d["directions"] and not _has_photo(d, "coffee"):
        ctx.user_data["_await_photo"] = "coffee"
        await message.reply_text(
            "☕ *Гадание по кофейной гуще*\n\n"
            "Используйте натуральный кофе (не растворимый). Если дома нет — можно выпить чашку в кофейне.\n\n"
            "Когда кофе выпит:\n"
            "1. Возьмите чашку левой рукой.\n"
            "2. Переверните её от себя на блюдце.\n"
            "3. Подождите около минуты.\n"
            "4. Сделайте чёткое фото рисунка внутри чашки.\n"
            "5. Загрузите фото сюда.", parse_mode="Markdown")
        return
    await _show_payment(message, ctx)


def _has_photo(d, kind):
    return any(isinstance(p, dict) and p.get("kind") == kind for p in d.get("photos", []))


async def _on_hand(q, ctx, side):
    ctx.user_data[D]["hand_side"] = side
    await q.message.reply_text(f"Выбрана {side.lower()} рука. Теперь отправьте её фотографию. ✋")
    ctx.user_data["_await_photo"] = "palm"


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # скриншот оплаты?
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
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio


def _copy_btn(label, value):
    if CopyTextButton:
        return InlineKeyboardButton(label, copy_text=CopyTextButton(text=value))
    return InlineKeyboardButton(label, callback_data="noop")


async def _show_payment(message, ctx):
    d = ctx.user_data[D]
    price = PRICES.get(d["package"], "")
    dirs = ", ".join(d["directions"]) if d["directions"] else d["package"]
    # QR-коды (карта / USDT)
    if qrcode:
        try:
            await message.reply_photo(_qr_photo(CARD_MONO), caption="QR — карта Monobank")
            await message.reply_photo(_qr_photo(USDT_TRON), caption="QR — USDT (TRON)")
        except Exception:
            pass
    rows = [
        [_copy_btn("📋 Копировать карту Monobank", CARD_MONO)],
        [_copy_btn("📋 Копировать IBAN (Deutsche Bank)", IBAN_DE)],
        [_copy_btn("📋 Копировать USDT (TRON)", USDT_TRON)],
        [InlineKeyboardButton("✅ Я оплатил", callback_data="paid")],
    ]
    await message.reply_text(
        f"Ваш заказ: *{d['package']}* — {price}\nНаправления: {dirs}\n\n{PAYMENT_DETAILS}\n\n"
        "Скопируйте реквизиты кнопкой или отсканируйте QR выше.",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")


async def _on_paid(q, ctx):
    d = ctx.user_data.get(D)
    if not d:
        return await q.message.reply_text("Заказ не найден. Нажмите /start.")
    # защита от повторного нажатия
    if ctx.user_data.get("_creating"):
        return
    ctx.user_data["_creating"] = True
    try:
        # антидубли
        if db.active_order(q.from_user.id):
            await q.message.reply_text("У вас уже есть активный заказ в обработке. Дождитесь проверки оплаты. 🙏")
            return
        if db.recent_duplicate(q.from_user.id, d["package"]):
            await q.message.reply_text("Такой заказ уже создан за последние минуты и находится в обработке. 🙏")
            return
        u = q.from_user
        try:
            order = db.create_order({
                "telegram_id": u.id, "username": u.username or "", "first_name": u.first_name or "",
                "customer_name": d.get("customer_name"), "customer_surname": d.get("customer_surname"),
                "phone": d.get("phone"), "birth_date": d.get("birth_date"),
                "birth_city": d.get("birth_city"), "birth_time": d.get("birth_time"),
                "package": d["package"], "directions": d["directions"],
                "hand_side": d.get("hand_side"), "question": d.get("question"),
                "photos": d["photos"], "status": "WAITING_PAYMENT",
            })
            code = db.make_order_code(order.get("order_no", 0))
            db.set_order(order["id"], {"order_code": code})
        except Exception:
            log.exception("Не удалось создать заказ для %s", u.id)
            await q.message.reply_text("Произошла ошибка при создании заказа. Попробуйте ещё раз через минуту.")
            return
        # убираем кнопки, просим скриншот
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        ctx.user_data["_await_payment_shot"] = order["id"]
        await q.message.reply_text(
            f"Заказ *{code}* создан. 📎\n\n"
            "Теперь пришлите *скриншот перевода* (фото подтверждения оплаты) — "
            "без него заявка не уйдёт на проверку.", parse_mode="Markdown")
    finally:
        ctx.user_data["_creating"] = False


async def _on_payment_screenshot(update, ctx):
    order_id = ctx.user_data.get("_await_payment_shot")
    if not order_id:
        return
    order = db.get_order(order_id)
    if order and order.get("payment_screenshot"):
        await update.message.reply_text("Подтверждение оплаты уже загружено и ожидает проверки администратора. 🙏")
        return
    fid = update.message.photo[-1].file_id
    db.set_order(order_id, {"payment_screenshot": fid, "status": "PAYMENT_CHECK"})
    ctx.user_data.pop("_await_payment_shot", None)
    order = db.get_order(order_id)
    code = order.get("order_code", "")
    await update.message.reply_text(
        f"Спасибо! 🌙 Скриншот получен, заказ *{code}* отправлен на проверку.\n\n"
        "Как только Маргарита подтвердит оплату, начнётся создание вашего разбора — "
        "вы получите его прямо здесь.", parse_mode="Markdown")
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
    await q.edit_message_reply_markup(reply_markup=None)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        "👑 Записаться на приватную консультацию — 49.99 €", callback_data="pkg|Приватная консультация")]])
    await q.message.reply_text(
        f"Спасибо за вашу оценку ({stars}⭐)! Это очень ценно. 🙏\n\n"
        "Если хотите более глубокий разбор своей ситуации — можно записаться на "
        "приватную консультацию Маргариты.", reply_markup=kb)


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
