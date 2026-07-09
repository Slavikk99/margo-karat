@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === MARGO KARAT BOT ===
pip install -r requirements.txt --quiet
python margo_bot.py
pause
