# -*- coding: utf-8 -*-
"""Админ-бот Margo Karat — CRM-панель управления (всё на кнопках)."""
import io
import logging
import datetime as dt

import requests
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup)
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

import bridge
import db
from config import ADMIN_SETUP_CODE, CLIENT_BOT_TOKEN

log = logging.getLogger("oracle.admin")

# нижнее меню админа
M_ORDERS = "📦 Заказы"
M_CONSULTS = "📅 Консультации"
M_CLIENTS = "👥 Клиенты"
M_RATINGS = "⭐ Оценки"
M_SUPPORT = "💬 Обращения"
M_STATS = "📊 Статистика"
ADMIN_MENU = ReplyKeyboardMarkup(
    [[M_ORDERS, M_CONSULTS], [M_CLIENTS, M_RATINGS], [M_SUPPORT, M_STATS]], resize_keyboard=True)

CONSULT_FILTERS = [
    ("🆕 На проверке оплаты", "PAYMENT_CHECK"),
    ("🕐 Ожидают оплату", "WAITING_PAYMENT"),
    ("✅ Подтверждённые", "CONFIRMED"),
    ("❌ Отменённые", "CANCELLED"),
]

STATUS_FILTERS = [
    ("🆕 На проверке оплаты", "PAYMENT_CHECK"),
    ("🕐 Ожидают оплату", "WAITING_PAYMENT"),
    ("⏳ В работе", "AI_PROCESSING"),
    ("📤 Готовы к отправке", "WAITING_ADMIN"),
    ("✅ Завершённые", "SENT"),
    ("👑 Консультации", "CONSULT_PAID"),
    ("❌ Отменённые", "CANCELLED"),
]


def _download_client_photo(file_id):
    r = requests.get(f"https://api.telegram.org/bot{CLIENT_BOT_TOKEN}/getFile",
                     params={"file_id": file_id}, timeout=30)
    r.raise_for_status()
    path = r.json()["result"]["file_path"]
    f = requests.get(f"https://api.telegram.org/file/bot{CLIENT_BOT_TOKEN}/{path}", timeout=60)
    f.raise_for_status()
    return f.content


def _is_admin(chat_id):
    saved = db.get_setting("admin_chat_id")
    return saved and str(chat_id) == str(saved)


# ---------------- /start ----------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if _is_admin(chat_id):
        return await update.message.reply_text(
            "👑 Панель управления Margo Karat. Выберите раздел ниже.", reply_markup=ADMIN_MENU)
    if db.get_setting("admin_chat_id"):
        return await update.message.reply_text("Админ уже назначен. Доступ запрещён.")
    await update.message.reply_text("Это админ-бот Margo Karat.\nВведите код настройки:")
    ctx.user_data["await_code"] = True


# ---------------- меню (текстовые кнопки) ----------------
async def on_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    if not _is_admin(update.effective_chat.id):
        # код настройки
        if ctx.user_data.get("await_code"):
            ctx.user_data.pop("await_code")
            if txt.strip() == ADMIN_SETUP_CODE:
                db.set_setting("admin_chat_id", update.effective_chat.id)
                return await update.message.reply_text("✅ Готово! Вы администратор.", reply_markup=ADMIN_MENU)
            return await update.message.reply_text("❌ Неверный код.")
        return

    # ответ клиенту (после кнопки «Написать»)
    reply_to = ctx.user_data.pop("reply_to", None)
    if reply_to:
        try:
            await bridge.client_bot.send_message(int(reply_to), txt)
            db.log_message(int(reply_to), "admin", txt)
            return await update.message.reply_text("✅ Отправлено клиенту.", reply_markup=ADMIN_MENU)
        except Exception as e:
            return await update.message.reply_text(f"Не удалось отправить: {e}")

    # редактирование расклада
    edit_id = ctx.user_data.pop("edit_order", None)
    if edit_id:
        db.set_order(edit_id, {"result": txt, "status": "WAITING_ADMIN"})
        return await update.message.reply_text("Текст обновлён.", reply_markup=_ready_kb(edit_id))

    if txt == M_ORDERS:
        kb = [[InlineKeyboardButton(n, callback_data=f"ordlist|{s}")] for n, s in STATUS_FILTERS]
        kb.append([InlineKeyboardButton("📋 Все последние", callback_data="ordlist|ALL")])
        return await update.message.reply_text("📦 Заказы — выберите категорию:", reply_markup=InlineKeyboardMarkup(kb))
    if txt == M_CONSULTS:
        kb = [[InlineKeyboardButton(n, callback_data=f"clist|{s}")] for n, s in CONSULT_FILTERS]
        return await update.message.reply_text("📅 Консультации — выберите категорию:",
                                               reply_markup=InlineKeyboardMarkup(kb))
    if txt == M_CLIENTS:
        return await _show_clients(update.message)
    if txt == M_RATINGS:
        return await _show_ratings(update.message)
    if txt == M_SUPPORT:
        return await _show_support(update.message)
    if txt == M_STATS:
        return await _show_stats(update.message)


# ---------------- разделы ----------------
async def _show_clients(message):
    rows = db.recent_chat_users()
    if not rows:
        return await message.reply_text("Клиентов пока нет.")
    kb = [[InlineKeyboardButton(f"{u.get('first_name','')} @{u.get('username') or '—'}",
                                callback_data=f"usr|{u.get('telegram_id')}")] for u in rows]
    await message.reply_text("👥 Клиенты:", reply_markup=InlineKeyboardMarkup(kb))


async def _show_ratings(message):
    rows = db.ratings()
    if not rows:
        return await message.reply_text("Оценок пока нет.")
    vals = [r["rating"] for r in rows if r.get("rating")]
    avg = round(sum(vals) / len(vals), 2) if vals else 0
    lines = [f"📊 Средняя оценка: *{avg}* (всего {len(vals)})\n"]
    for r in rows[:15]:
        d = (r.get("created_at", "") or "")[:10]
        lines.append(f"{'⭐'*int(r['rating'])} — {r.get('customer_name','')} ({r.get('order_code','')}) · {d}")
    await message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _show_support(message):
    rows = db.support_messages()
    if not rows:
        return await message.reply_text("Обращений пока нет.")
    for m in rows[:10]:
        t = (m.get("created_at", "") or "").replace("T", " ")[:16]
        u = db.get_user(m.get("telegram_id")) or {}
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 Ответить", callback_data=f"reply|{m.get('telegram_id')}")]])
        await message.reply_text(
            f"📩 [{t}] {u.get('first_name','')} @{u.get('username') or '—'} (id {m.get('telegram_id')})\n\n"
            f"«{m.get('text','')}»", reply_markup=kb)


async def _show_stats(message):
    orders = db.orders_list(None, limit=500)
    from collections import Counter
    c = Counter(o.get("status") for o in orders)
    paid = c.get("APPROVED", 0) + c.get("AI_PROCESSING", 0) + c.get("WAITING_ADMIN", 0) + c.get("SENT", 0) + c.get("CONSULT_PAID", 0)
    lines = [
        "📊 *Статистика*",
        f"Всего заказов: {len(orders)}",
        f"🆕 На проверке: {c.get('PAYMENT_CHECK',0)}",
        f"🕐 Ждут оплату: {c.get('WAITING_PAYMENT',0)}",
        f"✅ Оплачено/в работе: {paid}",
        f"📜 Отправлено: {c.get('SENT',0)}",
        f"❌ Отменено: {c.get('CANCELLED',0)}",
    ]
    await message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------- карточки/клавиатуры ----------------
def _order_caption(o):
    dirs = ", ".join(o.get("directions") or [])
    code = o.get("order_code") or db.make_order_code(o.get("order_no", 0))
    amount = o.get("amount")
    price = f"{amount:.2f} €" if amount else "—"
    created = (o.get("created_at", "") or "").replace("T", " ")[:16]
    return (
        f"🔮 *Заказ {code}*\n\n"
        f"Клиент: {o.get('customer_name','')} {o.get('customer_surname','') or ''}\n"
        f"Telegram: @{o.get('username') or '—'} (id {o.get('telegram_id')})\n"
        f"Телефон: {o.get('phone') or '—'}\n"
        f"Пакет: {o.get('package','')}{' + консультация' if o.get('add_consult') else ''}\n"
        f"Направления: {dirs}\n"
        f"Рука: {o.get('hand_side') or '—'}\n"
        f"Дата рождения: {o.get('birth_date') or '—'}\n"
        f"Город рождения: {o.get('birth_city') or '—'}\n"
        f"Время рождения: {o.get('birth_time') or '—'}\n"
        f"Вопрос/детали: {o.get('question') or '—'}\n"
        f"Сумма: {price}\n"
        f"Создан: {created}\n"
        f"Статус: {db.STATUS_RU.get(o.get('status'), o.get('status'))}")


def _pay_kb(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"pay_ok|{order_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"pay_no|{order_id}"),
    ]])


def _ready_kb(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Посмотреть расклад", callback_data=f"view|{order_id}")],
        [InlineKeyboardButton("📤 Отправить клиенту", callback_data=f"sendmenu|{order_id}"),
         InlineKeyboardButton("🔄 Переделать", callback_data=f"regen|{order_id}")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit|{order_id}")],
    ])


def _send_menu_kb(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Отправить сейчас", callback_data=f"snd|{order_id}|0")],
        [InlineKeyboardButton("⏰ Через 1 час", callback_data=f"snd|{order_id}|1"),
         InlineKeyboardButton("⏰ 2 часа", callback_data=f"snd|{order_id}|2"),
         InlineKeyboardButton("⏰ 3 часа", callback_data=f"snd|{order_id}|3")],
    ])


# ---------------- уведомления от клиентского бота ----------------
async def _send_admin(admin_app, text, kb=None):
    chat = db.get_setting("admin_chat_id")
    if not chat:
        log.warning("admin_chat_id не задан")
        return None
    return await admin_app.bot.send_message(int(chat), text, reply_markup=kb, parse_mode="Markdown")


async def notify_new_order(admin_app, order):
    chat = db.get_setting("admin_chat_id")
    if not chat:
        log.error("admin_chat_id не задан — заказ %s не отправлен", order.get("id"))
        return
    await admin_app.bot.send_message(int(chat), _order_caption(order),
                                     reply_markup=_pay_kb(order["id"]), parse_mode="Markdown")
    for ph in order.get("photos") or []:
        fid = ph.get("file_id") if isinstance(ph, dict) else ph
        try:
            await admin_app.bot.send_photo(int(chat), io.BytesIO(_download_client_photo(fid)),
                                           caption=f"Фото: {ph.get('kind','') if isinstance(ph, dict) else ''}")
        except Exception:
            log.warning("не переслать фото %s", order.get("id"))
    if order.get("payment_screenshot"):
        try:
            await admin_app.bot.send_photo(int(chat), io.BytesIO(_download_client_photo(order["payment_screenshot"])),
                                           caption="💳 Скриншот оплаты")
        except Exception:
            log.warning("не переслать скриншот %s", order.get("id"))


async def notify_reading_ready(admin_app, order):
    await _send_admin(admin_app,
                      f"🌙 Готов расклад {order.get('order_code','')} ({order.get('customer_name','')}). Проверьте.",
                      _ready_kb(order["id"]))


def _consult_caption(c):
    return (
        f"📅 *Заявка на консультацию {c.get('order_code','')}*\n\n"
        f"Клиент: {c.get('name','')} {c.get('surname','') or ''}\n"
        f"Telegram: @{c.get('username') or '—'} (id {c.get('telegram_id')})\n"
        f"Телефон: {c.get('phone') or '—'}\n"
        f"Дата рождения: {c.get('birth_date') or '—'}\n"
        f"Время рождения: {c.get('birth_time') or '—'}\n"
        f"Город рождения: {c.get('birth_city') or '—'}\n"
        f"Желаемый день: {c.get('desired_day') or '—'}\n"
        f"Желаемое время: {c.get('desired_time') or '—'}\n"
        f"Комментарий: {c.get('comment') or '—'}\n"
        f"Сумма: 49.99 €\n"
        f"Статус: {db.CONSULT_STATUS_RU.get(c.get('status'), c.get('status'))}")


def _consult_kb(cid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Принять", callback_data=f"cok|{cid}"),
        InlineKeyboardButton("❌ Отменить", callback_data=f"cno|{cid}"),
    ]])


async def notify_new_consultation(admin_app, c):
    chat = db.get_setting("admin_chat_id")
    if not chat:
        log.error("admin_chat_id не задан — консультация не отправлена")
        return
    await admin_app.bot.send_message(int(chat), _consult_caption(c),
                                     reply_markup=_consult_kb(c["id"]), parse_mode="Markdown")
    if c.get("payment_screenshot"):
        try:
            await admin_app.bot.send_photo(int(chat), io.BytesIO(_download_client_photo(c["payment_screenshot"])),
                                           caption="💳 Скриншот оплаты")
        except Exception:
            log.warning("не переслать скрин консультации")


async def notify_support(admin_app, user, text):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 Ответить", callback_data=f"reply|{user.id}")]])
    await _send_admin(admin_app,
                      f"📩 *Новое обращение*\nИмя: {user.first_name or ''}\nUsername: @{user.username or '—'}\n"
                      f"Telegram ID: {user.id}\n\nСообщение:\n«{text}»", kb)


async def notify_rating(admin_app, user, stars, order_id):
    o = db.get_order(order_id) or {}
    await _send_admin(admin_app,
                      f"⭐ *Новая оценка* {('⭐'*stars)}\nКлиент: {user.first_name or ''} @{user.username or '—'}\n"
                      f"Заказ: {o.get('order_code','')}")


# ---------------- callbacks ----------------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(q.message.chat.id):
        return await q.message.reply_text("Только для администратора.")
    action, arg = q.data.split("|", 1)

    if action == "ordlist":
        st = None if arg == "ALL" else arg
        rows = db.orders_list(st, limit=20)
        if not rows:
            return await q.message.reply_text("В этой категории пусто.")
        kb = []
        for o in rows:
            code = o.get("order_code") or db.make_order_code(o.get("order_no", 0))
            kb.append([InlineKeyboardButton(f"{code} · {o.get('customer_name','')}",
                                            callback_data=f"open|{o['id']}")])
        return await q.message.reply_text("Выберите заказ:", reply_markup=InlineKeyboardMarkup(kb))

    if action == "open":
        order = db.get_order(arg)
        if not order:
            return await q.message.reply_text("Заказ не найден.")
        await q.message.reply_text(_order_caption(order), parse_mode="Markdown")
        for ph in order.get("photos") or []:
            fid = ph.get("file_id") if isinstance(ph, dict) else ph
            try:
                await q.message.reply_photo(io.BytesIO(_download_client_photo(fid)))
            except Exception:
                pass
        if order.get("payment_screenshot"):
            try:
                await q.message.reply_photo(io.BytesIO(_download_client_photo(order["payment_screenshot"])),
                                            caption="💳 Скриншот оплаты")
            except Exception:
                pass
        if order["status"] in ("WAITING_PAYMENT", "PAYMENT_CHECK"):
            await q.message.reply_text("Действия:", reply_markup=_pay_kb(order["id"]))
        elif order["status"] in ("WAITING_ADMIN", "SENT", "AI_PROCESSING"):
            await q.message.reply_text("Действия:", reply_markup=_ready_kb(order["id"]))
        return

    if action == "usr":
        u = db.get_user(arg) or {}
        orders = db.user_orders(arg, limit=5)
        hist = "\n".join(f"• {o.get('order_code','')} — {db.STATUS_RU.get(o['status'], o['status'])}" for o in orders) or "нет"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Написать клиенту", callback_data=f"reply|{arg}")],
            [InlineKeyboardButton("📜 Переписка", callback_data=f"hist|{arg}")],
        ])
        return await q.message.reply_text(
            f"👤 *{u.get('first_name','')} {u.get('last_name','') or ''}*\n"
            f"Username: @{u.get('username') or '—'}\nID: {arg}\nТелефон: {u.get('phone') or '—'}\n\n"
            f"Заказы:\n{hist}", reply_markup=kb, parse_mode="Markdown")

    if action == "hist":
        msgs = db.chat_history(arg)
        who = {"client": "👤", "bot": "🔮", "admin": "👑", "support": "📩"}
        lines = [f"{who.get(m.get('role'),'•')} [{(m.get('created_at','') or '')[:16].replace('T',' ')}] {m.get('text','')}" for m in msgs]
        return await q.message.reply_text("💬 Переписка:\n\n" + ("\n".join(lines)[:3600] or "пусто"))

    if action == "reply":
        ctx.user_data["reply_to"] = arg
        return await q.message.reply_text(f"Напишите сообщение для клиента (id {arg}) — оно уйдёт от имени бота:")

    # ----- консультации -----
    if action == "clist":
        st = None if arg == "ALL" else arg
        rows = db.consultations_list(st, limit=20)
        if not rows:
            return await q.message.reply_text("В этой категории пусто.")
        kb = [[InlineKeyboardButton(f"{c.get('order_code','')} · {c.get('name','')}",
                                    callback_data=f"copen|{c['id']}")] for c in rows]
        return await q.message.reply_text("Выберите консультацию:", reply_markup=InlineKeyboardMarkup(kb))
    if action == "copen":
        c = db.get_consultation(arg)
        if not c:
            return await q.message.reply_text("Заявка не найдена.")
        await q.message.reply_text(_consult_caption(c), parse_mode="Markdown")
        if c.get("payment_screenshot"):
            try:
                await q.message.reply_photo(io.BytesIO(_download_client_photo(c["payment_screenshot"])),
                                            caption="💳 Скриншот оплаты")
            except Exception:
                pass
        if c["status"] in ("WAITING_PAYMENT", "PAYMENT_CHECK"):
            await q.message.reply_text("Действия:", reply_markup=_consult_kb(c["id"]))
        return
    if action in ("cok", "cno"):
        c = db.get_consultation(arg)
        if not c:
            return await q.message.reply_text("Заявка не найдена.")
        if action == "cok":
            db.set_consultation(arg, {"status": "CONFIRMED"})
            await q.edit_message_text(q.message.text + "\n\n✅ Принято.")
            try:
                await bridge.client_bot.send_message(c["telegram_id"],
                    "Маргарита получила ваш запрос. Консультация успешно подтверждена. Скоро с вами свяжутся лично. 🌙")
            except Exception:
                pass
        else:
            db.set_consultation(arg, {"status": "CANCELLED"})
            await q.edit_message_text(q.message.text + "\n\n❌ Отменено.")
            try:
                await bridge.client_bot.send_message(c["telegram_id"],
                    "К сожалению, возникла проблема с подтверждением оплаты. "
                    "Маргарита не сможет провести консультацию по этой заявке.")
            except Exception:
                pass
        return

    # ----- отложенная отправка расклада -----
    if action == "sendmenu":
        return await q.message.reply_text("Когда отправить расклад клиенту?", reply_markup=_send_menu_kb(arg))
    if action == "snd":
        oid, hours = arg.split("|")
        hours = int(hours)
        order = db.get_order(oid)
        if not order or not order.get("result"):
            return await q.message.reply_text("Текст расклада пуст.")
        if hours == 0:
            txt = order["result"]
            for i in range(0, len(txt), 3800):
                await bridge.client_bot.send_message(order["telegram_id"], txt[i:i+3800])
            db.log_message(order["telegram_id"], "bot", "[расклад отправлен]")
            db.set_order(oid, {"status": "SENT", "sent_at": dt.datetime.utcnow().isoformat()})
            return await q.message.reply_text("📤 Расклад отправлен клиенту.")
        # запланировать
        when = (dt.datetime.utcnow() + dt.timedelta(hours=hours)).isoformat()
        db.set_order(oid, {"status": "SCHEDULED_SEND", "scheduled_send_at": when})
        dirs = ", ".join(order.get("directions") or [])
        amount = order.get("amount")
        price_line = f"Стоимость заказа: {amount:.2f} €\n" if amount else ""
        try:
            await bridge.client_bot.send_message(order["telegram_id"],
                "Ваш персональный расклад подготовлен и находится в обработке. 🌙\n\n"
                f"Направления: {dirs}\n"
                f"{price_line}"
                f"Номер заказа: {order.get('order_code','')}\n"
                f"Имя: {order.get('customer_name','')}\n\n"
                "Скоро вы получите его здесь.")
        except Exception:
            pass
        return await q.message.reply_text(f"⏰ Расклад запланирован к отправке через {hours} ч.")

    # действия по заказу
    order = db.get_order(arg)
    if not order:
        return await q.message.reply_text("Заказ не найден.")
    client_bot = bridge.client_bot

    if action == "pay_ok":
        if order.get("package") == "Приватная консультация":
            db.set_order(arg, {"status": "CONSULT_PAID"})
            await q.edit_message_text(q.message.text + "\n\n✅ Оплата подтверждена (консультация).")
            try:
                await client_bot.send_message(order["telegram_id"],
                    "✅ Оплата подтверждена! Спасибо. 🌙\nМаргарита свяжется с вами, чтобы согласовать дату и время.")
            except Exception:
                pass
        else:
            db.set_order(arg, {"status": "APPROVED"})
            await q.edit_message_text(q.message.text + "\n\n✅ Оплата подтверждена. AI создаёт расклад…")
            try:
                msg = "✅ Оплата подтверждена! Маргарита приступила к вашему разбору. Скоро он придёт сюда. ✨"
                if order.get("add_consult"):
                    msg += "\n\nДату и время приватной консультации согласуем отдельно."
                await client_bot.send_message(order["telegram_id"], msg)
            except Exception:
                pass

    elif action == "pay_no":
        db.set_order(arg, {"status": "REJECTED"})
        await q.edit_message_text(q.message.text + "\n\n❌ Оплата отклонена.")
        try:
            await client_bot.send_message(order["telegram_id"],
                "К сожалению, оплата не найдена. Проверьте перевод или напишите нам — /start.")
        except Exception:
            pass

    elif action == "view":
        txt = order.get("result") or "Расклад ещё не создан."
        for i in range(0, len(txt), 3800):
            await q.message.reply_text(txt[i:i+3800])
        await q.message.reply_text("Действия:", reply_markup=_ready_kb(arg))

    elif action == "regen":
        db.set_order(arg, {"status": "APPROVED", "result": None})
        await q.message.reply_text("🔄 Отправлено на перегенерацию.")

    elif action == "edit":
        ctx.user_data["edit_order"] = arg
        await q.message.reply_text("Пришлите новый текст расклада одним сообщением.")

    elif action == "send":
        txt = order.get("result") or ""
        if not txt:
            return await q.message.reply_text("Текст расклада пуст.")
        for i in range(0, len(txt), 3800):
            await client_bot.send_message(order["telegram_id"], txt[i:i+3800])
        db.log_message(order["telegram_id"], "bot", "[расклад отправлен]")
        db.set_order(arg, {"status": "SENT", "sent_at": dt.datetime.utcnow().isoformat()})
        await q.message.reply_text("📤 Расклад отправлен клиенту. Заказ завершён.")


def build_admin_app() -> Application:
    from config import ADMIN_BOT_TOKEN
    app = Application.builder().token(ADMIN_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_menu))
    return app
