# celery_worker.py
from celery import Celery
import redis
import os
import time
import base64
import json
import requests
import sqlite3

# Настройки
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True
)

redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)

YANDEX_HEADERS = {
    "Authorization": f"Api-Key {YANDEX_API_KEY}",
    "Content-Type": "application/json"
}
SEARCH_API_URL = "https://searchapi.api.cloud.yandex.net/v2/web/searchAsync"
OPERATIONS_API_URL = "https://operation.api.cloud.yandex.net/operations"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 YaBrowser/25.2.0.0 Safari/537.36"
)

def get_db_conn():
    return sqlite3.connect('data/rankings.db')

def cache_key(query: str, region: str = "RU") -> str:
    return f"yandex_cache:{region}:{query}"

@celery_app.task(bind=True, max_retries=3)
def check_keyword_position(self, keyword_id: int, query: str, target_url: str, region: str = "RU"):
    # 1. Проверяем кэш (15 минут = 900 сек)
    key = cache_key(query, region)
    cached = redis_client.get(key)
    search_result = None

    if cached:
        print(f"✅ Кэш найден для '{query}'")
        search_result = json.loads(cached)
    else:
        # 2. Отправляем асинхронный запрос
        payload = {
            "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query},
            "folderId": YANDEX_FOLDER_ID,
            "responseFormat": "FORMAT_JSON",
            "userAgent": USER_AGENT
        }

        try:
            resp = requests.post(SEARCH_API_URL, headers=YANDEX_HEADERS, json=payload, timeout=10)
            resp.raise_for_status()
            op_id = resp.json()["id"]
        except Exception as exc:
            raise self.retry(exc=exc, countdown=60)

        # 3. Polling (до 300 сек)
        for _ in range(60):
            try:
                status_resp = requests.get(f"{OPERATIONS_API_URL}/{op_id}", headers=YANDEX_HEADERS, timeout=10)
                status_resp.raise_for_status()
                op = status_resp.json()
                if op.get("done"):
                    raw_data_b64 = op["response"]["rawData"]
                    search_result = json.loads(base64.b64decode(raw_data_b64).decode("utf-8"))
                    # Сохраняем в кэш на 15 минут
                    redis_client.setex(key, 900, json.dumps(search_result))
                    break
            except Exception:
                pass
            time.sleep(5)
        else:
            raise Exception("Таймаут Yandex Search API")

    # 4. Поиск позиции
    position = None
    found_url = None
    for i, item in enumerate(search_result.get("items", []), start=1):
        url = item.get("url", "").lower().rstrip("/")
        if target_url.lower().rstrip("/") in url:
            position = i
            found_url = url
            break

    # 5. Запись в БД
    conn = get_db_conn()
    conn.execute("""
        INSERT INTO rankings (keyword_id, position, found_url, total_results)
        VALUES (?, ?, ?, ?)
    """, (keyword_id, position, found_url, len(search_result.get("items", []))))
    conn.commit()
    conn.close()

    print(f"✅ keyword_id={keyword_id} → {query} → позиция {position}")
    return {"keyword_id": keyword_id, "position": position}
