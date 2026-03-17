from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import httpx
import os
import json

app = FastAPI()

# ============================
# НАСТРОЙКИ — заполни свои данные
# ============================
CLIENT_ID = os.getenv("THUMBTACK_CLIENT_ID", "YOUR_CLIENT_ID")
CLIENT_SECRET = os.getenv("THUMBTACK_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
REDIRECT_URI = "https://your-app.onrender.com/callback"  # замени на свой URL после деплоя
TOKEN_FILE = "token.json"  # токен сохраняется в файл
# ============================


@app.get("/")
def root():
    return {"status": "Thumbtack OAuth Server is running ✅"}


@app.get("/login")
def login():
    """
    Перейди по этому URL чтобы начать OAuth авторизацию.
    Thumbtack перенаправит тебя на /callback с кодом.
    """
    auth_url = (
        f"https://pro.thumbtack.com/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=openid"
    )
    return HTMLResponse(
        content=f'<a href="{auth_url}">👉 Нажми сюда чтобы авторизоваться через Thumbtack</a>'
    )


@app.get("/callback")
async def callback(request: Request, code: str = None, error: str = None):
    """
    Thumbtack вызывает этот endpoint после авторизации пользователя.
    Получаем code и обмениваем на access_token.
    """
    if error:
        return HTMLResponse(content=f"❌ Ошибка авторизации: {error}", status_code=400)

    if not code:
        return HTMLResponse(content="❌ Код авторизации не получен", status_code=400)

    # Обмениваем code на access_token
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://pro.thumbtack.com/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )

    if response.status_code != 200:
        return HTMLResponse(
            content=f"❌ Ошибка получения токена: {response.text}",
            status_code=500
        )

    token_data = response.json()

    # Сохраняем токен в файл
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)

    access_token = token_data.get("access_token", "N/A")

    return HTMLResponse(content=f"""
        <h2>✅ Авторизация успешна!</h2>
        <p><b>Access Token:</b> {access_token[:20]}...</p>
        <p>Токен сохранён в <code>{TOKEN_FILE}</code></p>
        <p>Теперь можно закрыть эту страницу.</p>
    """)


@app.get("/token")
def get_token():
    """Показывает сохранённый токен (для проверки)"""
    try:
        with open(TOKEN_FILE, "r") as f:
            token_data = json.load(f)
        return token_data
    except FileNotFoundError:
        return {"error": "Токен не найден. Сначала авторизуйся через /login"}
