"""
PAGE: main.py
Main entry point for running the Telegram bot.
"""

import logging
import asyncio
import nest_asyncio
nest_asyncio.apply()

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters
)
from config import TELEGRAM_BOT_TOKEN
from db import init_db
from handlers import (
    start,
    sell_scrap_flow_handler,
    register_flow_handler,
    view_requests_handler,
    cancel,
    error_handler
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def main():
    # Initialize the database (creates tables if not exist)
    init_db()

    # Create the Application instance
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Add conversation handlers
    application.add_handler(register_flow_handler)
    application.add_handler(sell_scrap_flow_handler)
    application.add_handler(view_requests_handler)

    # Add /start command to show main menu
    application.add_handler(CommandHandler('start', start))
    # Add /cancel command to cancel any active conversation
    application.add_handler(CommandHandler('cancel', cancel))

    # Add error handler
    application.add_error_handler(error_handler)

    # Start the bot (this call blocks until you stop the bot)
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
