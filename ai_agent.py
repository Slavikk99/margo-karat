# -*- coding: utf-8 -*-
"""AI Oracle Agent — генерация раскладов (800+ слов на направление) + анализ фото."""
import re
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


def check_image(file_id, kind):
    """Проверяет, что на фото то, что нужно. kind: 'palm' | 'coffee'.
    Возвращает True, если подходит ИЛИ проверка недоступна (fail-open)."""
    if not _groq:
        return True
    q = ("Внимательно посмотри на фото. На нём крупным планом человеческая ладонь с видимыми линиями руки? "
         "Ответь строго одним словом: ДА или НЕТ."
         if kind == "palm" else
         "Внимательно посмотри на фото. На нём кофейная чашка с кофейной гущей (следами кофе) внутри? "
         "Ответь строго одним словом: ДА или НЕТ.")
    try:
        img = _download_telegram_file(file_id)
        b64 = base64.b64encode(img).decode()
        resp = _groq.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": q},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            temperature=0, max_tokens=200, timeout=60,
        )
        ans = (resp.choices[0].message.content or "").strip().lower()
        log.info("check_image[%s] ответ модели: %r", kind, ans[:80])
        # явное «нет» — отклоняем; иначе принимаем (fail-open при неясности/сбое)
        if re.search(r"\bнет\b", ans) or re.search(r"\bno\b", ans):
            return False
        return True
    except Exception as e:
        log.warning("check_image недоступен (%s) — пропускаю проверку", e)
        return True


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

    sys = f"{STYLE}\n\n{KNOWLEDGE[direction]}"
    user = (ctx + "\nНапиши персональный разбор минимум 600 слов по обязательной структуре из 9 частей. "
            "Минимум теории о картах — максимум пользы, анализа судьбы и конкретных выводов. "
            "Только по-русски, без английских слов, иероглифов и случайных символов.")

    def _gen(extra_msgs=None, temp=0.9):
        msgs = [{"role": "system", "content": sys}, {"role": "user", "content": user}]
        if extra_msgs:
            msgs += extra_msgs
        r = _groq.chat.completions.create(model=GROQ_MODEL, messages=msgs,
                                          temperature=temp, max_tokens=4096, timeout=300)
        return (r.choices[0].message.content or "").strip()

    text = _gen()
    # контроль качества: короткий / иероглифы / много латиницы / мусор → перегенерация
    for _ in range(2):
        if not _quality_bad(text):
            break
        if len(text.split()) < 600:
            text = _gen([{"role": "assistant", "content": text},
                         {"role": "user", "content": "Расширь и углуби разбор до 600-900 слов, сохранив стиль и структуру."}])
        else:
            text = _gen(temp=0.85)
    return text


_CJK = re.compile(r"[぀-ヿ一-鿿가-힯]")
_LATIN_WORD = re.compile(r"\b[A-Za-z]{4,}\b")


def _quality_bad(text):
    """True, если текст плохой: слишком короткий, иероглифы, много английских слов, мусор."""
    if len(text.split()) < 500:
        return True
    if _CJK.search(text):
        return True
    if len(_LATIN_WORD.findall(text)) > 8:   # допускаем немного (имена карт и т.п.)
        return True
    if re.search(r"[�]{1,}", text):
        return True
    return False


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
