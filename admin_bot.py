# -*- coding: utf-8 -*-
"""Админ-бот Margo Karat — подтверждение оплаты, проверка и отправка раскладов."""
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
    """Скачивает фото по file_id через клиентский бот-токен (id привязан к нему)."""
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
        await update.message.reply_text("Вы — администратор Margo Karat. Новые заказы будут приходить сюда.")
        return
    if db.get_setting("admin_chat_id"):
        await update.message.reply_text("Админ уже назначен. Доступ запрещён.")
        return
    await update.message.reply_text(
        "Это админ-бот Margo Karat.\nВведите код настройки, чтобы стать администратором:")
    ctx.user_data["await_code"] = True


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data.get("await_code"):
        ctx.user_data.pop("await_code")
        if update.message.text.strip() == ADMIN_SETUP_CODE:
            db.set_setting("admin_chat_id", update.effective_chat.id)
            await update.message.reply_text(
                "✅ Готово! Вы назначены администратором. Сюда будут приходить все новые заказы.")
        else:
            await update.message.reply_text("❌ Неверный код.")
        return
    # админ редактирует расклад: ждём новый текст
    edit_id = ctx.user_data.pop("edit_order", None)
    if edit_id and _is_admin(update.effective_chat.id):
        db.set_order(edit_id, {"result": update.message.text, "status": "WAITING_ADMIN"})
        await update.message.reply_text("Текст расклада обновлён. Нажмите «Отправить клиенту», когда будете готовы.",
                                        reply_markup=_ready_kb(edit_id))


# ---------------- уведомления (вызываются из воркера/клиент-бота) ----------------
def _order_caption(o):
    dirs = ", ".join(o.get("directions") or [])
    photos = len(o.get("photos") or [])
    return (
        f"🔮 *Новый заказ Margo Karat №{o.get('order_no','?')}*\n\n"
        f"Клиент: {o.get('customer_name','')} {o.get('customer_surname','') or ''}\n"
        f"Telegram: @{o.get('username') or '—'} (id {o.get('telegram_id')})\n"
        f"Телефон: {o.get('phone','—')}\n"
        f"Пакет: {o.get('package','')}\n"
        f"Направления: {dirs}\n"
        f"Дата рождения: {o.get('birth_date','—')}\n"
        f"Город/время рожд.: {o.get('birth_city','—')} / {o.get('birth_time','—')}\n"
        f"Кофе: {o.get('coffee_mode') or '—'}\n"
        f"Фото: {photos} шт.\n"
        f"Вопрос: {o.get('question','—')}\n\n"
        f"Статус оплаты: ожидает проверки")


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
        log.warning("admin_chat_id не задан — заказ %s не отправлен админу", order.get("id"))
        return
    await admin_app.bot.send_message(int(chat), _order_caption(order),
                                     reply_markup=_pay_kb(order["id"]), parse_mode="Markdown")
    # фото: file_id привязан к клиентскому боту — скачиваем байты и шлём через админ-бот
    for ph in order.get("photos") or []:
        fid = ph.get("file_id") if isinstance(ph, dict) else ph
        cap = ph.get("kind", "") if isinstance(ph, dict) else ""
        try:
            data = _download_client_photo(fid)
            await admin_app.bot.send_photo(int(chat), io.BytesIO(data), caption=cap)
        except Exception:
            log.warning("Не удалось переслать фото заказа %s", order.get("id"))


async def notify_reading_ready(admin_app, order):
    chat = db.get_setting("admin_chat_id")
    if not chat:
        return
    await admin_app.bot.send_message(
        int(chat),
        f"🌙 Готов новый расклад по заказу №{order.get('order_no','?')} "
        f"({order.get('customer_name','')}). Проверьте перед отправкой.",
        reply_markup=_ready_kb(order["id"]))


# ---------------- callbacks ----------------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(q.message.chat.id):
        return await q.message.reply_text("Только для администратора.")
    action, oid = q.data.split("|", 1)
    order = db.get_order(oid)
    if not order:
        return await q.message.reply_text("Заказ не найден.")

    if action == "pay_ok":
        db.set_order(oid, {"status": "APPROVED"})
        await q.edit_message_text(q.message.text + "\n\n✅ Оплата подтверждена. AI создаёт расклад…")
        # сообщить клиенту
        client_bot = ctx.application.bot_data.get("client_bot")
        if client_bot:
            try:
                await client_bot.send_message(order["telegram_id"],
                    "✅ Оплата подтверждена! Маргарита приступила к вашему раскладу. "
                    "Скоро вы получите его здесь. ✨")
            except Exception:
                pass

    elif action == "pay_no":
        db.set_order(oid, {"status": "REJECTED"})
        await q.edit_message_text(q.message.text + "\n\n❌ Оплата отклонена.")
        client_bot = ctx.application.bot_data.get("client_bot")
        if client_bot:
            try:
                await client_bot.send_message(order["telegram_id"],
                    "К сожалению, оплата не найдена. Проверьте реквизиты или напишите нам — /start.")
            except Exception:
                pass

    elif action == "view":
        txt = order.get("result") or "Расклад ещё не создан."
        for i in range(0, len(txt), 3800):
            await q.message.reply_text(txt[i:i+3800])
        await q.message.reply_text("Действия:", reply_markup=_ready_kb(oid))

    elif action == "regen":
        db.set_order(oid, {"status": "APPROVED", "result": None})
        await q.message.reply_text("🔄 Отправлено на перегенерацию — AI создаст новую версию.")

    elif action == "edit":
        ctx.user_data["edit_order"] = oid
        await q.message.reply_text("Пришлите новый текст расклада одним сообщением.")

    elif action == "send":
        client_bot = ctx.application.bot_data.get("client_bot")
        txt = order.get("result") or ""
        if not txt:
            return await q.message.reply_text("Текст расклада пуст.")
        if client_bot:
            for i in range(0, len(txt), 3800):
                await client_bot.send_message(order["telegram_id"], txt[i:i+3800])
        db.set_order(oid, {"status": "SENT"})
        await q.message.reply_text("📤 Расклад отправлен клиенту. Заказ завершён.")


def build_admin_app() -> Application:
    from config import ADMIN_BOT_TOKEN
    app = Application.builder().token(ADMIN_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
