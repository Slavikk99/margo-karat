# -*- coding: utf-8 -*-
"""AI Oracle Agent — генерация раскладов (800+ слов на направление) + анализ фото."""
import base64
import logging
import random

import requests
from groq import Groq

from config import (GROQ_API_KEY, GROQ_MODEL, GROQ_VISION_MODEL,
                    CLIENT_BOT_TOKEN)
from knowledge import STYLE, KNOWLEDGE, TAROT_CARDS

log = logging.getLogger("oracle.ai")
_groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


# ---------- анализ фото (ладонь / кофейная гуща) ----------
def _download_telegram_file(file_id):
    """Скачивает фото из Telegram по file_id (через клиентский бот-токен)."""
    r = requests.get(f"https://api.telegram.org/bot{CLIENT_BOT_TOKEN}/getFile",
                     params={"file_id": file_id}, timeout=30)
    r.raise_for_status()
    path = r.json()["result"]["file_path"]
    f = requests.get(f"https://api.telegram.org/file/bot{CLIENT_BOT_TOKEN}/{path}", timeout=60)
    f.raise_for_status()
    return f.content


def analyze_image(file_id, what):
    if not _groq:
        return ""
    try:
        img = _download_telegram_file(file_id)
        b64 = base64.b64encode(img).decode()
        resp = _groq.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "text", "text":
                    f"Опиши по-русски это фото ({what}) для символического разбора: "
                    "видимые линии, формы, узоры, образы и их расположение. 150-250 слов."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            temperature=0.6, max_tokens=800, timeout=120,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        log.warning("analyze_image: %s", e)
        return ""


# ---------- генерация одного направления ----------
def _generate_direction(order, direction, image_note=""):
    cards = ", ".join(random.sample(TAROT_CARDS, 5))
    ctx = (
        f"ДАННЫЕ КЛИЕНТА:\n"
        f"Имя: {order.get('customer_name','')} {order.get('customer_surname','') or ''}\n"
        f"Дата рождения: {order.get('birth_date','не указана')}\n"
        f"Город рождения: {order.get('birth_city','') or '—'}\n"
        f"Время рождения: {order.get('birth_time','') or '—'}\n"
        f"Направление разбора: {direction}\n"
        f"Вопрос клиента: {order.get('question','') or '—'}\n"
    )
    if direction == "Линии руки":
        hs = order.get("hand_side")
        ctx += f"Прислана рука: {hs or 'не указана'}\n"
    if image_note:
        ctx += f"\nОПИСАНИЕ ЗАГРУЖЕННОГО ФОТО:\n{image_note}\n"
    if direction in ("Таро", "Задать вопрос"):
        ctx += f"\nВыпавшие карты (кратко назови три и сразу переходи к анализу): {cards}\n"

    resp = _groq.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": f"{STYLE}\n\n{KNOWLEDGE[direction]}"},
            {"role": "user", "content": ctx +
             "\nНапиши персональный разбор ~800 слов. Минимум теории о картах — максимум пользы, "
             "анализа ситуации и конкретных рекомендаций, что делать дальше."},
        ],
        temperature=0.9, max_tokens=4096, timeout=300,
    )
    text = (resp.choices[0].message.content or "").strip()

    # контроль качества: если коротко — дорасширяем
    if len(text.split()) < 550:
        resp2 = _groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": f"{STYLE}\n\n{KNOWLEDGE[direction]}"},
                {"role": "user", "content": ctx},
                {"role": "assistant", "content": text},
                {"role": "user", "content": "Углуби и расширь разбор до 800-1000 слов, сохранив живой стиль."},
            ],
            temperature=0.9, max_tokens=4096, timeout=300,
        )
        text = (resp2.choices[0].message.content or text).strip()
    return text


# ---------- полный расклад по заказу ----------
def generate_reading(order):
    """Возвращает (полный_текст, [(direction, text), ...])."""
    if not _groq:
        raise RuntimeError("GROQ_API_KEY не задан")

    directions = order.get("directions") or []
    if isinstance(directions, str):
        directions = [directions]

    # анализ фото по каждому подходящему направлению
    notes = {}
    photos = order.get("photos") or []
    for ph in photos:
        fid = ph.get("file_id") if isinstance(ph, dict) else ph
        kind = (ph.get("kind") if isinstance(ph, dict) else "")
        if kind == "palm" or "Линии руки" in directions and "palm" not in notes:
            notes["Линии руки"] = analyze_image(fid, "ладонь руки")
        if kind == "coffee" or "Кофейная гуща" in directions and "coffee" not in notes:
            notes["Кофейная гуща"] = analyze_image(fid, "кофейная гуща в чашке")

    sections, pairs = [], []
    for d in directions:
        if d not in KNOWLEDGE:
            continue
        body = _generate_direction(order, d, notes.get(d, ""))
        title = "ОТВЕТ НА ВАШ ВОПРОС" if d == "Задать вопрос" else d.upper()
        sections.append(f"✦ {title} ✦\n\n{body}")
        pairs.append((d, body))
        log.info("  ✓ %s — %d слов", d, len(body.split()))

    header = f"{order.get('customer_name','')}, вот ваш разбор от Маргариты.\n\n"
    footer = ("\n\n— \nС теплом,\nМаргарита · Margo Karat")
    full = header + "\n\n\n".join(sections) + footer
    return full, pairs
