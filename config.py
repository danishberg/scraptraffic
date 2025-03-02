"""
config.py
Загружает TELEGRAM_BOT_TOKEN из .env (или системных переменных).
"""

import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
