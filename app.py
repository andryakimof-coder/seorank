import os
import sqlite3  # ← замените на psycopg2 для PostgreSQL
# ...
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-fallback")
# ...
app.secret_key = SECRET_KEY
