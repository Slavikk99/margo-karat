# -*- coding: utf-8 -*-
"""MARGO KARAT Oracle — конфигурация (всё из .env)."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")   # запасной путь (общий .env агента)

# --- Telegram ---
CLIENT_BOT_TOKEN = os.getenv("CLIENT_BOT_TOKEN", os.getenv("MARGO_BOT_TOKEN", "")).strip()
ADMIN_BOT_TOKEN  = os.getenv("ADMIN_BOT_TOKEN", "").strip()
# Одноразовый код, чтобы «привязать» владельца к админ-боту (первый /start с этим кодом).
ADMIN_SETUP_CODE = os.getenv("ADMIN_SETUP_CODE", "margo-admin-2026").strip()

# --- Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "")

# --- AI (Groq по умолчанию: бесплатно, длинные тексты + vision) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
# Vision-модель (анализ фото ладони/гущи). llama-4-scout/maverick удалены Groq 17.06.2026 —
# если в .env остался старый вариант, принудительно используем актуальную qwen3.6-27b.
_DEPRECATED_VISION = {
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
}
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.6-27b").strip()
if GROQ_VISION_MODEL in _DEPRECATED_VISION or not GROQ_VISION_MODEL:
    GROQ_VISION_MODEL = "qwen/qwen3.6-27b"

# --- Реквизиты оплаты (ручная оплата) ---
PAYMENT_DETAILS = os.getenv("PAYMENT_DETAILS_OVERRIDE", (
    "💳 *Реквизиты для оплаты*\n\n"
    "🇩🇪 *Deutsche Bank (Европа)*\n"
    "IBAN: `DE77 3307 0024 0135 5908 05`\n\n"
    "🇺🇦 *Monobank (Украина)*\n"
    "Карта: `4874 0700 1782 1875`\n\n"
    "🪙 *USDT (сеть TRON / TRC-20)*\n"
    "Кошелёк: `TEACgeadY6kjsNANTn3z5oDfddwn9MVEkZ`\n\n"
    "После оплаты нажмите кнопку «✅ Я оплатил» — и укажите, каким способом заплатили."
))
# Путь к картинке с QR/реквизитами (например USDT-кошелёк). Если файла нет — шлётся только текст.
PAYMENT_QR_FILE = os.getenv("PAYMENT_QR_FILE", str(BASE_DIR / "payment_qr.jpg"))

# --- Цены (в тексте; оплата ручная) ---
PRICES = {
    "1 вопрос": "3.99 €",
    "3 направления": "4.99 €",
    "Полный пакет": "7.99 €",
    "Приватная консультация": "49.99 €",
}

# --- Опрос воркера ---
POLL_SEC = int(os.getenv("POLL_SEC", "20"))

# Ссылка на сайт-визитку «Знакомство с Маргаритой» (GitHub Pages)
BIO_URL = os.getenv("BIO_URL", "https://slavikk99.github.io/margo-karat/margo_bio.html")
