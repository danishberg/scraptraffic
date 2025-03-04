"""
handlers.py
Основная логика бота в едином потоке.
Без аудио, с выбором (Все заявки / По фильтру),
и "Сброс аккаунта (Logout)" в главном меню.
"""

import logging
import json
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

MAIN_MENU, REQUEST_INPUT = range(2)

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
        # Short callback data: "tn|filter_id|page"
        # 'tn' = toggle_notif
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

    # Кнопка назад
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="notif_back")])

    return InlineKeyboardMarkup(keyboard)

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
# ВЫВОД ВСЕХ ЗАЯВОК (ПРОСТАЯ ВЕРСИЯ)
# ---------------------------------------------------------------------------
def format_requests_list(requests):
    """
    requests => list of rows: (id, req_type, material, quantity, city, info, created_at)
    Return a formatted string.
    """
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

    # Главное меню
    if data == "menu_pro":
        await query.message.edit_text(
            "🚀 Функция активации Pro‑аккаунта пока не реализована.",
            reply_markup=build_main_menu(),
            parse_mode='HTML'
        )
        await query.answer()
        return MAIN_MENU

    elif data == "menu_notifications":
        await query.message.edit_text(
            "🔔 Вы можете отфильтровать получение заявок по материалам и городам.\n"
            "По умолчанию уведомления приходят по всем материалам и городам.",
            reply_markup=build_notifications_menu(),
            parse_mode='HTML'
        )
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
        await query.message.edit_text(summary, reply_markup=kb, parse_mode='HTML')
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

    # Меню уведомлений
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
        await query.message.edit_text(
            "🔔 Настройка материалов:",
            reply_markup=items_kb,
            parse_mode='HTML'
        )
        await query.answer()
        return MAIN_MENU

    elif data == "notif_cities":
        items_kb = build_notification_list_keyboard(user_id, "city", page=1)
        await query.message.edit_text(
            "🔔 Настройка городов:",
            reply_markup=items_kb,
            parse_mode='HTML'
        )
        await query.answer()
        return MAIN_MENU

    elif data == "notif_view_requests":
        # Показать все заявки (простая версия)
        all_reqs = get_all_requests()
        text = "<b>Все заявки:</b>\n\n" + format_requests_list(all_reqs)
        # Возможно, в реальном коде делать пагинацию, но тут всё одним сообщением
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data="notif_back")]
        ])
        await query.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        await query.answer()
        return MAIN_MENU

    elif data == "notif_back":
        # Вернуться к меню уведомлений
        await query.message.edit_text(
            "🔔 Вы можете отфильтровать получение заявок по материалам и городам.\n"
            "По умолчанию уведомления приходят по всем материалам и городам.",
            reply_markup=build_notifications_menu(),
            parse_mode='HTML'
        )
        await query.answer()
        return MAIN_MENU

    # ----------------------------------------------
    # Редактирование заявки (req_...)
    # ----------------------------------------------
    elif data.startswith("req_"):
        if data == "req_set_type_selling":
            context.user_data["request"]["type"] = "продажа"
            summary = build_request_summary(context.user_data)
            kb = build_request_keyboard(context.user_data)
            await query.message.edit_text(summary, reply_markup=kb, parse_mode='HTML')
            await query.answer()
            return MAIN_MENU

        elif data == "req_set_type_buying":
            context.user_data["request"]["type"] = "закупка"
            summary = build_request_summary(context.user_data)
            kb = build_request_keyboard(context.user_data)
            await query.message.edit_text(summary, reply_markup=kb, parse_mode='HTML')
            await query.answer()
            return MAIN_MENU

        elif data == "req_really_confirm":
            req = context.user_data["request"]
            add_request(
                user_id=user_id,
                req_type=req["type"],
                material=req["material"],
                quantity=req["quantity"],
                city=req["city"],
                info=req["info"]
            )
            await notify_users_about_new_request(context, user_id, req)

            context.user_data["request"] = {
                "type": "не указан",
                "material": "не указан",
                "quantity": "не указано",
                "city": "не указан",
                "info": "не указана"
            }
            await query.message.edit_text("✅ Заявка успешно создана!", parse_mode='HTML')
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
            await query.message.edit_text("Выберите тип заявки:", reply_markup=kb, parse_mode='HTML')
            await query.answer()
            return MAIN_MENU

        elif sub in ["material", "quantity", "city", "info"]:
            context.user_data["awaiting_field"] = sub
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="req_cancel_field")]])
            await query.message.edit_text(
                f"Введите значение для <b>{sub}</b>:\nОтправьте текст сообщением в чат.",
                reply_markup=kb,
                parse_mode='HTML'
            )
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
                await query.message.edit_text(
                    f"{summary}\nПодтверждаете размещение заявки?",
                    reply_markup=kb,
                    parse_mode='HTML'
                )
                await query.answer()
                return MAIN_MENU

        else:
            await query.answer("Непонятная команда req_.", show_alert=True)
            return MAIN_MENU

    elif data in ["req_cancel_field", "req_no_confirm"]:
        summary = build_request_summary(context.user_data)
        kb = build_request_keyboard(context.user_data)
        await query.message.edit_text(summary, reply_markup=kb, parse_mode='HTML')
        await query.answer()
        return MAIN_MENU

    # ----------------------------------------------
    # Обработка короткого callback_data для фильтров
    # ----------------------------------------------
    elif data.startswith("tn|"):
        # "tn|<filter_id>|<page>"
        parts = data.split("|")
        if len(parts) == 3:
            _, filter_id_str, page_str = parts
            filter_id = int(filter_id_str)
            page = int(page_str)

            # Toggle
            toggle_notification_item_by_id(user_id, filter_id)
            # Rebuild keyboard
            # But we need to know if it's "material" or "city".
            # We'll do a small trick: we can look it up again from DB.
            # Because we need the filter_type to rebuild properly.
            # Or we can store it in the callback. Let's do the second approach:
            # We'll handle that below. For now let's do a quick solution:
            # We'll do "ln|material|1" approach to re-page. So let's skip it:
            # We don't know if it's material or city from just the ID. We'll do the same approach as "ln|".
            # We'll do a separate table lookup approach. Let's do that:

            # We can do a separate function to get filter_type from filter_id
            filter_type = get_filter_type_for_id(user_id, filter_id=filter_id)
            if filter_type:
                new_kb = build_notification_list_keyboard(user_id, filter_type, page)
                try:
                    await query.message.edit_reply_markup(new_kb)
                except Exception as e:
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
        # "ln|<filter_type>|<page>"
        parts = data.split("|")
        if len(parts) == 3:
            _, filter_type, page_str = parts
            page = int(page_str)
            new_kb = build_notification_list_keyboard(user_id, filter_type, page)
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

# ---------------------------------------------------------------------------
# ДОП. ФУНКЦИЯ: ПОЛУЧИТЬ filter_type ПО filter_id
# ---------------------------------------------------------------------------
def get_filter_type_for_id(user_id: int, filter_id: int) -> str:
    """
    Возвращает 'material' или 'city' для заданного filter_id (принадлежащего user_id).
    """
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
