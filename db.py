# -*- coding: utf-8 -*-
"""Работа с Supabase через REST (service key обходит RLS)."""
import logging
import requests
from config import SUPABASE_URL, SERVICE_KEY

log = logging.getLogger("oracle.db")


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
    return insert("oracle_orders", data)


def get_order(order_id):
    rows = select("oracle_orders", {"id": f"eq.{order_id}", "select": "*"})
    return rows[0] if rows else None


def set_order(order_id, payload):
    return update("oracle_orders", {"id": f"eq.{order_id}"}, payload)


def orders_by_status(status, limit=10):
    return select("oracle_orders", {"status": f"eq.{status}", "select": "*",
                                    "order": "created_at.asc", "limit": str(limit)})


def user_orders(tg_id, limit=10):
    return select("oracle_orders", {"telegram_id": f"eq.{tg_id}", "select": "*",
                                    "order": "created_at.desc", "limit": str(limit)})


# ---------- настройки (admin chat id) ----------
def get_setting(key):
    rows = select("oracle_settings", {"key": f"eq.{key}", "select": "value"})
    return rows[0]["value"] if rows else None


def set_setting(key, value):
    requests.post(f"{SUPABASE_URL}/rest/v1/oracle_settings",
                  headers=_h({"Prefer": "resolution=merge-duplicates"}),
                  json={"key": key, "value": str(value)}, timeout=30)
