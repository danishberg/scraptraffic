"""
index.py
Главная точка входа для запуска Telegram-бота.
"""

import logging
import asyncio
import nest_asyncio

from telegram.ext import ApplicationBuilder
from config import TELEGRAM_BOT_TOKEN  # Предполагается, что config.py существует
from db import init_db
from handlers import main_flow_handler, error_handler

nest_asyncio.apply()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("Инициализация базы данных...")
    init_db()

    logger.info("Создание приложения...")
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    logger.info("Добавляем ConversationHandler...")
    application.add_handler(main_flow_handler)

    logger.info("Добавляем обработчик ошибок...")
    application.add_error_handler(error_handler)

    logger.info("Запуск бота. Нажмите Ctrl+C для остановки.")
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
