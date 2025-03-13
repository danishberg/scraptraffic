# index.py
import logging
import asyncio
import nest_asyncio
import os
from aiohttp import web
from telegram.ext import ApplicationBuilder
from config import TELEGRAM_BOT_TOKEN, BEARER_TOKEN  # Предполагается, что значения заданы в .env
from db import init_db
from handlers import main_flow_handler, error_handler, get_users_for_notification, get_telegram_id_by_user_id

nest_asyncio.apply()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app_telegram = None

async def start_bot():
    global app_telegram
    logger.info("Инициализация базы данных...")
    init_db()

    logger.info("Создание приложения Telegram...")
    app_telegram = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app_telegram.add_handler(main_flow_handler)
    app_telegram.add_error_handler(error_handler)

    logger.info("Запуск Telegram-бота. Нажмите Ctrl+C для остановки.")
    await app_telegram.run_polling()

async def handle_new_order(request: web.Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth.split(" ")[1] != BEARER_TOKEN:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception as e:
        logger.error(f"Ошибка при разборе JSON: {e}")
        return web.json_response({"error": "Invalid JSON"}, status=400)

    new_order = {
        "type": "новая заявка",
        "material": data.get("text_material", "не указан"),
        "quantity": data.get("text_volume", "не указано"),
        "city": data.get("text_city", "не указан"),
        "info": data.get("comment", "не указана")
    }

    notification_text = (
        f"🔔 <b>Новая заявка</b>\n"
        f"Тип: {new_order['type']}\n"
        f"Материал: {new_order['material']}\n"
        f"Количество: {new_order['quantity']}\n"
        f"Город: {new_order['city']}\n"
        f"Доп. инфо: {new_order['info']}\n\n"
        "Для просмотра откройте меню бота."
    )

    matching_user_ids = get_users_for_notification(new_order["material"], new_order["city"])
    for uid in matching_user_ids:
        tg_id = get_telegram_id_by_user_id(uid)
        if tg_id:
            try:
                await app_telegram.bot.send_message(
                    chat_id=tg_id,
                    text=notification_text,
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Failed to send notification to user_id={uid} (tg_id={tg_id}): {e}")

    return web.json_response({"status": "ok"})

async def start_webserver():
    web_app = web.Application()
    web_app.router.add_post("/new_order", handle_new_order)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    logger.info("Запуск веб-сервера на порту 8080.")


    await site.start()

async def main():
    await asyncio.gather(start_webserver(), start_bot())

if __name__ == '__main__':
    asyncio.run(main())
1599