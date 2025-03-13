# handlers.py
# Основная логика бота в едином потоке.
# Без аудио, с выбором (Все заявки / По фильтру),
# и "Сброс аккаунта (Logout)" в главном меню.
import logging
import json
import aiohttp
import os
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

# Import all DB-related functions from db.py
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
    get_all_requests
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MAIN_MENU, REQUEST_INPUT, SEARCH_INPUT = range(3)

# ---------------------------------------------------------------------------
# ПОМОГАЮЩИЕ ФУНКЦИИ
# ---------------------------------------------------------------------------
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

def build_notification_list_keyboard(user_id, filter_type, page=1):
    """
    Показываем по 15 штук за раз. (id, value, is_enabled).
    Instead of storing the entire JSON with Cyrillic in callback_data,
    we store a short code: "tn|<filter_id>|<page>".
    """
    items = get_notification_items(user_id, filter_type)  # => [(id, value, is_enabled), ...]
    items_per_page = 15
    start = (page - 1) * items_per_page
    end = start + items_per_page
    subitems = items[start:end]

    keyboard = []
    for (filter_id, val, is_en) in subitems:
        icon = "✅" if is_en == 1 else "❌"
        data = f"tn|{filter_id}|{page}"
        button_text = f"{icon} {val}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=data)])

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
    orders_url = "https://scraptraffic.com/team/api/telegram_bot_external/orders"
    headers = {"Authorization": f"Bearer {BEARER_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(orders_url, headers=headers) as resp:
            data = await resp.json()
            return data

async def post_new_order(order_data: dict) -> dict:
    # Emulate new order via GET request (since POST returns 405)
    url = "https://scraptraffic.com/team/api/telegram_bot_external/emulate_new_order"
    headers = {"Authorization": f"Bearer {BEARER_TOKEN}"}
    logger.info(f"Posting new order with data: {order_data}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=order_data) as resp:
            try:
                data = await resp.json()
                logger.info(f"New order posted successfully: {data}")
            except Exception as e:
                text = await resp.text()
                logger.error(f"Error decoding JSON: {e}. Response text: {text}")
                data = {"error": "Не удалось создать заявку"}
            return data

async def build_requests_page_text(search, page, page_size=10):
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
    return text, has_prev, has_next

def build_requests_page_keyboard(page, has_prev, has_next, search):
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

# ---------------------------------------------------------------------------
# УВЕДОМЛЕНИЕ ПОЛЬЗОВАТЕЛЕЙ О НОВОЙ ЗАЯВКЕ
# ---------------------------------------------------------------------------
async def notify_users_about_new_request(context: ContextTypes.DEFAULT_TYPE, creator_user_id: int, req: dict):
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
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=notification_text,
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Failed to send notification to user_id={uid} (tg_id={tg_id}): {e}")

# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
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

# ---------------------------------------------------------------------------
# ОБРАБОТКА INLINE-КНОПОК
# ---------------------------------------------------------------------------
async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data
    user = query.from_user
    user_row = get_user_by_telegram_id(user.id)
    if not user_row:
        await query.answer("Вы не зарегистрированы. Введите /start.", show_alert=True)
        return ConversationHandler.END
    user_id = user_row[0]

    if data == "menu_pro":
        try:
            await query.message.edit_text(
                "🚀 Функция активации Pro‑аккаунта пока не реализована.",
                reply_markup=build_main_menu(),
                parse_mode='HTML'
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                logger.info("Message not modified, skipping update.")
            else:
                logger.error(f"edit_text error: {e}")
        await query.answer()
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
            if "Message is not modified" in str(e):
                logger.info("Message not modified, skipping update.")
            else:
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
        items_kb = build_notification_list_keyboard(user_id, "material", page=1)
        try:
            await query.message.edit_text(
                "🔔 Настройка материалов:",
                reply_markup=items_kb,
                parse_mode='HTML'
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                logger.info("Message not modified, skipping update.")
            else:
                logger.error(f"edit_text error: {e}")
        await query.answer()
        return MAIN_MENU

    elif data == "notif_cities":
        items_kb = build_notification_list_keyboard(user_id, "city", page=1)
        try:
            await query.message.edit_text(
                "🔔 Настройка городов:",
                reply_markup=items_kb,
                parse_mode='HTML'
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                logger.info("Message not modified, skipping update.")
            else:
                logger.error(f"edit_text error: {e}")
        await query.answer()
        return MAIN_MENU

    elif data == "notif_view_requests":
        text, has_prev, has_next = await build_requests_page_text("", 1)
        kb = build_requests_page_keyboard(1, has_prev, has_next, "")
        try:
            await query.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        except Exception as e:
            if "Message is not modified" in str(e):
                logger.info("Message not modified, skipping update.")
            else:
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
                if "Message is not modified" in str(e):
                    logger.info("Message not modified, skipping update.")
                else:
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
            if "Message is not modified" in str(e):
                logger.info("Message not modified, skipping update.")
            else:
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
            if "Message is not modified" in str(e):
                logger.info("Message not modified, skipping update.")
            else:
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
                if "Message is not modified" in str(e):
                    logger.info("Message not modified, skipping update.")
                else:
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
                if "Message is not modified" in str(e):
                    logger.info("Message not modified, skipping update.")
                else:
                    logger.error(f"edit_text error: {e}")
            await query.answer()
            return MAIN_MENU

        elif data == "req_really_confirm":
            req = context.user_data["request"]
            try:
                result = await post_new_order({
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
                if "Message is not modified" in str(e):
                    logger.info("Message not modified, skipping update.")
                else:
                    logger.error(f"edit_text error: {e}")
            await query.answer()
            await query.message.reply_text(
                "📋 Главное меню: выберите действие.",
                reply_markup=build_main_menu(),
                parse_mode='HTML'
            )
            return MAIN_MENU

        sub = data.split("_")[1]
        if sub == "back":
            await query.message.delete()
            await query.bot.send_message(
                chat_id=query.message.chat_id,
                text="📋 Главное меню: выберите действие.",
                reply_markup=build_main_menu(),
                parse_mode='HTML'
            )
            await query.answer()
            return MAIN_MENU

        elif sub == "type":
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
                if "Message is not modified" in str(e):
                    logger.info("Message not modified, skipping update.")
                else:
                    logger.error(f"edit_text error: {e}")
            await query.answer()
            return MAIN_MENU

        elif sub in ["material", "quantity", "city", "info"]:
            context.user_data["awaiting_field"] = sub
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="req_cancel_field")]])
            try:
                await query.message.edit_text(
                    f"Введите значение для <b>{sub}</b>:\nОтправьте текст сообщением в чат.",
                    reply_markup=kb,
                    parse_mode='HTML'
                )
            except Exception as e:
                if "Message is not modified" in str(e):
                    logger.info("Message not modified, skipping update.")
                else:
                    logger.error(f"edit_text error: {e}")
            await query.answer()
            return REQUEST_INPUT

        elif sub == "confirm":
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
                    if "Message is not modified" in str(e):
                        logger.info("Message not modified, skipping update.")
                    else:
                        logger.error(f"edit_text error: {e}")
                await query.answer()
                return MAIN_MENU

        else:
            await query.answer("Непонятная команда req_.", show_alert=True)
            return MAIN_MENU

    elif data in ["req_cancel_field", "req_no_confirm"]:
        summary = build_request_summary(context.user_data)
        kb = build_request_keyboard(context.user_data)
        try:
            await query.message.edit_text(summary, reply_markup=kb, parse_mode='HTML')
        except Exception as e:
            if "Message is not modified" in str(e):
                logger.info("Message not modified, skipping update.")
            else:
                logger.error(f"edit_text error: {e}")
        await query.answer()
        return MAIN_MENU

    elif data.startswith("tn|"):
        parts = data.split("|")
        if len(parts) == 3:
            _, filter_id_str, page_str = parts
            filter_id = int(filter_id_str)
            page = int(page_str)
            toggle_notification_item_by_id(user_id, filter_id)
            filter_type = get_filter_type_for_id(user_id, filter_id=filter_id)
            if filter_type:
                new_kb = build_notification_list_keyboard(user_id, filter_type, page)
                try:
                    await query.message.edit_reply_markup(new_kb)
                except Exception as e:
                    if "Message is not modified" in str(e):
                        logger.info("Message not modified, skipping update.")
                    else:
                        logger.error(f"edit_reply_markup error: {e}")
                await query.answer()
                return MAIN_MENU
            else:
                await query.answer("Фильтр не найден.", show_alert=True)
                return MAIN_MENU
        else:
            await query.answer("Непонятный формат callback_data (tn|).", show_alert=True)
            return MAIN_MENU

    elif data.startswith("ln|"):
        parts = data.split("|")
        if len(parts) == 3:
            _, filter_type, page_str = parts
            page = int(page_str)
            new_kb = build_notification_list_keyboard(user_id, filter_type, page)
            try:
                await query.message.edit_reply_markup(new_kb)
            except Exception as e:
                if "Message is not modified" in str(e):
                    logger.info("Message not modified, skipping update.")
                else:
                    logger.error(f"edit_reply_markup error: {e}")
            await query.answer()
            return MAIN_MENU
        else:
            await query.answer("Непонятный формат callback_data (ln|).", show_alert=True)
            return MAIN_MENU

    else:
        await query.answer("Непонятная команда.", show_alert=True)
        return MAIN_MENU

# ---------------------------------------------------------------------------
# ДОП. ФУНКЦИЯ: ПОЛУЧИТЬ filter_type ПО filter_id
# ---------------------------------------------------------------------------
def get_filter_type_for_id(user_id: int, filter_id: int) -> str:
    from db import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT filter_type
        FROM notification_filters
        WHERE id = ? AND user_id = ?
    ''', (filter_id, user_id))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0]
    return None

# ---------------------------------------------------------------------------
# ФОЛЛБЕК ДЛЯ ТЕКСТОВЫХ СООБЩЕНИЙ
# ---------------------------------------------------------------------------
async def text_fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()
    if text == "test notifications":
        await update.message.reply_text(
            "🔔 Пример уведомления:\n"
            "Новая заявка: Город Алматы, Материал Медь, Количество 5 тонн.\n"
            "Нажмите /view_123 для просмотра.",
            parse_mode='HTML'
        )
        return MAIN_MENU
    else:
        await update.message.reply_text("⚠️ Непонятная команда. Попробуйте ещё раз.", parse_mode='HTML')
        return MAIN_MENU

# ---------------------------------------------------------------------------
# ОБРАБОТКА ВВОДА ТЕКСТА ДЛЯ ЗАЯВКИ
# ---------------------------------------------------------------------------
async def request_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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

# ---------------------------------------------------------------------------
# ОБРАБОТКА ВВОДА ТЕКСТА ДЛЯ ПОИСКА ЗАЯВОК
# ---------------------------------------------------------------------------
async def search_requests_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    search_query = update.message.text.strip()
    text, has_prev, has_next = await build_requests_page_text(search_query, 1)
    kb = build_requests_page_keyboard(1, has_prev, has_next, search_query)
    await update.message.reply_text(text, reply_markup=kb, parse_mode='HTML')
    return MAIN_MENU

# ---------------------------------------------------------------------------
# ОБРАБОТЧИК ОШИБОК
# ---------------------------------------------------------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error('Update "%s" caused error "%s"', update, context.error)

# ---------------------------------------------------------------------------
# ConversationHandler
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Запуск бота (точка входа)
# ---------------------------------------------------------------------------
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
