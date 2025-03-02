"""
db.py
Функции для работы с базой данных (пользователи, заявки, настройки).
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

    # Таблица предпочтений
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
            city TEXT,
            metal_type TEXT,
            quantity TEXT,
            description TEXT,
            image_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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

def update_user_role(user_id, role):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users
        SET role = ?
        WHERE id = ?
    ''', (role, user_id))
    conn.commit()
    conn.close()

def update_user_account_type(user_id, account_type):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users
        SET account_type = ?
        WHERE id = ?
    ''', (account_type, user_id))
    conn.commit()
    conn.close()

def set_user_preferences(user_id, city_prefs, metal_prefs):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT id FROM user_preferences WHERE user_id = ?', (user_id,))
    existing = cursor.fetchone()
    if existing:
        cursor.execute('''
            UPDATE user_preferences
            SET city_preferences = ?, metal_preferences = ?
            WHERE user_id = ?
        ''', (city_prefs, metal_prefs, user_id))
    else:
        cursor.execute('''
            INSERT INTO user_preferences (user_id, city_preferences, metal_preferences)
            VALUES (?, ?, ?)
        ''', (user_id, city_prefs, metal_prefs))

    conn.commit()
    conn.close()

def get_user_preferences(user_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT city_preferences, metal_preferences
        FROM user_preferences
        WHERE user_id = ?
    ''', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return None, None

def add_request(user_id, city, metal_type, quantity, description, image_path=''):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO requests (user_id, city, metal_type, quantity, description, image_path)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, city, metal_type, quantity, description, image_path))
    conn.commit()
    conn.close()

def get_recent_requests(limit=10):
    """
    Возвращает последние заявки (до limit штук).
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, user_id, city, metal_type, quantity, description, image_path, created_at
        FROM requests
        ORDER BY created_at DESC
        LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def delete_user_by_telegram_id(telegram_id):
    """
    Удаляет пользователя (и его настройки) из БД.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    user_row = cursor.fetchone()
    if user_row:
        user_id = user_row[0]
        cursor.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))

    conn.commit()
    conn.close()
