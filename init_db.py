# init_db.py
import sqlite3

conn = sqlite3.connect('data/rankings.db')
cursor = conn.cursor()

# Таблица для хранения результатов проверок
cursor.execute('''
CREATE TABLE IF NOT EXISTS rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    target_url TEXT NOT NULL,
    position INTEGER,
    found_url TEXT,
    total_results INTEGER,
    check_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Таблица для хранения групп ключевых фраз (опционально, для будущего масштабирования)
cursor.execute('''
CREATE TABLE IF NOT EXISTS keyword_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
)
''')

# Связь между группой и ключевой фразой (если нужно)
cursor.execute('''
CREATE TABLE IF NOT EXISTS group_keywords (
    group_id INTEGER,
    keyword TEXT,
    FOREIGN KEY (group_id) REFERENCES keyword_groups(id)
)
''')

conn.commit()
conn.close()
print("✅ База данных и таблицы созданы.")
