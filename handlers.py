import logging
import json
import aiohttp
import os
import asyncio
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler
)
from config import BEARER_TOKEN
from db import (
    init_db,
    add_user,
    get_user_by_telegram_id,
    delete_user_by_telegram_id,
    add_request,
    init_notification_items_for_user,
    get_notification_items,
    toggle_notification_item_by_id,
    get_users_for_notification,
    get_telegram_id_by_user_id,
    get_all_requests,
    get_connection
)
from payment_store import generate_unique_hash, valid_payment_hashes, payment_links

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MAIN_MENU, REQUEST_INPUT, SEARCH_INPUT = range(3)

def build_main_menu():
    keyboard = [
        [InlineKeyboardButton("🚀 Активировать Pro‑аккаунт", callback_data="menu_pro")],
        [InlineKeyboardButton("🔔 Настроить уведомления", callback_data="menu_notifications")],
        [InlineKeyboardButton("📝 Разместить заявку", callback_data="menu_create_request")],
        [InlineKeyboardButton("🚪 Выйти из аккаунта", callback_data="menu_logout")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_request_summary(user_data):
    req = user_data.get("request", {})
    text = (
        "<b>📋 Ваша заявка:</b>\n"
        f"Тип: {req.get('type', 'не указан')}\n"
        f"Материал: {req.get('material', 'не указан')}\n"
        f"Количество: {req.get('quantity', 'не указано')}\n"
        f"Город: {req.get('city', 'не указан')}\n"
        f"Доп. информация: {req.get('info', 'не указана')}\n"
    )
    return text

def build_request_keyboard(user_data):
    req = user_data.get("request", {})
    type_btn = "Изменить тип заявки" if req.get("type", "не указан") != "не указан" else "Указать тип заявки"
    mat_btn = "Изменить материал" if req.get("material", "не указан") != "не указан" else "Указать материал"
    qty_btn = "Изменить количество" if req.get("quantity", "не указано") != "не указано" else "Указать количество"
    city_btn = "Изменить город" if req.get("city", "не указан") != "не указан" else "Указать город"
    info_btn = "Изменить доп. информацию" if req.get("info", "не указана") != "не указана" else "Указать доп. информацию"
    keyboard = [
        [InlineKeyboardButton(f"🔄 {type_btn}", callback_data="req_type")],
        [InlineKeyboardButton(f"🔄 {mat_btn}", callback_data="req_material")],
        [InlineKeyboardButton(f"🔄 {qty_btn}", callback_data="req_quantity")],
        [InlineKeyboardButton(f"🔄 {city_btn}", callback_data="req_city")],
        [InlineKeyboardButton(f"🔄 {info_btn}", callback_data="req_info")],
        [InlineKeyboardButton("✅ Подтвердить заявку", callback_data="req_confirm")],
        [InlineKeyboardButton("🔙 Назад", callback_data="req_back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_notifications_menu():
    keyboard = [
        [InlineKeyboardButton("Настроить материалы", callback_data="notif_materials")],
        [InlineKeyboardButton("Настроить города", callback_data="notif_cities")],
        [InlineKeyboardButton("🔍 Посмотреть все заявки", callback_data="notif_view_requests")],
        [InlineKeyboardButton("🔙 Назад", callback_data="notif_back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def fetch_materials_and_cities():
    logger.info("fetch_materials_and_cities called")
    url = "https://scraptraffic.com/api/telegram_bot_external/materials_and_cities"
    headers = {"Authorization": f"Bearer {BEARER_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            raw_data = await resp.json()
            logger.info("Raw materials_and_cities data: %s", raw_data)
            materials_list = [m["title"] for m in raw_data.get("materials", [])
                              if isinstance(m, dict) and "title" in m]
            cities_list = [c["title"] for c in raw_data.get("cities", [])
                           if isinstance(c, dict) and "title" in c]
            result = {"materials": materials_list, "cities": cities_list}
            logger.info("Transformed materials_and_cities: %s", result)
            return result

def add_notification_item(user_id: int, filter_type: str, value: str):
    logger.info("add_notification_item user_id=%s filter_type=%s value=%s", user_id, filter_type, value)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO notification_filters (user_id, filter_type, value, is_enabled) VALUES (?, ?, ?, ?)",
        (user_id, filter_type, value, 1)
    )
    conn.commit()
    conn.close()

async def build_filter_keyboard(user_id, filter_type, page=1):
    data = await fetch_materials_and_cities()
    key = "materials" if filter_type == "material" else "cities"
    items = data.get(key, [])
    logger.info("build_filter_keyboard: user_id=%s filter_type=%s items_count=%s", user_id, filter_type, len(items))
    items_per_page = 15
    start = (page - 1) * items_per_page
    end = start + items_per_page
    subitems = items[start:end]
    db_items = get_notification_items(user_id, filter_type)
    db_dict = {val: (fid, is_enabled) for fid, val, is_enabled in db_items}
    keyboard = []
    for item in subitems:
        if item in db_dict:
            fid, is_enabled = db_dict[item]
            icon = "✅" if is_enabled == 1 else "❌"
            data_cb = f"tn|{fid}|{page}|{filter_type}"
        else:
            icon = "❌"
            data_cb = f"add_filter|{item}|{page}|{filter_type}"
        button_text = f"{icon} {item}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=data_cb)])
    nav_btns = []
    if page > 1:
        nav_btns.append(InlineKeyboardButton("⬅️", callback_data=f"ln|{filter_type}|{page-1}"))
    if end < len(items):
        nav_btns.append(InlineKeyboardButton("➡️", callback_data=f"ln|{filter_type}|{page+1}"))
    if nav_btns:
        keyboard.append(nav_btns)
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="notif_back")])
    return InlineKeyboardMarkup(keyboard)

async def fetch_orders():
    logger.info("fetch_orders called")
    orders_url = "https://scraptraffic.com/api/telegram_bot_external/orders"
    headers = {"Authorization": f"Bearer {BEARER_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(orders_url, headers=headers) as resp:
            data = await resp.json()
            logger.info("fetch_orders received %s items", len(data))
            return data

async def post_new_order(order_data: dict) -> dict:
    logger.info("post_new_order called with: %s", order_data)
    url = "https://scraptraffic.com/api/telegram_bot_external/emulate_new_order"
    headers = {"Authorization": f"Bearer {BEARER_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=order_data) as resp:
            try:
                data = await resp.json()
                logger.info("post_new_order success: %s", data)
            except Exception as e:
                text = await resp.text()
                logger.error(f"Error decoding JSON: {e}. Response text: {text}")
                data = {"error": "Не удалось создать заявку"}
            return data

async def build_requests_page_text(search, page, page_size=10):
    logger.info("build_requests_page_text called, search=%s page=%s", search, page)
    orders = await fetch_orders()
    transformed = []
    for order in orders:
        transformed.append((
            order.get("order_id"),
            "Новая заявка",
            order.get("text_material", ""),
            order.get("text_volume", ""),
            order.get("text_city", ""),
            order.get("comment", ""),
            order.get("date", "")
        ))
    if search:
        search_lower = search.lower()
        filtered = [r for r in transformed if (search_lower in str(r[0]).lower() or
                                               search_lower in str(r[1]).lower() or
                                               search_lower in (r[2] or "").lower() or
                                               search_lower in (r[3] or "").lower() or
                                               search_lower in (r[4] or "").lower() or
                                               search_lower in (r[5] or "").lower() or
                                               search_lower in (r[6] or "").lower())]
    else:
        filtered = transformed
    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_reqs = filtered[start:end]
    text = "<b>Все заявки:</b>\n\n" + format_requests_list(page_reqs)
    has_prev = page > 1
    has_next = end < total
    logger.info("build_requests_page_text: total=%s, page_reqs=%s", total, len(page_reqs))
    return text, has_prev, has_next

def build_requests_page_keyboard(page, has_prev, has_next, search):
    logger.info("build_requests_page_keyboard: page=%s has_prev=%s has_next=%s search=%s", page, has_prev, has_next, search)
    buttons = []
    nav_buttons = []
    if has_prev:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"view_req|{page-1}|{search}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"view_req|{page+1}|{search}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton("🔍 Поиск", callback_data="view_req_search")])
    buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="notif_back")])
    return InlineKeyboardMarkup(buttons)

def format_requests_list(requests):
    if not requests:
        return "Заявок пока нет."
    lines = []
    for r in requests:
        (req_id, req_type, material, qty, city, info, created_at) = r
        line = (
            f"#{req_id}: [{req_type}] {material}, {qty}, {city}\n"
            f"   Доп: {info}\n"
            f"   Дата: {created_at}\n"
        )
        lines.append(line)
    return "\n".join(lines)

async def notify_users_about_new_request(context: ContextTypes.DEFAULT_TYPE, creator_user_id: int, req: dict):
    logger.info("notify_users_about_new_request called for user_id=%s req=%s", creator_user_id, req)
    material = req["material"]
    city = req["city"]
    matching_user_ids = get_users_for_notification(material, city)
    notification_text = (
        f"🔔 <b>Новая заявка</b>\n"
        f"Тип: {req['type']}\n"
        f"Материал: {material}\n"
        f"Количество: {req['quantity']}\n"
        f"Город: {city}\n"
        f"Доп. инфо: {req['info']}\n"
        "\n"
        "Для просмотра откройте меню бота."
    )
    for uid in matching_user_ids:
        if uid == creator_user_id:
            continue
        tg_id = get_telegram_id_by_user_id(uid)
        if tg_id:
            try:
                logger.info("Sending new request notification to tg_id=%s", tg_id)
                await context.bot.send_message(chat_id=tg_id, text=notification_text, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Failed to send notification to user_id=%s (tg_id={tg_id}): {e}")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    logger.info("cmd_start called by user_id=%s, username=%s", user.id, user.username)
    add_user(user.id, user.username)
    row = get_user_by_telegram_id(user.id)
    if row:
        user_id = row[0]
        init_notification_items_for_user(user_id)
    if update.message:
        await update.message.reply_text("⏳ Пожалуйста, подождите...", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text(
            "📋 Главное меню: выберите действие.",
            reply_markup=build_main_menu(),
            parse_mode='HTML'
        )
    return MAIN_MENU

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data

    # Immediately handle the "req_back_main" callback data.
    if data == "req_back_main":
        try:
            await query.message.delete()
        except Exception as e:
            logger.error(f"Error deleting message in req_back_main: {e}")
        await query.answer()  # answer the callback query
        # Send the main menu message
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="📋 Главное меню: выберите действие.",
            reply_markup=build_main_menu(),
            parse_mode='HTML'
        )
        return MAIN_MENU


    user = query.from_user
    logger.info("main_menu_callback: user_id=%s data=%s", user.id, data)
    user_row = get_user_by_telegram_id(user.id)
    if not user_row:
        await query.answer("Вы не зарегистрированы. Введите /start.", show_alert=True)
        return ConversationHandler.END
    user_id = user_row[0]

    if data == "menu_pro":
        unique_hash = generate_unique_hash()
        valid_payment_hashes[unique_hash] = True
        payment_links[unique_hash] = user.id
        payment_link = f"https://scraptraffic.com/team/bot-payment-test?id={unique_hash}"
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Оплатить", url=payment_link),
                InlineKeyboardButton("Оплатить (симуляция)", callback_data=f"pay_now|{unique_hash}")
            ],
            [
                InlineKeyboardButton("🔙 Назад в меню", callback_data="notif_back_main")
            ]
        ])
        logger.info("User %s generated payment link with hash=%s", user.id, unique_hash)
        try:
            await query.message.edit_text(
                "Для активации Pro‑аккаунта:\n\n"
                f"1. Нажмите кнопку «Оплатить» ниже или используйте «Оплатить (симуляция)» для имитации платежа.\n"
                f"2. Завершите оплату на открывшейся странице (если нажали «Оплатить»)\n"
                f"3. По завершении, нажмите кнопку «Проверить оплату» или ждите уведомления.",
                reply_markup=kb,
                parse_mode='HTML'
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                logger.info("Message not modified, skipping update.")
            else:
                logger.error(f"edit_text error: {e}")
        await query.answer()
        return MAIN_MENU

    elif data.startswith("pay_now|"):
        parts = data.split("|", 1)
        if len(parts) == 2:
            unique_hash = parts[1]
            logger.info("Simulated payment initiated for hash=%s by user %s", unique_hash, user.id)
            if unique_hash in valid_payment_hashes:
                del valid_payment_hashes[unique_hash]
            if unique_hash in payment_links:
                del payment_links[unique_hash]
            try:
                await query.message.edit_text(
                    "✅ Оплата за Pro‑аккаунт получена! (симуляция)",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад в меню", callback_data="notif_back_main")]
                    ])
                )
            except Exception as e:
                logger.error(f"edit_text error in pay_now: {e}")
            await query.answer()
            return MAIN_MENU
        else:
            await query.answer("Непонятный формат callback для оплаты.", show_alert=True)
            return MAIN_MENU

    elif data == "menu_notifications":
        try:
            await query.message.edit_text(
                "🔔 Вы можете отфильтровать получение заявок по материалам и городам.\n"
                "По умолчанию уведомления приходят по всем материалам и городам.",
                reply_markup=build_notifications_menu(),
                parse_mode='HTML'
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                logger.info("Message not modified, skipping update.")
            else:
                logger.error(f"edit_text error: {e}")
        await query.answer()
        return MAIN_MENU

    elif data == "menu_create_request":
        if "request" not in context.user_data:
            context.user_data["request"] = {
                "type": "не указан",
                "material": "не указан",
                "quantity": "не указано",
                "city": "не указан",
                "info": "не указана"
            }
        summary = build_request_summary(context.user_data)
        kb = build_request_keyboard(context.user_data)
        try:
            await query.message.edit_text(summary, reply_markup=kb, parse_mode='HTML')
        except Exception as e:
            logger.error(f"edit_text error: {e}")
        await query.answer()
        return MAIN_MENU

    elif data == "menu_logout":
        delete_user_by_telegram_id(user.id)
        await query.message.delete()
        await query.answer()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🚪 Вы вышли из аккаунта. Введите /start, чтобы зарегистрироваться заново.",
            parse_mode='HTML'
        )
        return ConversationHandler.END

    elif data == "notif_back_main":
        await query.message.delete()
        await query.answer()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="📋 Главное меню: выберите действие.",
            reply_markup=build_main_menu(),
            parse_mode='HTML'
        )
        return MAIN_MENU

    elif data == "notif_materials":
        items_kb = await build_filter_keyboard(user_id, "material", page=1)
        try:
            await query.message.edit_text(
                "🔔 Настройка материалов:",
                reply_markup=items_kb,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"edit_text error: {e}")
        await query.answer()
        return MAIN_MENU

    elif data == "notif_cities":
        items_kb = await build_filter_keyboard(user_id, "city", page=1)
        try:
            await query.message.edit_text(
                "🔔 Настройка городов:",
                reply_markup=items_kb,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"edit_text error: {e}")
        await query.answer()
        return MAIN_MENU

    elif data == "notif_view_requests":
        text, has_prev, has_next = await build_requests_page_text("", 1)
        kb = build_requests_page_keyboard(1, has_prev, has_next, "")
        try:
            await query.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        except Exception as e:
            logger.error(f"edit_text error: {e}")
        await query.answer()
        return MAIN_MENU

    elif data.startswith("view_req|"):
        parts = data.split("|")
        if len(parts) == 3:
            try:
                page = int(parts[1])
            except ValueError:
                page = 1
            search = parts[2]
            text, has_prev, has_next = await build_requests_page_text(search, page)
            kb = build_requests_page_keyboard(page, has_prev, has_next, search)
            try:
                await query.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            except Exception as e:
                logger.error(f"edit_text error: {e}")
            await query.answer()
            return MAIN_MENU
        else:
            await query.answer("Непонятный формат запроса.", show_alert=True)
            return MAIN_MENU

    elif data == "view_req_search":
        try:
            await query.message.edit_text(
                "Введите текст для поиска по заявкам:",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"edit_text error: {e}")
        await query.answer()
        return SEARCH_INPUT

    elif data == "notif_back":
        try:
            await query.message.edit_text(
                "🔔 Вы можете отфильтровать получение заявок по материалам и городам.\n"
                "По умолчанию уведомления приходят по всем материалам и городам.",
                reply_markup=build_notifications_menu(),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"edit_text error: {e}")
        await query.answer()
        return MAIN_MENU

    elif data.startswith("req_"):
        if data == "req_set_type_selling":
            context.user_data["request"]["type"] = "продажа"
            summary = build_request_summary(context.user_data)
            kb = build_request_keyboard(context.user_data)
            try:
                await query.message.edit_text(summary, reply_markup=kb, parse_mode='HTML')
            except Exception as e:
                logger.error(f"edit_text error: {e}")
            await query.answer()
            return MAIN_MENU

        elif data == "req_set_type_buying":
            context.user_data["request"]["type"] = "закупка"
            summary = build_request_summary(context.user_data)
            kb = build_request_keyboard(context.user_data)
            try:
                await query.message.edit_text(summary, reply_markup=kb, parse_mode='HTML')
            except Exception as e:
                logger.error(f"edit_text error: {e}")
            await query.answer()
            return MAIN_MENU

        elif data == "req_really_confirm":
            req = context.user_data["request"]
            try:
                await post_new_order({
                    "type": req["type"],
                    "material": req["material"],
                    "quantity": req["quantity"],
                    "city": req["city"],
                    "info": req["info"]
                })
            except Exception as e:
                logger.error(f"Failed to post new order: {e}")
                await query.answer("Ошибка при создании заявки.", show_alert=True)
                return MAIN_MENU
            await notify_users_about_new_request(context, user_id, req)
            context.user_data["request"] = {
                "type": "не указан",
                "material": "не указан",
                "quantity": "не указано",
                "city": "не указан",
                "info": "не указана"
            }
            try:
                await query.message.edit_text("✅ Заявка успешно создана!", parse_mode='HTML')
            except Exception as e:
                logger.error(f"edit_text error: {e}")
            await query.answer()
            await query.message.reply_text(
                "📋 Главное меню: выберите действие.",
                reply_markup=build_main_menu(),
                parse_mode='HTML'
            )
            return MAIN_MENU

        elif data.split("_")[1] == "back":
            await query.message.delete()
            await query.bot.send_message(
                chat_id=query.message.chat_id,
                text="📋 Главное меню: выберите действие.",
                reply_markup=build_main_menu(),
                parse_mode='HTML'
            )
            await query.answer()
            return MAIN_MENU

        elif data.split("_")[1] == "type":
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("💰 Я продаю", callback_data="req_set_type_selling"),
                    InlineKeyboardButton("🛒 Я покупаю", callback_data="req_set_type_buying")
                ],
                [InlineKeyboardButton("🔙 Отмена", callback_data="req_cancel_field")]
            ])
            try:
                await query.message.edit_text("Выберите тип заявки:", reply_markup=kb, parse_mode='HTML')
            except Exception as e:
                logger.error(f"edit_text error: {e}")
            await query.answer()
            return MAIN_MENU

        elif data.split("_")[1] in ["material", "quantity", "city", "info"]:
            context.user_data["awaiting_field"] = data.split("_")[1]
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="req_cancel_field")]])
            try:
                await query.message.edit_text(
                    f"Введите значение для <b>{context.user_data['awaiting_field']}</b>:\nОтправьте текст сообщением в чат.",
                    reply_markup=kb,
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"edit_text error: {e}")
            await query.answer()
            return REQUEST_INPUT

        elif data.split("_")[1] == "confirm":
            req = context.user_data["request"]
            if (req.get("type") == "не указан" or
                req.get("material") == "не указан" or
                req.get("quantity") == "не указано" or
                req.get("city") == "не указан"):
                await query.answer("⚠️ Заполните обязательные поля (тип, материал, количество, город).", show_alert=True)
                return MAIN_MENU
            else:
                summary = build_request_summary(context.user_data)
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Разместить заявку", callback_data="req_really_confirm")],
                    [InlineKeyboardButton("🔄 Вернуться к редактированию", callback_data="req_no_confirm")]
                ])
                try:
                    await query.message.edit_text(
                        f"{summary}\nПодтверждаете размещение заявки?",
                        reply_markup=kb,
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"edit_text error: {e}")
                await query.answer()
                return MAIN_MENU

        else:
            await query.answer("Непонятная команда req_.", show_alert=True)
            return MAIN_MENU

    elif data.startswith("add_filter|"):
        parts = data.split("|", 3)
        if len(parts) == 4:
            _, value, page_str, filter_type = parts
            try:
                page = int(page_str)
            except:
                page = 1
            add_notification_item(user_id, filter_type, value)
            new_kb = await build_filter_keyboard(user_id, filter_type, page)
            try:
                await query.message.edit_reply_markup(new_kb)
            except Exception as e:
                logger.error(f"edit_reply_markup error: {e}")
            await query.answer(f"Фильтр '{value}' добавлен.")
            return MAIN_MENU
        else:
            await query.answer("Непонятный формат callback_data (add_filter|).", show_alert=True)
            return MAIN_MENU

    elif data.startswith("tn|"):
        parts = data.split("|")
        if len(parts) == 4:
            _, filter_id_str, page_str, filter_type = parts
            filter_id = int(filter_id_str)
            page = int(page_str)
            toggle_notification_item_by_id(user_id, filter_id)
            new_kb = await build_filter_keyboard(user_id, filter_type, page)
            try:
                await query.message.edit_reply_markup(new_kb)
            except Exception as e:
                logger.error(f"edit_reply_markup error: {e}")
            await query.answer()
            return MAIN_MENU
        else:
            await query.answer("Непонятный формат callback_data (tn|).", show_alert=True)
            return MAIN_MENU

    elif data.startswith("ln|"):
        parts = data.split("|")
        if len(parts) == 3:
            _, filter_type, page_str = parts
            try:
                page = int(page_str)
            except:
                page = 1
            new_kb = await build_filter_keyboard(user_id, filter_type, page)
            try:
                await query.message.edit_reply_markup(new_kb)
            except Exception as e:
                logger.error(f"edit_reply_markup error: {e}")
            await query.answer()
            return MAIN_MENU
        else:
            await query.answer("Непонятный формат callback_data (ln|).", show_alert=True)
            return MAIN_MENU

    else:
        await query.answer("Непонятная команда.", show_alert=True)
        return MAIN_MENU

async def text_fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("text_fallback_handler called with text=%s", update.message.text)
    text = update.message.text.strip().lower()
    if text == "test notifications":
        await update.message.reply_text(
            "🔔 Пример уведомления:\nНовая заявка: Город Алматы, Материал Медь, Количество 5 тонн.\nНажмите /view_123 для просмотра.",
            parse_mode='HTML'
        )
        return MAIN_MENU
    else:
        await update.message.reply_text("⚠️ Непонятная команда. Попробуйте ещё раз.", parse_mode='HTML')
        return MAIN_MENU

async def request_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("request_field_input called with text=%s", update.message.text)
    text = update.message.text.strip()
    field = context.user_data.get("awaiting_field")
    if not field:
        await update.message.reply_text("⚠️ Нет поля для ввода. Попробуйте ещё раз.", parse_mode='HTML')
        return MAIN_MENU
    req = context.user_data.setdefault("request", {})
    if field == "material":
        req["material"] = text
    elif field == "quantity":
        req["quantity"] = text
    elif field == "city":
        req["city"] = text
    elif field == "info":
        req["info"] = text
    context.user_data["awaiting_field"] = None
    summary = build_request_summary(context.user_data)
    kb = build_request_keyboard(context.user_data)
    await update.message.reply_text(summary, reply_markup=kb, parse_mode='HTML')
    return MAIN_MENU

async def search_requests_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("search_requests_input called with text=%s", update.message.text)
    search_query = update.message.text.strip()
    text, has_prev, has_next = await build_requests_page_text(search_query, 1)
    kb = build_requests_page_keyboard(1, has_prev, has_next, search_query)
    await update.message.reply_text(text, reply_markup=kb, parse_mode='HTML')
    return MAIN_MENU

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error('Update "%s" caused error "%s"', update, context.error)

main_flow_handler = ConversationHandler(
    entry_points=[CommandHandler('start', cmd_start)],
    states={
        MAIN_MENU: [
            CallbackQueryHandler(main_menu_callback),
            MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback_handler),
        ],
        REQUEST_INPUT: [
            CallbackQueryHandler(main_menu_callback),
            MessageHandler(filters.TEXT & ~filters.COMMAND, request_field_input),
        ],
        SEARCH_INPUT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, search_requests_input)
        ]
    },
    fallbacks=[CommandHandler('cancel', cmd_start)],
    name="main_flow"
)

from telegram.ext import Application
async def run_bot():
    init_db()
    app = Application.builder().token("YOUR_BOT_TOKEN_HERE").build()
    app.add_handler(main_flow_handler)
    app.add_error_handler(error_handler)
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_bot())
