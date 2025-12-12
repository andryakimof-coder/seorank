# app.py
import os
import time
import sqlite3
import pandas as pd
import secrets
import string
from datetime import datetime, timedelta
from passlib.hash import pbkdf2_sha256
from flask import Flask, request, jsonify, render_template_string, send_file, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from celery_worker import celery_app, check_keyword_position

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-123")  # для продакшена — смените!

# Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

def get_db():
    conn = sqlite3.connect('data/rankings.db')
    conn.row_factory = sqlite3.Row
    return conn

class User(UserMixin):
    def __init__(self, id, email):
        self.id = id
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT id, email FROM users WHERE id = ?", (int(user_id),)).fetchone()
    conn.close()
    return User(row["id"], row["email"]) if row else None

# === Генерация короткого токена (как у Topvisor: uhLjJ9-va) ===
def generate_short_token():
    # 8 символов base62: a-zA-Z0-9 → ~218 трлн вариантов
    alphabet = string.ascii_letters + string.digits
    token = ''.join(secrets.choice(alphabet) for _ in range(8))
    # Заменяем похожие символы, чтобы избежать путаницы
    return token.replace('0', 'A').replace('O', 'B').replace('l', 'L')

# === HTML-шаблоны ===
LOGIN_HTML = '''
<!doctype html>
<html>
<head><title>🔐 Вход</title></head>
<body>
  <h2>🔐 Вход</h2>
  {% with messages = get_flashed_messages() %}
    {% if messages %}<ul>{% for msg in messages %}<li>{{ msg }}</li>{% endfor %}</ul>{% endif %}
  {% endwith %}
  <form method="POST">
    <input name="email" placeholder="Email" required><br><br>
    <input name="password" type="password" placeholder="Пароль" required><br><br>
    <button type="submit">Войти</button>
  </form>
  <p><a href="/register">Регистрация</a></p>
</body>
</html>
'''

REGISTER_HTML = '''
<!doctype html>
<html>
<head><title>會員註冊</title></head>
<body>
  <h2>會員註冊</h2>
  <form method="POST">
    <input name="email" placeholder="Email" required><br><br>
    <input name="password" type="password" placeholder="Пароль (мин. 6 символов)" required minlength="6"><br><br>
    <button type="submit">Создать аккаунт</button>
  </form>
  <p><a href="/login">Уже есть аккаунт?</a></p>
</body>
</html>
'''

INDEX_HTML = '''
<!doctype html>
<html>
<head><title>Topvisor-подобный Rank Tracker</title></head>
<body>
  <h2>👋 Привет, {{ current_user.email }}! 
    <a href="/logout">🚪 Выйти</a>
  </h2>

  <h3>➕ Создать проект</h3>
  <form method="POST" action="/add_project">
    <input name="name" placeholder="Название проекта" required>
    <input name="main_url" placeholder="https://example.com" required>
    <select name="search_engine">
      <option value="yandex">Yandex</option>
    </select>
    <input name="region" placeholder="RU" value="RU">
    <button>➕ Создать</button>
  </form>
  <hr>

  <h3>Мои проекты:</h3>
  <ul>
  {% for p in projects %}
    <li>
      <strong>{{ p.name }}</strong> → {{ p.main_url }}
      <a href="/project/{{ p.id }}">⚙️ Управление</a>
      <a href="/project/{{ p.id }}/share" target="_blank">🔗 Поделиться</a>
    </li>
  {% endfor %}
  </ul>
</body>
</html>
'''

SHARE_LINK_HTML = '''
<!doctype html>
<html>
<head><title>🔗 Гостевая ссылка создана</title></head>
<body>
  <h2>✅ Ссылка для публичного доступа создана</h2>
  <p>Скопируйте и отправьте её клиенту или коллеге:</p>
  <input id="link" value="{{ full_url }}" size="60" readonly>
  <br><br>
  <button onclick="copyLink()">📋 Копировать</button>

  <script>
    function copyLink() {
      document.getElementById('link').select();
      document.execCommand('copy');
      alert('Скопировано!');
    }
  </script>
  <hr>
  <a href="/project/{{ project_id }}">← Назад к проекту</a>
</body>
</html>
'''

# ——— Роуты аутентификации ———
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        conn = get_db()
        user = conn.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if user and pbkdf2_sha256.verify(password, user["password_hash"]):
            login_user(User(user["id"], email))
            return redirect("/")
        flash("❌ Неверный email или пароль")
    return render_template_string(LOGIN_HTML)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        if len(password) < 6:
            flash("❌ Пароль должен быть не менее 6 символов")
            return render_template_string(REGISTER_HTML)
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, pbkdf2_sha256.hash(password))
            )
            conn.commit()
            conn.close()
            flash("✅ Аккаунт создан. Войдите.")
            return redirect("/login")
        except sqlite3.IntegrityError:
            flash("❌ Email уже занят")
    return render_template_string(REGISTER_HTML)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")

# ——— Основные роуты (требуют входа) ———
@app.route("/")
@login_required
def index():
    conn = get_db()
    projects = conn.execute("SELECT * FROM projects WHERE user_id = ? ORDER BY name", (current_user.id,)).fetchall()
    conn.close()
    return render_template_string(INDEX_HTML, projects=projects)

@app.route("/add_project", methods=["POST"])
@login_required
def add_project():
    name = request.form["name"]
    main_url = request.form["main_url"]
    engine = request.form.get("search_engine", "yandex")
    region = request.form.get("region", "RU")
    conn = get_db()
    conn.execute("""
        INSERT INTO projects (user_id, name, main_url, search_engine, region)
        VALUES (?, ?, ?, ?, ?)
    """, (current_user.id, name, main_url, engine, region))
    conn.commit()
    conn.close()
    return redirect("/")

# ——— Гостевая ссылка ———
@app.route("/project/<int:project_id>/share")
@login_required
def create_guest_link(project_id):
    # Проверяем, что проект принадлежит пользователю
    conn = get_db()
    proj = conn.execute("SELECT id FROM projects WHERE id = ? AND user_id = ?", (project_id, current_user.id)).fetchone()
    if not proj:
        return "❌ Проект не найден", 404

    # Генерируем уникальный токен
    for _ in range(5):
        token = generate_short_token()
        try:
            conn.execute(
                "INSERT INTO guest_links (project_id, token, expires_at) VALUES (?, ?, ?)",
                (project_id, token, None)  # бессрочная
            )
            conn.commit()
            break
        except sqlite3.IntegrityError:
            continue  # повтор при коллизии
    else:
        return "❌ Не удалось создать ссылку", 500

    conn.close()

    full_url = url_for("guest_report", token=token, _external=True)
    return render_template_string(SHARE_LINK_HTML, full_url=full_url, project_id=project_id)

# ——— Публичный отчёт по токену ———
@app.route("/g/<token>")
def guest_report(token):
    conn = get_db()
    link = conn.execute("""
        SELECT p.id, p.name, p.main_url
        FROM guest_links gl
        JOIN projects p ON gl.project_id = p.id
        WHERE gl.token = ? AND (gl.expires_at IS NULL OR gl.expires_at > ?)
    """, (token, datetime.utcnow().isoformat())).fetchone()

    if not link:
        return "❌ Ссылка недействительна или истекла", 404

    # Получаем данные (аналогично /report, но без привязки к пользователю)
    since = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute("""
        SELECT
            r.checked_at,
            r.position,
            k.query,
            g.name AS group_name,
            LAG(r.position) OVER (PARTITION BY k.id ORDER BY r.checked_at) AS prev_position
        FROM rankings r
        JOIN keywords k ON r.keyword_id = k.id
        JOIN keyword_groups g ON k.group_id = g.id
        WHERE g.project_id = ? AND r.checked_at >= ?
        ORDER BY r.checked_at DESC
    """, (link["id"], since)).fetchall()
    conn.close()

    # Подготовка данных (как в /report)
    history = []
    series = {}
    for r in rows:
        delta = None
        if r["position"] is not None and r["prev_position"] is not None:
            d = int(r["prev_position"]) - int(r["position"])
            delta = ("↑" if d > 0 else "↓" if d < 0 else "") + str(abs(d))
        history.append({
            "group_name": r["group_name"],
            "query": r["query"],
            "checked_at": r["checked_at"],
            "position": r["position"],
            "delta": delta
        })

        key = f"{r['group_name']} → {r['query']}"
        series.setdefault(key, {"dates": [], "positions": []})
        series[key]["dates"].append(r["checked_at"][:10])
        series[key]["positions"].append(r["position"] or 100)

    # Chart.js данные
    labels = sorted({d for s in series.values() for d in s["dates"]})
    datasets = []
    colors = ["#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#607D8B"]
    for i, (label, data) in enumerate(series.items()):
        pos_map = {d: p for d, p in zip(data["dates"], data["positions"])}
        points = [pos_map.get(d, None) for d in labels]
        datasets.append({
            "label": label,
            "data": points,
            "borderColor": colors[i % len(colors)],
            "tension": 0.3,
            "fill": False
        })

    chart_data = {"labels": labels, "datasets": datasets} if datasets else None

    # HTML отчёта (упрощённый — без кнопок экспорта/фильтров)
    GUEST_REPORT_HTML = '''
    <!doctype html>
    <html>
    <head><title>📊 Отчёт: {{ project_name }}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
      body { max-width: 1200px; margin: 20px auto; font-family: sans-serif; }
      .header { text-align: center; margin-bottom: 30px; }
      .chart { height: 500px; margin: 20px 0; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
      .top1-3 { background: #d4edda; }
      .top4-10 { background: #cce5ff; }
      .top11-30 { background: #e2e3e5; }
      .top31 { background: #f8d7da; }
    </style>
    </head>
    <body>
      <div class="header">
        <h1>📊 Публичный отчёт</h1>
        <h2>{{ project_name }}</h2>
        <p><em>Создано с помощью Rank Tracker (аналог Topvisor)</em></p>
      </div>

      {% if chart_data %}
      <div class="chart">
        <canvas id="chart"></canvas>
      </div>
      {% endif %}

      <h3>Данные по позициям ({{ history|length }} записей)</h3>
      <table>
        <thead><tr>
          <th>Группа</th><th>Фраза</th><th>Дата</th><th>Позиция</th><th>Изменение</th>
        </tr></thead>
        <tbody>
        {% for r in history %}
        <tr class="{% if r.position <= 3 %}top1-3{% elif r.position <= 10 %}top4-10{% elif r.position <= 30 %}top11-30{% else %}top31{% endif %}">
          <td>{{ r.group_name }}</td>
          <td>{{ r.query }}</td>
          <td>{{ r.checked_at[:16] }}</td>
          <td>{{ r.position or '—' }}</td>
          <td>{{ r.delta or '—' }}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>

      {% if chart_data %}
      <script>
        const ctx = document.getElementById('chart').getContext('2d');
        new Chart(ctx, {
          type: 'line',
          data: {{ chart_data | tojson(indent=2) }},
          options: {
            responsive: true,
            plugins: { legend: { position: 'top' } },
            scales: {
              y: { reverse: true, min: 0, max: 100 },
              x: { title: { display: true, text: 'Дата' } }
            }
          }
        });
      </script>
      {% endif %}
    </body>
    </html>
    '''
    return render_template_string(
        GUEST_REPORT_HTML,
        project_name=link["name"],
        history=history,
        chart_data=chart_data
    )

# ——— Остальные роуты (project/<id>, check_one, report и т.д.) ———
# ... (скопируйте их из предыдущей версии app.py, добавив @login_required)
# Ниже — сокращённо для экономии места:

@app.route("/project/<int:project_id>")
@login_required
def project_detail(project_id):
    conn = get_db()
    project = conn.execute("SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, current_user.id)).fetchone()
    if not project:
        return "❌ Проект не найден", 404
    groups = conn.execute("SELECT * FROM keyword_groups WHERE project_id = ?", (project_id,)).fetchall()
    keywords = conn.execute("""
        SELECT k.*, g.name AS group_name
        FROM keywords k
        JOIN keyword_groups g ON k.group_id = g.id
        WHERE g.project_id = ?
    """, (project_id,)).fetchall()
    conn.close()
    return render_template_string(PROJECT_HTML, project=project, groups=groups, keywords=keywords)

# ... и так далее для /project/<id>/add_group, /check_one, /report, /export

# Для краткости: PROJECT_HTML, REPORT_HTML и остальные функции — скопируйте из предыдущего app.py,
# добавив `@login_required` ко всем, кроме `/g/<token>` и `/login`, `/register`.

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
