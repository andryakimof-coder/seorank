# migrate_auth.py
import sqlite3
import secrets
import string

def generate_token(length=8):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length)).replace('0', 'A').replace('O', 'B')

conn = sqlite3.connect('data/rankings.db')
c = conn.cursor()

# 1. Таблица пользователей
c.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# 2. Добавляем user_id в projects (если ещё не добавлено)
try:
    c.execute("ALTER TABLE projects ADD COLUMN user_id INTEGER REFERENCES users(id)")
except sqlite3.OperationalError:
    pass  # уже есть

# 3. Таблица гостевых ссылок
c.execute('''
CREATE TABLE IF NOT EXISTS guest_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
)
''')

# 4. Индекс для токенов
c.execute('CREATE INDEX IF NOT EXISTS idx_guest_token ON guest_links(token)')

conn.commit()
conn.close()
print("✅ Миграция авторизации и гостевых ссылок завершена.")
