# -*- coding: utf-8 -*-
"""Админ-бот Margo Karat — оплаты, проверка раскладов, просмотр чатов."""
import io
import logging

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

import db
from config import ADMIN_SETUP_CODE, CLIENT_BOT_TOKEN

log = logging.getLogger("oracle.admin")


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


# ---------------- /start (привязка владельца) ----------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if _is_admin(chat_id):
        await update.message.reply_text(
            "Вы — администратор Margo Karat. Сюда приходят заказы.\n\n"
            "Команды:\n/users — список клиентов\n/chat <id> — переписка\n/say <id> текст — написать клиенту")
        return
    if db.get_setting("admin_chat_id"):
        await update.message.reply_text("Админ уже назначен. Доступ запрещён.")
        return
    await update.message.reply_text("Это админ-бот Margo Karat.\nВведите код настройки, чтобы стать администратором:")
    ctx.user_data["await_code"] = True


STATUS_FILTERS = [
    ("Новые/на проверке", "PAYMENT_CHECK"),
    ("Ожидают оплату", "WAITING_PAYMENT"),
    ("В работе", "AI_PROCESSING"),
    ("Готовы к отправке", "WAITING_ADMIN"),
    ("Завершённые", "SENT"),
    ("Консультации", "CONSULT_PAID"),
    ("Отменённые", "CANCELLED"),
]


async def cmd_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_chat.id):
        return
    kb = [[InlineKeyboardButton(name, callback_data=f"ordlist|{st}")] for name, st in STATUS_FILTERS]
    kb.append([InlineKeyboardButton("📋 Все последние", callback_data="ordlist|ALL")])
    await update.message.reply_text("📦 Заказы — выберите категорию:", reply_markup=InlineKeyboardMarkup(kb))


async def _order_short(o):
    code = o.get("order_code") or db.make_order_code(o.get("order_no", 0))
    created = (o.get("created_at", "") or "").replace("T", " ")[:16]
    return f"{code} · {created} · {o.get('customer_name','')} · {db.STATUS_RU.get(o['status'], o['status'])}"


async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_chat.id):
        return
    rows = db.recent_chat_users()
    if not rows:
        return await update.message.reply_text("Пользователей пока нет.")
    lines = [f"{u.get('first_name','')} @{u.get('username') or '—'} — id `{u.get('telegram_id')}`" for u in rows]
    await update.message.reply_text("👥 Клиенты:\n\n" + "\n".join(lines) +
                                    "\n\n/chat <id> — переписка, /say <id> текст — написать", parse_mode="Markdown")


async def cmd_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_chat.id):
        return
    if not ctx.args:
        return await update.message.reply_text("Формат: /chat <telegram_id>")
    tg = ctx.args[0]
    msgs = db.chat_history(tg)
    if not msgs:
        return await update.message.reply_text("Переписки нет.")
    who = {"client": "👤", "bot": "🔮", "admin": "👑"}
    lines = []
    for m in msgs:
        t = (m.get("created_at", "") or "").replace("T", " ")[:16]
        lines.append(f"{who.get(m.get('role'),'•')} [{t}] {m.get('text','')}")
    await update.message.reply_text(
        f"💬 Переписка с {tg}:\n\n" + "\n".join(lines)[:3600] +
        f"\n\nОтветить: /say {tg} ваш текст")


async def cmd_say(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_chat.id):
        return
    if len(ctx.args) < 2:
        return await update.message.reply_text("Формат: /say <telegram_id> текст")
    tg = ctx.args[0]
    text = " ".join(ctx.args[1:])
    client_bot = ctx.application.bot_data.get("client_bot")
    try:
        await client_bot.send_message(int(tg), text)
        db.log_message(int(tg), "admin", text)
        await update.message.reply_text("✅ Отправлено клиенту.")
    except Exception as e:
        await update.message.reply_text(f"Не удалось отправить: {e}")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("await_code"):
        ctx.user_data.pop("await_code")
        if update.message.text.strip() == ADMIN_SETUP_CODE:
            db.set_setting("admin_chat_id", update.effective_chat.id)
            await update.message.reply_text("✅ Готово! Вы назначены администратором. Сюда будут приходить заказы.")
        else:
            await update.message.reply_text("❌ Неверный код.")
        return
    edit_id = ctx.user_data.pop("edit_order", None)
    if edit_id and _is_admin(update.effective_chat.id):
        db.set_order(edit_id, {"result": update.message.text, "status": "WAITING_ADMIN"})
        await update.message.reply_text("Текст обновлён. Нажмите «Отправить клиенту», когда готовы.",
                                        reply_markup=_ready_kb(edit_id))


# ---------------- уведомления ----------------
def _order_caption(o):
    dirs = ", ".join(o.get("directions") or [])
    code = o.get("order_code") or db.make_order_code(o.get("order_no", 0))
    return (
        f"🔮 *Новый заказ {code}*\n\n"
        f"Клиент: {o.get('customer_name','')} {o.get('customer_surname','') or ''}\n"
        f"Telegram: @{o.get('username') or '—'} (id {o.get('telegram_id')})\n"
        f"Телефон: {o.get('phone','—')}\n"
        f"Пакет: {o.get('package','')}\n"
        f"Направления: {dirs}\n"
        f"Рука (хиромантия): {o.get('hand_side','—')}\n"
        f"Дата рождения: {o.get('birth_date','—')}\n"
        f"Город/время: {o.get('birth_city','—')} / {o.get('birth_time','—')}\n"
        f"Вопрос: {o.get('question','—')}\n"
        f"Статус: {db.STATUS_RU.get(o.get('status'), o.get('status'))}")


def _pay_kb(order_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"pay_ok|{order_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"pay_no|{order_id}"),
    ]])


def _ready_kb(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Посмотреть", callback_data=f"view|{order_id}")],
        [InlineKeyboardButton("📤 Отправить клиенту", callback_data=f"send|{order_id}"),
         InlineKeyboardButton("🔄 Переделать", callback_data=f"regen|{order_id}")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit|{order_id}")],
    ])


async def notify_new_order(admin_app, order):
    chat = db.get_setting("admin_chat_id")
    if not chat:
        log.warning("admin_chat_id не задан — заказ %s не отправлен", order.get("id"))
        return
    await admin_app.bot.send_message(int(chat), _order_caption(order),
                                     reply_markup=_pay_kb(order["id"]), parse_mode="Markdown")
    # фото клиента (ладонь/кофе)
    for ph in order.get("photos") or []:
        fid = ph.get("file_id") if isinstance(ph, dict) else ph
        cap = ph.get("kind", "") if isinstance(ph, dict) else ""
        try:
            await admin_app.bot.send_photo(int(chat), io.BytesIO(_download_client_photo(fid)), caption=f"Фото: {cap}")
        except Exception:
            log.warning("Не переслать фото заказа %s", order.get("id"))
    # скриншот оплаты
    shot = order.get("payment_screenshot")
    if shot:
        try:
            await admin_app.bot.send_photo(int(chat), io.BytesIO(_download_client_photo(shot)),
                                           caption="💳 Скриншот оплаты")
        except Exception:
            log.warning("Не переслать скриншот заказа %s", order.get("id"))


async def notify_reading_ready(admin_app, order):
    chat = db.get_setting("admin_chat_id")
    if not chat:
        return
    code = order.get("order_code", "")
    await admin_app.bot.send_message(
        int(chat), f"🌙 Готов расклад по заказу {code} ({order.get('customer_name','')}). Проверьте перед отправкой.",
        reply_markup=_ready_kb(order["id"]))


# ---------------- callbacks ----------------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(q.message.chat.id):
        return await q.message.reply_text("Только для администратора.")
    action, oid = q.data.split("|", 1)

    # список заказов по категории
    if action == "ordlist":
        st = None if oid == "ALL" else oid
        rows = db.orders_list(st, limit=15)
        if not rows:
            return await q.message.reply_text("В этой категории заказов нет.")
        kb = [[InlineKeyboardButton(await _order_short(o), callback_data=f"open|{o['id']}")] for o in rows]
        return await q.message.reply_text("Выберите заказ:", reply_markup=InlineKeyboardMarkup(kb))

    # открыть заказ целиком
    if action == "open":
        order = db.get_order(oid)
        if not order:
            return await q.message.reply_text("Заказ не найден.")
        await q.message.reply_text(_order_caption(order), parse_mode="Markdown")
        for ph in order.get("photos") or []:
            fid = ph.get("file_id") if isinstance(ph, dict) else ph
            try:
                await q.message.reply_photo(io.BytesIO(_download_client_photo(fid)),
                                            caption=f"Фото: {ph.get('kind','') if isinstance(ph, dict) else ''}")
            except Exception:
                pass
        if order.get("payment_screenshot"):
            try:
                await q.message.reply_photo(io.BytesIO(_download_client_photo(order["payment_screenshot"])),
                                            caption="💳 Скриншот оплаты")
            except Exception:
                pass
        # клавиатура действий по статусу
        if order["status"] in ("WAITING_PAYMENT", "PAYMENT_CHECK"):
            await q.message.reply_text("Действия:", reply_markup=_pay_kb(order["id"]))
        elif order["status"] in ("WAITING_ADMIN", "SENT"):
            await q.message.reply_text("Действия:", reply_markup=_ready_kb(order["id"]))
        return

    order = db.get_order(oid)
    if not order:
        return await q.message.reply_text("Заказ не найден.")
    client_bot = ctx.application.bot_data.get("client_bot")

    if action == "pay_ok":
        if order.get("package") == "Приватная консультация":
            db.set_order(oid, {"status": "CONSULT_PAID"})
            await q.edit_message_text(q.message.text + "\n\n✅ Оплата подтверждена (консультация).")
            if client_bot:
                try:
                    await client_bot.send_message(order["telegram_id"],
                        "✅ Оплата подтверждена! Спасибо. 🌙\n\n"
                        "Дату и время вашей приватной консультации Маргарита согласует с вами отдельно — "
                        "ожидайте сообщения здесь.")
                    db.log_message(order["telegram_id"], "bot", "Оплата консультации подтверждена")
                except Exception:
                    pass
        else:
            db.set_order(oid, {"status": "APPROVED"})
            await q.edit_message_text(q.message.text + "\n\n✅ Оплата подтверждена. AI создаёт расклад…")
            if client_bot:
                try:
                    msg = "✅ Оплата подтверждена! Маргарита приступила к вашему разбору. Скоро он придёт сюда. ✨"
                    if order.get("add_consult"):
                        msg += "\n\nДату и время приватной консультации Маргарита согласует с вами отдельно."
                    await client_bot.send_message(order["telegram_id"], msg)
                except Exception:
                    pass

    elif action == "pay_no":
        db.set_order(oid, {"status": "REJECTED"})
        await q.edit_message_text(q.message.text + "\n\n❌ Оплата отклонена.")
        if client_bot:
            try:
                await client_bot.send_message(order["telegram_id"],
                    "К сожалению, оплата не найдена. Проверьте перевод или напишите нам — /start.")
            except Exception:
                pass

    elif action == "view":
        txt = order.get("result") or "Расклад ещё не создан."
        for i in range(0, len(txt), 3800):
            await q.message.reply_text(txt[i:i+3800])
        await q.message.reply_text("Действия:", reply_markup=_ready_kb(oid))

    elif action == "regen":
        db.set_order(oid, {"status": "APPROVED", "result": None})
        await q.message.reply_text("🔄 Отправлено на перегенерацию.")

    elif action == "edit":
        ctx.user_data["edit_order"] = oid
        await q.message.reply_text("Пришлите новый текст расклада одним сообщением.")

    elif action == "send":
        txt = order.get("result") or ""
        if not txt:
            return await q.message.reply_text("Текст расклада пуст.")
        if client_bot:
            for i in range(0, len(txt), 3800):
                await client_bot.send_message(order["telegram_id"], txt[i:i+3800])
            db.log_message(order["telegram_id"], "bot", "[расклад отправлен]")
        import datetime as dt
        db.set_order(oid, {"status": "SENT", "sent_at": dt.datetime.utcnow().isoformat()})
        await q.message.reply_text("📤 Расклад отправлен клиенту. Заказ завершён.")


def build_admin_app() -> Application:
    from config import ADMIN_BOT_TOKEN
    app = Application.builder().token(ADMIN_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("chat", cmd_chat))
    app.add_handler(CommandHandler("say", cmd_say))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
