# index.py
import logging
import asyncio
import nest_asyncio
import os
from aiohttp import web
from telegram.ext import ApplicationBuilder
from config import TELEGRAM_BOT_TOKEN, BEARER_TOKEN
from db import init_db
from handlers import (
    main_flow_handler,
    error_handler,
    get_users_for_notification,
    get_telegram_id_by_user_id,
    fetch_materials_and_cities
)
from payment_store import valid_payment_hashes, payment_links, generate_unique_hash

nest_asyncio.apply()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# hide httpx “POST /getUpdates → HTTP/1.1 200 OK” spam
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("h11").setLevel(logging.WARNING)

app_telegram = None

async def start_bot():
    global app_telegram
    logger.info("Инициализация базы данных...")
    init_db()

    logger.info("Создание приложения Telegram...")
    app_telegram = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Delete existing webhook before starting polling
    try:
        await app_telegram.bot.delete_webhook()
        logger.info("Webhook deleted, switching to polling mode.")
    except Exception as e:
        logger.error("Error deleting webhook: %s", e)
    
    # Add handlers once after webhook deletion
    app_telegram.add_handler(main_flow_handler)
    app_telegram.add_error_handler(error_handler)
    
    # (Optional) Pre-populate a test payment hash for testing
    test_hash = "TEST123"
    valid_payment_hashes[test_hash] = True
    payment_links[test_hash] = "TEST_TELEGRAM_ID"
    logger.info("Inserted test payment hash: %s", test_hash)
    logger.info("Current valid_payment_hashes: %s", valid_payment_hashes)

    logger.info("Запуск Telegram-бота. Нажмите Ctrl+C для остановки.")
    await app_telegram.run_polling()


async def handle_new_order(request: web.Request):
    logger.info("handle_new_order called")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth.split(" ")[1] != BEARER_TOKEN:
        logger.warning("Unauthorized in handle_new_order")
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        data = await request.json()
        logger.info("handle_new_order received JSON: %s", data)
    except Exception as e:
        logger.error("Ошибка при разборе JSON: %s", e)
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
    logger.info("handle_new_order matching_user_ids: %s", matching_user_ids)

    for uid in matching_user_ids:
        tg_id = get_telegram_id_by_user_id(uid)
        if tg_id:
            try:
                logger.info("Sending new_order notification to tg_id=%s", tg_id)
                await app_telegram.bot.send_message(chat_id=tg_id, text=notification_text, parse_mode='HTML')
            except Exception as e:
                logger.error("Failed to send notification to user_id=%s (tg_id=%s): %s", uid, tg_id, e)

    return web.json_response({"status": "ok"})


async def verify_payment_link(request: web.Request):
    logger.info("verify_payment_link called, query=%s", request.query)
    logger.info("Headers: %s", request.headers)
    auth = request.headers.get("Authorization", "")
    logger.info("Authorization header: %s", auth)
    if not auth.startswith("Bearer ") or auth.split(" ")[1] != BEARER_TOKEN:
        logger.warning("Unauthorized in verify_payment_link")
        return web.json_response({"error": "Unauthorized"}, status=401)
    unique_hash = request.query.get("id")
    if not unique_hash:
        logger.warning("Missing id param in verify_payment_link")
        return web.json_response({"error": "Missing id parameter"}, status=400)
    logger.info("verify_payment_link unique_hash=%s", unique_hash)
    logger.info("Current valid_payment_hashes: %s", valid_payment_hashes)
    if unique_hash in valid_payment_hashes:
        logger.info("verify_payment_link found valid hash=%s", unique_hash)
        return web.json_response({"status": "verified", "id": unique_hash})
    else:
        logger.warning("verify_payment_link invalid hash=%s", unique_hash)
        return web.json_response({"error": "Invalid payment link"}, status=400)


async def handle_payment_notification(request: web.Request):
    logger.info("handle_payment_notification called")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth.split(" ")[1] != BEARER_TOKEN:
        logger.warning("Unauthorized in handle_payment_notification")
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        data = await request.json()
        logger.info("handle_payment_notification received JSON: %s", data)
    except Exception as e:
        logger.error("Error parsing JSON: %s", e)
        return web.json_response({"error": "Invalid JSON"}, status=400)
    unique_hash = data.get("id")
    if not unique_hash:
        logger.warning("Missing id param in handle_payment_notification")
        return web.json_response({"error": "Missing id parameter"}, status=400)
    logger.info("handle_payment_notification unique_hash=%s", unique_hash)
    if unique_hash in valid_payment_hashes:
        tg_id = payment_links.get(unique_hash)
        logger.info("Payment link is valid, tg_id=%s", tg_id)
        if tg_id and app_telegram:
            try:
                logger.info("Notifying Telegram user about payment success, tg_id=%s", tg_id)
                await app_telegram.bot.send_message(
                    chat_id=tg_id,
                    text=f"✅ Оплата для {unique_hash} получена.",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error("Failed to notify Telegram for payment %s: %s", unique_hash, e)
        del valid_payment_hashes[unique_hash]
        if unique_hash in payment_links:
            del payment_links[unique_hash]
        return web.json_response({"status": "payment notified", "id": unique_hash})
    else:
        logger.warning("Invalid payment link in handle_payment_notification: %s", unique_hash)
        return web.json_response({"error": "Invalid payment link"}, status=400)


async def handle_test_materials_cities(request: web.Request):
    logger.info("handle_test_materials_cities called")
    data = await fetch_materials_and_cities()
    logger.info("Test endpoint fetched materials and cities: %s", data)
    return web.json_response(data)


async def start_webserver():
    web_app = web.Application()
    web_app.router.add_post("/new_order", handle_new_order)
    web_app.router.add_get("/bot-payment-test", verify_payment_link)
    web_app.router.add_post("/payment-notification", handle_payment_notification)
    web_app.router.add_get("/test/materials_cities", handle_test_materials_cities)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 5002)
    logger.info("Запуск веб-сервера на порту 5002.")
    await site.start()


async def main():
    await asyncio.gather(start_webserver(), start_bot())


if __name__ == "__main__":
    asyncio.run(main())
