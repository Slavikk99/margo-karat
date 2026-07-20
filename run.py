# -*- coding: utf-8 -*-
"""
MARGO KARAT — Telegram AI Oracle. Единая точка запуска.

Запускает в одном процессе:
  • клиентский бот (Margo Karat) — приём заказов;
  • админ-бот — подтверждение оплаты, проверка и отправка;
  • AI-воркер — генерация раскладов для подтверждённых заказов.

Запуск: python run.py   (переменные — в .env, см. README_УСТАНОВКА.md)
"""
import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import db
from config import (CLIENT_BOT_TOKEN, ADMIN_BOT_TOKEN, SUPABASE_URL, SERVICE_KEY,
                    GROQ_API_KEY, POLL_SEC)
from client_bot import build_client_app
from admin_bot import build_admin_app, notify_new_order, notify_reading_ready
from ai_agent import generate_reading


async def send_review_requests(client_app):
    """Через 1 час после отправки — просим оценку и предлагаем консультацию."""
    for o in db.orders_due_review():
        try:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"{n}⭐", callback_data=f"rate|{o['id']}|{n}") for n in range(1, 6)
            ]])
            await client_app.bot.send_message(
                o["telegram_id"],
                "Надеюсь, расклад оказался для вас полезным. 🌙\n\n"
                "Буду благодарна за отзыв — оцените работу по пятибалльной шкале:",
                reply_markup=kb)
            db.set_order(o["id"], {"review_sent": True})
        except Exception:
            log.exception("Не удалось запросить отзыв по заказу %s", o.get("id"))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(), logging.FileHandler("oracle.log", encoding="utf-8")],
)
log = logging.getLogger("oracle.run")


async def ai_worker(client_app, admin_app):
    """Фоновый цикл: подтверждённые заказы → генерация → на проверку админу."""
    log.info("AI-воркер запущен (опрос каждые %d сек)", POLL_SEC)
    while True:
        try:
            for order in db.orders_by_status("APPROVED", limit=3):
                oid = order["id"]
                log.info("Генерация заказа №%s", order.get("order_no"))
                db.set_order(oid, {"status": "AI_PROCESSING"})
                try:
                    full, pairs = await asyncio.to_thread(generate_reading, order)
                    # сохраняем расклад(ы)
                    for direction, text in pairs:
                        db.insert("oracle_readings",
                                  {"order_id": oid, "direction": direction, "text": text})
                    db.set_order(oid, {"status": "WAITING_ADMIN", "result": full})
                    fresh = db.get_order(oid)
                    await notify_reading_ready(admin_app, fresh)
                    log.info("Заказ №%s готов к проверке (%d слов)",
                             order.get("order_no"), len(full.split()))
                except Exception:
                    log.exception("Ошибка генерации заказа %s", oid)
                    db.set_order(oid, {"status": "APPROVED"})  # вернём в очередь
            await send_review_requests(client_app)
        except Exception:
            log.exception("Ошибка цикла воркера")
        await asyncio.sleep(POLL_SEC)


async def main():
    # проверки конфигурации
    missing = [n for n, v in [
        ("CLIENT_BOT_TOKEN", CLIENT_BOT_TOKEN), ("ADMIN_BOT_TOKEN", ADMIN_BOT_TOKEN),
        ("SUPABASE_URL", SUPABASE_URL), ("SUPABASE_SERVICE_KEY", SERVICE_KEY),
        ("GROQ_API_KEY", GROQ_API_KEY),
    ] if not v]
    if missing:
        raise SystemExit("❌ В .env не заполнено: " + ", ".join(missing))

    client_app = build_client_app()
    admin_app = build_admin_app()

    # связываем боты через модуль-мост (не через bot_data — он попадает в persistence)
    import bridge
    bridge.admin_app = admin_app
    bridge.client_bot = client_app.bot

    async with client_app, admin_app:
        await client_app.start()
        await admin_app.start()
        await client_app.updater.start_polling()
        await admin_app.updater.start_polling()
        log.info("✅ Оба бота запущены. Margo Karat Oracle работает.")
        try:
            await ai_worker(client_app, admin_app)
        finally:
            await client_app.updater.stop()
            await admin_app.updater.stop()
            await client_app.stop()
            await admin_app.stop()


if __name__ == "__main__":
    asyncio.run(main())
