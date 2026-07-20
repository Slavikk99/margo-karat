# -*- coding: utf-8 -*-
"""Работа с Supabase через REST (service key обходит RLS)."""
import logging
import datetime as dt
import requests
from config import SUPABASE_URL, SERVICE_KEY

log = logging.getLogger("oracle.db")

# Внутренний статус -> человекочитаемый (для клиента и админа)
STATUS_RU = {
    "WAITING_PAYMENT": "🕐 Ожидает оплату",
    "PAYMENT_CHECK": "💳 На проверке оплаты",
    "APPROVED": "✅ Оплата подтверждена",
    "AI_PROCESSING": "✨ Выполняется расклад",
    "WAITING_ADMIN": "🌙 Готов к отправке",
    "SENT": "📜 Завершён",
    "COMPLETED": "📜 Завершён",
    "CONSULT_PAID": "✅ Оплачено, согласуем время",
    "SCHEDULED_SEND": "⏰ Запланирована отправка",
    "REJECTED": "❌ Отклонён",
    "CANCELLED": "❌ Отменён",
    "NEW": "🔮 Оформление",
}

CONSULT_STATUS_RU = {
    "WAITING_PAYMENT": "🕐 Ожидает оплату",
    "PAYMENT_CHECK": "💳 На проверке оплаты",
    "CONFIRMED": "✅ Подтверждена",
    "CANCELLED": "❌ Отменена",
    "COMPLETED": "📜 Проведена",
}


def _h(extra=None):
    h = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def insert(table, payload):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=_h({"Prefer": "return=representation"}),
                      json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data[0] if isinstance(data, list) and data else data


def select(table, params):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=_h(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def update(table, match, payload):
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{table}", headers=_h({"Prefer": "return=representation"}),
                       params=match, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


# ---------- удобные обёртки ----------
def upsert_user(tg_id, username, first_name, last_name=""):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/oracle_users",
                      headers=_h({"Prefer": "resolution=merge-duplicates"}),
                      json={"telegram_id": tg_id, "username": username,
                            "first_name": first_name, "last_name": last_name},
                      timeout=30)
    if not r.ok:
        log.warning("upsert_user: %s", r.text)


def create_order(data):
    """Создать заказ. Если новых колонок (add_consult/amount) ещё нет в БД —
    повторяем без них, чтобы заказ гарантированно создался."""
    try:
        return insert("oracle_orders", data)
    except Exception as e:
        log.warning("create_order retry без доп.полей: %s", e)
        safe = {k: v for k, v in data.items() if k not in ("add_consult", "amount", "birth_country")}
        return insert("oracle_orders", safe)


def get_order(order_id):
    rows = select("oracle_orders", {"id": f"eq.{order_id}", "select": "*"})
    return rows[0] if rows else None


def set_order(order_id, payload):
    return update("oracle_orders", {"id": f"eq.{order_id}"}, payload)


def orders_by_status(status, limit=10):
    return select("oracle_orders", {"status": f"eq.{status}", "select": "*",
                                    "order": "created_at.asc", "limit": str(limit)})


def orders_list(status=None, limit=15):
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    if status:
        params["status"] = f"eq.{status}"
    return select("oracle_orders", params)


def user_orders(tg_id, limit=10):
    return select("oracle_orders", {"telegram_id": f"eq.{tg_id}", "select": "*",
                                    "order": "created_at.desc", "limit": str(limit)})


def active_order(tg_id):
    """Активный заказ пользователя, ожидающий оплаты/проверки (антидубль)."""
    rows = select("oracle_orders", {
        "telegram_id": f"eq.{tg_id}",
        "status": "in.(WAITING_PAYMENT,PAYMENT_CHECK)",
        "select": "*", "order": "created_at.desc", "limit": "1"})
    return rows[0] if rows else None


def recent_duplicate(tg_id, package):
    """Есть ли такой же заказ за последние 30 минут."""
    since = (dt.datetime.utcnow() - dt.timedelta(minutes=30)).isoformat()
    rows = select("oracle_orders", {
        "telegram_id": f"eq.{tg_id}", "package": f"eq.{package}",
        "created_at": f"gte.{since}", "select": "id", "limit": "1"})
    return bool(rows)


def make_order_code(order_no):
    return f"MK-{dt.datetime.utcnow().year}-{int(order_no):06d}"


def make_consult_code(order_no):
    return f"MK-C-{dt.datetime.utcnow().year}-{int(order_no):06d}"


# ---------- КОНСУЛЬТАЦИИ (отдельная таблица, не пересекается с заказами) ----------
def create_consultation(data):
    return insert("consultations", data)


def get_consultation(cid):
    rows = select("consultations", {"id": f"eq.{cid}", "select": "*"})
    return rows[0] if rows else None


def set_consultation(cid, payload):
    return update("consultations", {"id": f"eq.{cid}"}, payload)


def active_consultation(tg_id):
    rows = select("consultations", {
        "telegram_id": f"eq.{tg_id}",
        "status": "in.(WAITING_PAYMENT,PAYMENT_CHECK)",
        "select": "*", "order": "created_at.desc", "limit": "1"})
    return rows[0] if rows else None


def consultations_list(status=None, limit=20):
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    if status:
        params["status"] = f"eq.{status}"
    return select("consultations", params)


def user_consultations(tg_id, limit=10):
    return select("consultations", {"telegram_id": f"eq.{tg_id}", "select": "*",
                                    "order": "created_at.desc", "limit": str(limit)})


def orders_due_send():
    """Заказы с запланированной отправкой, которым пора уйти клиенту."""
    now = dt.datetime.utcnow().isoformat()
    return select("oracle_orders", {
        "status": "eq.SCHEDULED_SEND", "scheduled_send_at": f"lte.{now}",
        "select": "*", "limit": "10"})


# ---------- лог переписки (для админ-панели чатов) ----------
def log_message(tg_id, role, text):
    try:
        insert("oracle_messages", {"telegram_id": tg_id, "role": role, "text": (text or "")[:4000]})
    except Exception:
        pass


def chat_history(tg_id, limit=20):
    rows = select("oracle_messages", {"telegram_id": f"eq.{tg_id}", "select": "*",
                                      "order": "created_at.desc", "limit": str(limit)})
    return list(reversed(rows))


def recent_chat_users(limit=20):
    return select("oracle_users", {"select": "*", "order": "created_at.desc", "limit": str(limit)})


def get_user(tg_id):
    rows = select("oracle_users", {"telegram_id": f"eq.{tg_id}", "select": "*"})
    return rows[0] if rows else None


def ratings(limit=30):
    return select("oracle_orders", {"rating": "not.is.null", "select": "*",
                                    "order": "created_at.desc", "limit": str(limit)})


def support_messages(limit=30):
    return select("oracle_messages", {"role": "eq.support", "select": "*",
                                      "order": "created_at.desc", "limit": str(limit)})


def orders_due_review():
    """Заказы, отправленные >1ч назад, без запрошенного отзыва."""
    cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=1)).isoformat()
    return select("oracle_orders", {
        "status": "in.(SENT,COMPLETED)", "review_sent": "eq.false",
        "sent_at": f"lt.{cutoff}", "select": "*", "limit": "10"})


# ---------- настройки (admin chat id) ----------
def get_setting(key):
    rows = select("oracle_settings", {"key": f"eq.{key}", "select": "value"})
    return rows[0]["value"] if rows else None


def set_setting(key, value):
    requests.post(f"{SUPABASE_URL}/rest/v1/oracle_settings",
                  headers=_h({"Prefer": "resolution=merge-duplicates"}),
                  json={"key": key, "value": str(value)}, timeout=30)
