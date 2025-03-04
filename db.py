"""
db.py
Функции для работы с базой данных (пользователи, заявки, настройки уведомлений).
"""

import sqlite3

DATABASE = 'bot.db'

def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            role TEXT DEFAULT 'buyer',
            account_type TEXT DEFAULT 'free',
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблица user_preferences (опционально, если нужно)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            city_preferences TEXT,
            metal_preferences TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')

    # Таблица заявок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            req_type TEXT,
            material TEXT,
            quantity TEXT,
            city TEXT,
            info TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')

    # Таблица фильтров уведомлений (города/материалы)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notification_filters (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            filter_type TEXT,  -- "material" или "city"
            value TEXT,        -- например "Материал 1" или "Город 5"
            is_enabled INTEGER DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')

    conn.commit()
    conn.close()

def get_connection():
    return sqlite3.connect(DATABASE)

def add_user(telegram_id, username, role='buyer', account_type='free'):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO users (telegram_id, username, role, account_type)
        VALUES (?, ?, ?, ?)
    ''', (telegram_id, username, role, account_type))
    conn.commit()
    conn.close()

def get_user_by_telegram_id(telegram_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, telegram_id, username, role, account_type
        FROM users
        WHERE telegram_id = ?
    ''', (telegram_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def delete_user_by_telegram_id(telegram_id):
    """
    Удаляет пользователя (и все его настройки) из БД.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    user_row = cursor.fetchone()
    if user_row:
        user_id = user_row[0]
        # Удаляем записи из notification_filters
        cursor.execute("DELETE FROM notification_filters WHERE user_id = ?", (user_id,))
        # Удаляем записи из user_preferences (если используете)
        cursor.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,))
        # Удаляем заявки
        cursor.execute("DELETE FROM requests WHERE user_id = ?", (user_id,))
        # Удаляем пользователя
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_request(user_id, req_type, material, quantity, city, info):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO requests (user_id, req_type, material, quantity, city, info)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, req_type, material, quantity, city, info))
    conn.commit()
    conn.close()

def init_notification_items_for_user(user_id):
    """
    Создаём 50 материалов ("Материал 1..50") и 50 городов ("Город 1..50"), все включены (is_enabled=1).
    Если уже существуют записи, ничего не делаем.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM notification_filters WHERE user_id = ?", (user_id,))
    count = cursor.fetchone()[0]
    if count == 0:
        for i in range(1, 51):
            mat_val = f"Материал {i}"
            city_val = f"Город {i}"
            cursor.execute('''
                INSERT INTO notification_filters (user_id, filter_type, value, is_enabled)
                VALUES (?, 'material', ?, 1)
            ''', (user_id, mat_val))
            cursor.execute('''
                INSERT INTO notification_filters (user_id, filter_type, value, is_enabled)
                VALUES (?, 'city', ?, 1)
            ''', (user_id, city_val))
        conn.commit()
    conn.close()

def get_notification_items(user_id, filter_type):
    """
    Возвращает список кортежей (id, value, is_enabled) для заданного user_id и filter_type.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, value, is_enabled
        FROM notification_filters
        WHERE user_id = ? AND filter_type = ?
        ORDER BY value
    ''', (user_id, filter_type))
    rows = cursor.fetchall()
    conn.close()
    return rows

def toggle_notification_item_by_id(user_id, filter_id):
    """
    Переключаем is_enabled по конкретному ID из notification_filters
    (принадлежащему тому же user_id).
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Ensure the filter belongs to this user
    cursor.execute('''
        SELECT is_enabled
        FROM notification_filters
        WHERE id = ? AND user_id = ?
    ''', (filter_id, user_id))
    row = cursor.fetchone()
    if row:
        current = row[0]
        new_val = 0 if current == 1 else 1
        cursor.execute('''
            UPDATE notification_filters
            SET is_enabled = ?
            WHERE id = ? AND user_id = ?
        ''', (new_val, filter_id, user_id))
        conn.commit()

    conn.close()

def get_telegram_id_by_user_id(user_id):
    """
    Возвращает telegram_id пользователя по его внутреннему ID (users.id).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0]
    return None

def get_users_for_notification(material, city):
    """
    Возвращает список user_id, у которых включены фильтры по данному material И по данному city.
    Используем INTERSECT для пересечения.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id
        FROM notification_filters
        WHERE filter_type='material' AND value=? AND is_enabled=1
        INTERSECT
        SELECT user_id
        FROM notification_filters
        WHERE filter_type='city' AND value=? AND is_enabled=1
    ''', (material, city))
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_all_requests():
    """
    Возвращает список всех заявок (id, req_type, material, quantity, city, info, created_at).
    Можно дописать лимит, сортировку и т.д.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, req_type, material, quantity, city, info, created_at
        FROM requests
        ORDER BY created_at DESC
    ''')
    rows = cursor.fetchall()
    conn.close()
    return rows
