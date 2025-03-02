"""
handlers.py
Основная логика бота в едином потоке.
Без аудио, с выбором (Все заявки / По фильтру),
и "Сброс аккаунта (Logout)" в главном меню.
"""

import os
import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters
)
from db import (
    add_user,
    get_user_by_telegram_id,
    update_user_role,
    set_user_preferences,
    get_user_preferences,
    add_request,
    get_recent_requests,
    delete_user_by_telegram_id
)

logger = logging.getLogger(__name__)

# Ensure the images folder exists
if not os.path.exists("images"):
    os.makedirs("images")

# Conversation states (enum-like)
(
    CHOOSE_ROLE,
    ASK_BUYER_CITY_PREF,
    ASK_BUYER_METAL_PREF,
    MAIN_MENU,
    SELL_CITY,
    SELL_METAL,
    SELL_QUANTITY,
    SELL_DESCRIPTION,
    SELL_IMAGE,
    SELL_CONFIRM,
    VIEW_MENU,
    CHANGE_PREFS_CITY,
    CHANGE_PREFS_METAL
) = range(13)

# -----------------------------
# /start
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    /start: Check if user is in DB.
    If not, ask for role. If yes, go to main menu.
    """
    user = update.effective_user
    user_record = get_user_by_telegram_id(user.id)

    if not user_record:
        # User is new
        add_user(user.id, user.username)
        reply_keyboard = [["Покупатель", "Продавец", "Оба"]]
        await update.message.reply_text(
            "Добро пожаловать! Вы впервые здесь.\n"
            "Кем вы хотите себя обозначить?\n"
            "- Покупатель\n"
            "- Продавец\n"
            "- Оба\n",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True)
        )
        return CHOOSE_ROLE
    else:
        # Already in DB, go to main menu
        return await go_to_main_menu(update, context)

async def choose_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Save chosen role (buyer/seller/both) from Russian text.
    """
    role_text = update.message.text.strip().lower()
    user = update.effective_user
    user_record = get_user_by_telegram_id(user.id)

    role_map = {
        "покупатель": "buyer",
        "продавец": "seller",
        "оба": "both"
    }

    if role_text not in role_map:
        await update.message.reply_text("Пожалуйста, выберите: Покупатель, Продавец или Оба.")
        return CHOOSE_ROLE

    new_role = role_map[role_text]
    if user_record:
        update_user_role(user_record[0], new_role)

    # If buyer or both, ask for city and metal preferences
    if new_role in ["buyer", "both"]:
        await update.message.reply_text("Введите города, в которых хотите видеть заявки (через запятую):")
        return ASK_BUYER_CITY_PREF
    else:
        # Seller only -> go to main menu
        return await go_to_main_menu(update, context)

async def ask_buyer_city_pref(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['city_prefs'] = update.message.text
    await update.message.reply_text("Введите типы металла, которые вас интересуют (через запятую):")
    return ASK_BUYER_METAL_PREF

async def ask_buyer_metal_pref(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['metal_prefs'] = update.message.text

    user = update.effective_user
    user_record = get_user_by_telegram_id(user.id)
    if user_record:
        # Save preferences to DB
        set_user_preferences(user_record[0],
                             context.user_data['city_prefs'],
                             context.user_data['metal_prefs'])

    await update.message.reply_text("Настройки сохранены!")
    return await go_to_main_menu(update, context)

# -----------------------------
# Main menu
# -----------------------------
async def go_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Show main menu.
    """
    reply_keyboard = [
        ["Подать заявку", "Просмотр заявок"],
        ["Изменить настройки", "Оплата/Подписка"],
        ["Сброс аккаунта (Logout)", "Выход из чата"]
    ]
    await update.message.reply_text(
        "Главное меню: выберите действие.",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True)
    )
    return MAIN_MENU

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handle clicks in main menu.
    """
    text = update.message.text.strip().lower()
    user = update.effective_user
    user_record = get_user_by_telegram_id(user.id)
    role = user_record[3] if user_record else "buyer"

    if text == "назад в меню":
        # Already in the main menu
        await update.message.reply_text("Вы уже находитесь в главном меню.")
        return MAIN_MENU

    elif text == "подать заявку":
        if role in ["seller", "both"]:
            await update.message.reply_text("Введите ваш город:")
            return SELL_CITY
        else:
            await update.message.reply_text("У вас роль покупателя, заявка недоступна.")
            return MAIN_MENU

    elif text == "просмотр заявок":
        # Submenu: All or Filtered, or Back
        sub_menu = [
            ["Все заявки", "По фильтру"],
            ["Назад в меню"]
        ]
        await update.message.reply_text(
            "Хотите посмотреть все заявки или только по вашим фильтрам?",
            reply_markup=ReplyKeyboardMarkup(sub_menu, one_time_keyboard=True)
        )
        return VIEW_MENU

    elif text == "изменить настройки":
        if role in ["buyer", "both"]:
            await update.message.reply_text("Введите новые города (через запятую):")
            return CHANGE_PREFS_CITY
        else:
            await update.message.reply_text("Вы зарегистрированы как продавец. Нет настроек.")
            return MAIN_MENU

    elif text == "оплата/подписка":
        # Placeholder for future payment logic
        await update.message.reply_text(
            "Функция оплаты/подписки не реализована.\n"
            "Здесь можно добавить платные возможности."
        )
        return MAIN_MENU

    elif text == "сброс аккаунта (logout)":
        delete_user_by_telegram_id(user.id)
        await update.message.reply_text(
            "Ваш аккаунт удалён. Введите /start, чтобы зарегистрироваться заново."
        )
        return ConversationHandler.END

    elif text == "выход из чата":
        await update.message.reply_text("Вы вышли из диалога. До свидания!")
        return ConversationHandler.END

    else:
        await update.message.reply_text("Непонятная команда. Попробуйте ещё раз.")
        return MAIN_MENU

# -----------------------------
# Submenu "Просмотр заявок"
# -----------------------------
async def view_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip().lower()

    if choice == "все заявки":
        return await view_all_requests(update, context)
    elif choice == "по фильтру":
        return await view_filtered_requests(update, context)
    elif choice == "назад в меню":
        return await go_to_main_menu(update, context)
    else:
        await update.message.reply_text("Непонятная команда. Попробуйте ещё раз.")
        return VIEW_MENU

async def view_all_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    rows = get_recent_requests(limit=10)
    if not rows:
        await update.message.reply_text("Пока нет заявок.")
        # Stay in the VIEW_MENU so user can pick again
        return VIEW_MENU

    for row in rows:
        req_id, user_id, city, metal_type, quantity, description, image_path, created_at = row
        lines = [
            f"ID заявки: {req_id}",
            f"Город: {city}",
            f"Металл: {metal_type}",
            f"Количество: {quantity}",
            f"Описание: {description}",
            f"Дата: {created_at}"
        ]
        await update.message.reply_text("\n".join(lines))

        # If there's an image, send it
        if image_path and os.path.exists(image_path):
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open(image_path, 'rb'),
                caption="Прикреплённое изображение."
            )

    # After listing all, stay in VIEW_MENU
    return VIEW_MENU

async def view_filtered_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    user_record = get_user_by_telegram_id(user.id)
    if not user_record:
        await update.message.reply_text("Вы не зарегистрированы.")
        return VIEW_MENU

    rows = get_recent_requests(limit=10)
    if not rows:
        await update.message.reply_text("Пока нет заявок.")
        return VIEW_MENU

    city_prefs, metal_prefs = get_user_preferences(user_record[0])
    city_list = [c.strip().lower() for c in city_prefs.split(',')] if city_prefs else []
    metal_list = [m.strip().lower() for m in metal_prefs.split(',')] if metal_prefs else []

    matched_any = False
    for row in rows:
        req_id, u_id, city, metal_type, quantity, description, image_path, created_at = row

        # Filter by city
        if city_list and city.lower() not in city_list:
            continue
        # Filter by metal
        if metal_list and metal_type.lower() not in metal_list:
            continue

        matched_any = True
        lines = [
            f"ID заявки: {req_id}",
            f"Город: {city}",
            f"Металл: {metal_type}",
            f"Количество: {quantity}",
            f"Описание: {description}",
            f"Дата: {created_at}"
        ]
        await update.message.reply_text("\n".join(lines))

        if image_path and os.path.exists(image_path):
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open(image_path, 'rb'),
                caption="Прикреплённое изображение."
            )

    if not matched_any:
        await update.message.reply_text("Нет заявок, соответствующих вашим фильтрам.")

    # Stay in VIEW_MENU
    return VIEW_MENU

# -----------------------------
# Changing preferences
# -----------------------------
async def change_prefs_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['city_prefs'] = update.message.text
    await update.message.reply_text("Введите новые типы металла (через запятую):")
    return CHANGE_PREFS_METAL

async def change_prefs_metal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    user_record = get_user_by_telegram_id(user.id)
    context.user_data['metal_prefs'] = update.message.text

    if user_record:
        set_user_preferences(
            user_record[0],
            context.user_data['city_prefs'],
            context.user_data['metal_prefs']
        )

    await update.message.reply_text("Настройки обновлены.")
    return MAIN_MENU

# -----------------------------
# Sell flow (Подача заявки)
# -----------------------------
async def sell_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['city'] = update.message.text
    await update.message.reply_text("Введите тип металла (например, алюминий, медь, нержавейка):")
    return SELL_METAL

async def sell_metal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['metal'] = update.message.text
    await update.message.reply_text("Введите количество (например, 8 тонн):")
    return SELL_QUANTITY

async def sell_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['quantity'] = update.message.text
    await update.message.reply_text("Опишите заявку (качество, условия и т.д.):")
    return SELL_DESCRIPTION

async def sell_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['description'] = update.message.text
    await update.message.reply_text(
        "Отправьте изображение для заявки (или напишите 'Пропустить'):"
    )
    return SELL_IMAGE

async def sell_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    User sent a photo. Save it to images/<file_id>.jpg
    """
    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_path = os.path.join("images", f"{photo.file_id}.jpg")
    await file.download_to_drive(custom_path=image_path)

    context.user_data['image_path'] = image_path
    await update.message.reply_text("Изображение получено. Подтвердить заявку? (Да/Нет)")
    return SELL_CONFIRM

async def skip_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    User typed 'Пропустить' instead of sending a photo.
    """
    context.user_data['image_path'] = ''
    await update.message.reply_text("Изображение пропущено. Подтвердить заявку? (Да/Нет)")
    return SELL_CONFIRM

async def sell_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()
    user = update.effective_user
    user_record = get_user_by_telegram_id(user.id)

    if text == "да" and user_record:
        add_request(
            user_id=user_record[0],
            city=context.user_data.get('city', ''),
            metal_type=context.user_data.get('metal', ''),
            quantity=context.user_data.get('quantity', ''),
            description=context.user_data.get('description', ''),
            image_path=context.user_data.get('image_path', '')
        )
        await update.message.reply_text("Заявка успешно создана!")
    else:
        await update.message.reply_text("Заявка отменена.")

    return await go_to_main_menu(update, context)

# -----------------------------
# Error handler
# -----------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error('Update "%s" caused error "%s"', update, context.error)

# -----------------------------
# ConversationHandler
# -----------------------------
main_flow_handler = ConversationHandler(
    entry_points=[CommandHandler('start', start)],
    states={
        CHOOSE_ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_role)],
        ASK_BUYER_CITY_PREF: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_buyer_city_pref)],
        ASK_BUYER_METAL_PREF: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_buyer_metal_pref)],

        MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu_handler)],

        VIEW_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, view_menu_handler)],

        CHANGE_PREFS_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_prefs_city)],
        CHANGE_PREFS_METAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_prefs_metal)],

        SELL_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_city)],
        SELL_METAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_metal)],
        SELL_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_quantity)],
        SELL_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_description)],
        SELL_IMAGE: [
            MessageHandler(filters.PHOTO, sell_image),
            MessageHandler(filters.TEXT & ~filters.COMMAND, skip_image)
        ],
        SELL_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_confirm)],
    },
    fallbacks=[CommandHandler('cancel', start)],
    name="main_flow"
)
