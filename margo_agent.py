# -*- coding: utf-8 -*-
"""
MARGO KARAT — AI READING AGENT
Автономный агент персональных разборов. Работает в облаке 24/7.

Цикл (каждые 60 сек):
1. Берёт из Supabase оплаченные заказы со статусом «Новый заказ» или
   с пометкой regenerate → генерирует разборы (800–900+ слов на направление)
   → сохраняет черновик → статус «Ожидает проверки» → уведомляет Маргариту в Telegram.
2. Берёт одобренные заказы (approved_at старше DELAY_HOURS) → отправляет
   письмо клиенту от имени Маргариты → статус «Отправлен клиенту».

Архитектура: отдельный облачный воркер (вариант «separate cloud AI agent») —
надёжнее встроенного в сайт AI: не зависит от браузера клиента, ключи не
попадают во фронтенд, перезапускается systemd/Docker автоматически.

.env:
  SUPABASE_URL=...            (Settings → API)
  SUPABASE_SERVICE_KEY=...    (service_role — ТОЛЬКО на сервере!)
  GROQ_API_KEY=...            (мозг агента; можно заменить на другой LLM)
  GROQ_MODEL=llama-3.3-70b-versatile
  GROQ_VISION_MODEL=meta-llama/llama-4-scout-17b-16e-instruct   (анализ фото)
  SMTP_HOST=smtp.gmail.com    SMTP_PORT=465
  SMTP_USER=почта_маргариты   SMTP_PASS=пароль_приложения
  MARGO_BOT_TOKEN=...         ADMIN_CHAT_ID=...   (уведомления в Telegram)
  DELAY_HOURS=3.5             (пауза перед отправкой письма)
"""

import os
import ssl
import time
import base64
import random
import logging
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

import requests
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
BOT_TOKEN = os.getenv("MARGO_BOT_TOKEN", "")
ADMIN_CHAT = os.getenv("ADMIN_CHAT_ID", "")
DELAY_HOURS = float(os.getenv("DELAY_HOURS", "3.5"))
POLL_SEC = int(os.getenv("POLL_SEC", "60"))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(), logging.FileHandler("margo_agent.log", encoding="utf-8")],
)
log = logging.getLogger("margo-agent")
groq = Groq(api_key=GROQ_KEY) if GROQ_KEY else None

# ============================================================
# БАЗА ЗНАНИЙ (система знаний агента по направлениям)
# ============================================================
STYLE = (
    "Ты — Маргарита, мастер символических практик с более чем двадцатилетним опытом. "
    "Пишешь ПО-РУССКИ, тёплым, живым, человеческим языком — как личное письмо близкому человеку. "
    "Обращаешься к клиенту по имени, на «вы», мягко и уважительно. "
    "ЗАПРЕЩЕНО: роботизированные фразы, канцелярит, повторяющиеся конструкции, "
    "упоминание того, что ты ИИ, обещания гарантированных событий, медицинские/юридические/финансовые советы. "
    "Ответ — минимум 850 слов: подробный, эмоциональный, структурированный (абзацы, без markdown-заголовков), "
    "с плавным вступлением, глубоким разбором и тёплым завершением с рекомендациями для размышления."
)

KNOWLEDGE = {
    "Таро": (
        "ЗНАНИЯ ТАРО. Колода: 22 старших аркана (Шут—Мир) и 56 младших (Жезлы-огонь-действие, "
        "Кубки-вода-чувства, Мечи-воздух-мысли, Пентакли-земля-материя). Значения учитывают прямое/перевёрнутое "
        "положение, соседние карты и позицию в раскладе. Расклады: «Кельтский крест» (10 позиций), "
        "«Три карты» (прошлое-настоящее-будущее / ситуация-препятствие-совет), «Подкова». "
        "Метод: выбери подходящий вопросу расклад, СЛУЧАЙНО выбери карты (назови их), опиши символику каждой, "
        "затем свяжи карты в единый сюжет вокруг вопроса клиента. Комбинации карт важнее одиночных значений."
    ),
    "Каббала": (
        "ЗНАНИЯ КАББАЛЫ. Древо Сфирот: Кетер (венец, замысел), Хохма (мудрость), Бина (понимание), "
        "Хесед (милосердие), Гвура (строгость), Тиферет (гармония, сердце), Нецах (победа, стойкость), "
        "Ход (сияние, интеллект), Йесод (основа, подсознание), Малхут (царство, материальный мир). "
        "Гематрия: числовые значения букв иврита; число имени и даты рождения раскрывают духовные задачи. "
        "22 буквы алфавита связаны с путями Древа. Тиккун — исправление души, её урок в этом воплощении. "
        "Метод: вычисли символические числа из имени и даты рождения, соотнеси со сфирот и путями, "
        "раскрой духовную задачу, сильные стороны души и зону роста применительно к вопросу. "
        "Ссылайся на традиционную символику (без религиозных предписаний)."
    ),
    "Натальная карта": (
        "ЗНАНИЯ АСТРОЛОГИИ. По дате рождения определи знак Солнца (ядро личности) и опиши архетип. "
        "Символика планет: Луна-эмоции, Меркурий-ум и речь, Венера-любовь и ценности, Марс-энергия и воля, "
        "Юпитер-расширение и удача, Сатурн-уроки и дисциплина, Уран-перемены, Нептун-интуиция, Плутон-трансформация. "
        "Стихии (огонь/земля/воздух/вода) и качества (кардинальный/фиксированный/мутабельный). "
        "Числология даты рождения как дополнительный слой (число жизненного пути). "
        "Метод: разбери архетип личности, сильные стороны, кармические уроки и текущие символические циклы "
        "(возвраты Сатурна ~29 лет, циклы Юпитера ~12 лет) применительно к вопросу клиента."
    ),
    "Кофейная гуща": (
        "ЗНАНИЯ ТАССЕОГРАФИИ. Традиционные символы: птица-новости, дорога/линия-путь и перемены, "
        "кольцо-союз и обязательства, дерево-рост и род, гора-препятствие и амбиция, сердце-чувства, "
        "звезда-удача и предназначение, рыба-прибыль, ключ-открытие и решение, луна-интуиция, "
        "цветок-радость, мост-переход, глаз-внимание и защита, спираль-развитие. "
        "Зоны чашки: у края-ближайшее время, середина-настоящее, дно-глубинные корни ситуации; "
        "ручка-сам человек, символы возле неё касаются его лично. "
        "Метод: если есть фото — опиши увиденные образы; если фото нет — сообщи, что Маргарита сама "
        "приготовила символическую чашку для клиента, «увидь» 4–6 образов, свяжи их в единый рассказ вокруг вопроса."
    ),
    "Линии руки": (
        "ЗНАНИЯ ХИРОМАНТИИ. Основные линии: линия жизни (жизненная сила, перемены), линия головы (мышление), "
        "линия сердца (чувства, привязанности), линия судьбы (призвание, путь). "
        "Холмы: Венеры-любовь и энергия, Юпитера-амбиции, Сатурна-мудрость, Аполлона-творчество, "
        "Меркурия-общение, Луны-интуиция, Марса-воля. Форма руки: стихии огня/земли/воздуха/воды. "
        "Знаки: звёзды, кресты, острова, ветви, треугольники. "
        "Метод: если есть фото ладони — опиши видимые линии и их символику; если анализ фото недоступен, "
        "строй разбор на традиционной символике, связывая её с датой рождения и вопросом клиента."
    ),
    "Задать вопрос": (
        "РЕЖИМ ПРЯМОГО ВОПРОСА. Клиент задал личный вопрос. Ответь глубоко и развёрнуто, соединяя "
        "символику Таро (вытяни 3 карты и назови их) и натальные архетипы по дате рождения. "
        "Структура: личное обращение → отражение сути вопроса → символический анализ (карты + астрология) "
        "→ разбор вариантов развития → мягкие рекомендации для размышления → тёплое завершение."
    ),
}

# ============================================================
# SUPABASE REST
# ============================================================
def sb_headers():
    return {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}",
            "Content-Type": "application/json", "Prefer": "return=representation"}


def sb_select(table, params):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=sb_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def sb_update(table, row_id, payload):
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{table}", headers=sb_headers(),
                       params={"id": f"eq.{row_id}"}, json=payload, timeout=30)
    r.raise_for_status()


def storage_download(path):
    r = requests.get(f"{SUPABASE_URL}/storage/v1/object/uploads/{path}",
                     headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}, timeout=60)
    r.raise_for_status()
    return r.content


# ============================================================
# ГЕНЕРАЦИЯ
# ============================================================
def describe_image(img_bytes, what):
    """Анализ фото (ладонь / кофейная гуща) vision-моделью."""
    if not groq:
        return ""
    try:
        b64 = base64.b64encode(img_bytes).decode()
        resp = groq.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "text", "text":
                    f"Опиши по-русски это фото ({what}) для символического разбора: "
                    "видимые линии, формы, узоры, образы, их расположение. Подробно, 150-250 слов."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            temperature=0.6, max_tokens=800, timeout=120,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        log.warning("Vision недоступен (%s) — разбор без анализа фото", e)
        return ""


def generate_reading(order, direction, image_notes):
    """Один разбор по направлению, 850+ слов."""
    seed_cards = random.sample([
        "Шут","Маг","Верховная Жрица","Императрица","Император","Иерофант","Влюблённые",
        "Колесница","Сила","Отшельник","Колесо Фортуны","Справедливость","Повешенный",
        "Смерть","Умеренность","Дьявол","Башня","Звезда","Луна","Солнце","Суд","Мир"], 5)
    user_block = (
        f"ДАННЫЕ КЛИЕНТА:\nИмя: {order.get('customer_name','')} {order.get('customer_surname','') or ''}\n"
        f"Дата рождения: {order.get('birth_date','не указана')}\n"
        f"Пакет: {order.get('package','')}\n"
        f"Направление этого разбора: {direction}\n"
        f"Вопрос клиента: {order.get('question','')}\n"
    )
    if image_notes:
        user_block += f"\nОПИСАНИЕ ЗАГРУЖЕННОГО ФОТО:\n{image_notes}\n"
    if direction in ("Таро", "Задать вопрос"):
        user_block += f"\nСлучайно выпавшие карты (используй 3 из них): {', '.join(seed_cards)}\n"
    if direction == "Кофейная гуща" and not image_notes:
        mode = order.get("coffee_mode")
        user_block += ("\nФото гущи нет: Маргарита сама приготовила символическую чашку для клиента.\n"
                       if mode == "margo_draws" else "\nФото гущи отсутствует — создай символический образ чашки.\n")

    resp = groq.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": f"{STYLE}\n\n{KNOWLEDGE[direction]}"},
            {"role": "user", "content": user_block + "\nНапиши полный персональный разбор (минимум 850 слов)."},
        ],
        temperature=0.9, max_tokens=4096, timeout=300,
    )
    text = (resp.choices[0].message.content or "").strip()
    if len(text.split()) < 500:  # контроль качества: слишком коротко — дорасширить
        resp2 = groq.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": f"{STYLE}\n\n{KNOWLEDGE[direction]}"},
                {"role": "user", "content": user_block},
                {"role": "assistant", "content": text},
                {"role": "user", "content": "Расширь и углуби этот разбор до 850-1000 слов, сохранив стиль."},
            ],
            temperature=0.9, max_tokens=4096, timeout=300,
        )
        text = (resp2.choices[0].message.content or text).strip()
    return text


def process_order(order):
    oid = order["id"]
    name = f"{order.get('customer_name','')}"
    directions = order.get("directions") or []
    if isinstance(directions, str):
        directions = [directions]
    log.info("Заказ %s (%s): направления %s", oid, name, directions)
    sb_update("service_orders", oid, {"status": "В обработке"})

    # анализ фото
    notes = {}
    for path in order.get("uploaded_images") or []:
        what = "ладонь руки" if path.startswith("palm") else "кофейная гуща в чашке"
        key = "Линии руки" if path.startswith("palm") else "Кофейная гуща"
        try:
            notes[key] = describe_image(storage_download(path), what)
        except Exception:
            log.exception("Не удалось скачать/описать %s", path)

    sections = []
    for d in directions:
        if d not in KNOWLEDGE:
            continue
        body = generate_reading(order, d, notes.get(d, ""))
        title = d.upper() if d != "Задать вопрос" else "ОТВЕТ НА ВАШ ВОПРОС"
        sections.append(f"— {title} —\n\n{body}")
        log.info("  ✓ %s: %d слов", d, len(body.split()))

    full = "\n\n\n".join(sections)
    sb_update("service_orders", oid, {
        "generated_response": full,
        "status": "Ожидает проверки",
        "approval_status": "draft",
    })
    notify_admin(
        f"🔮 Черновик готов\nКлиент: {name} ({order.get('customer_email')})\n"
        f"Пакет: {order.get('package')}\nНаправления: {', '.join(directions)}\n"
        f"Объём: {len(full.split())} слов\n\nОткройте админ-панель для проверки и одобрения."
    )


# ============================================================
# EMAIL (от имени Маргариты)
# ============================================================
def send_email(to_addr, client_name, body_text):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header("Ваш персональный разбор — MARGO KARAT", "utf-8")
    msg["From"] = f"Маргарита · MARGO KARAT <{SMTP_USER}>"
    msg["To"] = to_addr
    text = (f"{body_text}\n\n—\nС теплом,\nМаргарита\nMARGO KARAT ✨\n\n"
            "Данный разбор носит развлекательно-познавательный характер и не заменяет "
            "профессиональную помощь в медицинских, юридических или финансовых вопросах.")
    html_body = "".join(f"<p>{p}</p>" for p in body_text.split("\n\n"))
    html = f"""<div style="max-width:640px;margin:0 auto;font-family:Georgia,serif;color:#2b2b2b;line-height:1.75">
<div style="text-align:center;padding:26px 0;border-bottom:2px solid #c9a24b">
<span style="font-size:22px;letter-spacing:6px;color:#8a6d2f">MARGO KARAT</span></div>
<div style="padding:28px 8px;font-size:16px">{html_body}
<p style="margin-top:32px">С теплом,<br><b>Маргарита</b> ✨</p></div>
<div style="border-top:1px solid #ddd;padding:14px 8px;font-size:11px;color:#999">
Разбор носит развлекательно-познавательный характер и не заменяет профессиональную помощь
в медицинских, юридических или финансовых вопросах.</div></div>"""
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=60) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, [to_addr], msg.as_string())


def deliver_approved():
    cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=DELAY_HOURS)).isoformat()
    rows = sb_select("service_orders", {
        "approval_status": "eq.approved", "email_sent": "eq.false",
        "approved_at": f"lt.{cutoff}", "select": "*", "limit": "5",
    })
    for o in rows:
        try:
            if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
                log.warning("SMTP не настроен — письмо для %s не отправлено", o["id"])
                continue
            send_email(o["customer_email"], o.get("customer_name", ""), o.get("generated_response", ""))
            sb_update("service_orders", o["id"], {
                "email_sent": True, "email_sent_at": dt.datetime.utcnow().isoformat(),
                "status": "Отправлен клиенту",
            })
            log.info("✉ Отправлено: %s → %s", o["id"], o["customer_email"])
            notify_admin(f"✉️ Разбор доставлен клиенту {o.get('customer_name','')} ({o['customer_email']})")
        except Exception:
            log.exception("Ошибка отправки письма %s", o["id"])
            sb_update("service_orders", o["id"], {"status": "Ошибка"})


def notify_admin(text):
    if not (BOT_TOKEN and ADMIN_CHAT):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": ADMIN_CHAT, "text": text}, timeout=15)
    except Exception:
        log.exception("Не удалось отправить уведомление в Telegram")


# ============================================================
# ГЛАВНЫЙ ЦИКЛ
# ============================================================
def main():
    if not (SUPABASE_URL and SERVICE_KEY):
        raise SystemExit("❌ Заполните SUPABASE_URL и SUPABASE_SERVICE_KEY в .env")
    if not groq:
        raise SystemExit("❌ Заполните GROQ_API_KEY в .env")
    log.info("MARGO KARAT AI Agent запущен. Опрос каждые %d сек.", POLL_SEC)
    while True:
        try:
            # новые оплаченные + отправленные на перегенерацию
            fresh = sb_select("service_orders", {
                "payment_status": "eq.оплачен", "status": "eq.Новый заказ",
                "select": "*", "limit": "3"})
            regen = sb_select("service_orders", {
                "approval_status": "eq.regenerate", "select": "*", "limit": "2"})
            for order in fresh + regen:
                try:
                    process_order(order)
                except Exception:
                    log.exception("Ошибка обработки заказа %s", order.get("id"))
                    sb_update("service_orders", order["id"], {"status": "Ошибка"})
                    notify_admin(f"⚠️ Ошибка генерации по заказу {order.get('id')}")
            deliver_approved()
        except Exception:
            log.exception("Ошибка цикла")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
