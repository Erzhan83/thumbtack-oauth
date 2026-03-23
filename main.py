from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx
import os
import json
import requests
import base64

app = FastAPI()

# ============================
# CONFIG (Render Environment Variables)
# ============================
CLIENT_ID      = os.getenv("THUMBTACK_CLIENT_ID", "YOUR_CLIENT_ID")
CLIENT_SECRET  = os.getenv("THUMBTACK_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
VAPI_API_KEY   = os.getenv("VAPI_API_KEY", "b66dff86-76f4-4cca-af7a-39889f87e8b8")

# Cloudflare KV — multi-Pro token storage
CF_ACCOUNT_ID  = os.getenv("CF_ACCOUNT_ID", "947e28c8b310dca3060ebcbd29246ee5")
CF_API_TOKEN   = os.getenv("CF_API_TOKEN", "cfat_IDBotmUkpAQYYGKcE2czZ5nnwHogku5lQ2FbeLJf4dbc84b6")
CF_KV_NS_ID    = os.getenv("CF_KV_NS_ID", "8705996ebea74c23b4f5908085285bc5")

REDIRECT_URI   = "https://thumbtack-oauth.onrender.com/callback"

# Thumbtack correct endpoints (production)
TT_AUTH_URL    = "https://auth.thumbtack.com/oauth2/auth"
TT_TOKEN_URL   = "https://auth.thumbtack.com/oauth2/token"
TT_API_BASE    = "https://api.thumbtack.com/api"

# VAPI
VAPI_ASSISTANT_ID    = "2d48591e-a23d-4e33-af29-acfe4dddf78b"
VAPI_PHONE_NUMBER_ID = "c1072055-69d2-43e5-878b-6db30524a8a8"

ISAAC_PROMPT = """You are Isaac, a professional handyman in Miami/Hollywood, FL.
A potential client just submitted a request on Thumbtack. Write a short, warm, professional response.

Rules:
- Max 3-4 sentences
- Be friendly and direct
- Express interest in the job
- Ask one clarifying question if needed (photos, exact items, address)
- Do NOT quote a price yet — say you'll confirm after reviewing details
- Sign off as: Isaac | Isaac Handyman Services
"""

# ============================
# CLOUDFLARE KV — per-Pro token storage
# ============================

def kv_url(key: str) -> str:
    return f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NS_ID}/values/{key}"

def kv_headers() -> dict:
    return {"Authorization": f"Bearer {CF_API_TOKEN}"}

def kv_save_token(pro_id: str, token_data: dict):
    requests.put(kv_url(f"pro:{pro_id}"), headers=kv_headers(), data=json.dumps(token_data))

def kv_load_token(pro_id: str) -> dict | None:
    resp = requests.get(kv_url(f"pro:{pro_id}"), headers=kv_headers())
    if resp.status_code == 200:
        try:
            return json.loads(resp.text)
        except Exception:
            return None
    return None

def kv_list_pros() -> list[str]:
    resp = requests.get(
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NS_ID}/keys?prefix=pro:",
        headers=kv_headers(),
    )
    if resp.status_code == 200:
        keys = resp.json().get("result", [])
        return [k["name"].replace("pro:", "") for k in keys]
    return []

def decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}

# ============================
# THUMBTACK API HELPERS
# ============================

async def refresh_pro_token(pro_id: str) -> str | None:
    token_data = kv_load_token(pro_id)
    if not token_data or not token_data.get("refresh_token"):
        return None

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TT_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": token_data["refresh_token"],
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
    if resp.status_code == 200:
        new_token = resp.json()
        if not new_token.get("refresh_token"):
            new_token["refresh_token"] = token_data["refresh_token"]
        kv_save_token(pro_id, new_token)
        return new_token.get("access_token")
    return None

async def get_pro_token(pro_id: str) -> str | None:
    token_data = kv_load_token(pro_id)
    if not token_data:
        return None
    access_token = token_data.get("access_token")
    if access_token:
        return access_token
    return await refresh_pro_token(pro_id)

async def send_thumbtack_message(negotiation_id: str, message: str, access_token: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TT_API_BASE}/v4/negotiations/{negotiation_id}/messages",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"text": message},
        )

# ============================
# AI + VAPI
# ============================

def generate_ai_response(customer_name: str, service: str, details: str) -> str:
    if not OPENAI_API_KEY:
        return f"Hi {customer_name}! Thanks for reaching out. I'd love to help with your {service}. Can you share more details or photos? — Isaac | Isaac Handyman Services"

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": ISAAC_PROMPT},
                {"role": "user", "content": f"Customer: {customer_name}\nService: {service}\nDetails: {details}"},
            ],
            "max_tokens": 200,
            "temperature": 0.7,
        },
    )
    if resp.status_code == 200:
        return resp.json()["choices"][0]["message"]["content"].strip()
    return f"Hi {customer_name}! Thanks for your request. I'll get back to you shortly. — Isaac"


def trigger_vapi_call(customer_name: str, customer_phone: str, service: str):
    phone = customer_phone.strip().replace("(", "").replace(")", "").replace("-", "").replace(" ", "")
    if not phone.startswith("+"):
        phone = "+1" + phone if len(phone) == 10 else "+" + phone

    requests.post(
        "https://api.vapi.ai/call/phone",
        headers={"Authorization": f"Bearer {VAPI_API_KEY}", "Content-Type": "application/json"},
        json={
            "assistantId": VAPI_ASSISTANT_ID,
            "phoneNumberId": VAPI_PHONE_NUMBER_ID,
            "customer": {"number": phone, "name": customer_name},
            "assistantOverrides": {
                "variableValues": {"customerName": customer_name, "serviceNeeded": service}
            },
        },
    )

# ============================
# ROUTES
# ============================

@app.get("/")
def root():
    return {"status": "Thumbtack OAuth Server is running ✅"}


@app.get("/login")
def login():
    """Start OAuth flow — redirect Pro to Thumbtack authorization page."""
    auth_url = (
        f"{TT_AUTH_URL}"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=offline_access"
        f"&audience=urn%3Apartner-api"
    )
    return RedirectResponse(url=auth_url)


@app.get("/callback")
async def callback(request: Request, code: str = None, error: str = None):
    """Thumbtack redirects here after Pro authorization."""
    if error:
        return HTMLResponse(content=f"❌ Authorization error: {error}", status_code=400)
    if not code:
        return HTMLResponse(content="❌ No authorization code received", status_code=400)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            TT_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )

    if response.status_code != 200:
        return HTMLResponse(content=f"❌ Token error: {response.text}", status_code=500)

    token_data = response.json()

    # Extract pro_id from the ID token (JWT)
    id_token = token_data.get("id_token", "")
    claims = decode_jwt_payload(id_token) if id_token else {}
    pro_id = claims.get("sub") or claims.get("user_id") or "default"

    kv_save_token(pro_id, token_data)

    return HTMLResponse(content=f"""
        <h2>✅ Authorization successful!</h2>
        <p>Pro ID: <code>{pro_id}</code></p>
        <p>Connected to Thumbtack. AI will now handle leads automatically.</p>
        <p>You can close this page.</p>
    """)


@app.get("/pros")
def list_pros():
    pros = kv_list_pros()
    return {"connected_pros": pros, "count": len(pros)}


@app.get("/token/{pro_id}")
def get_token(pro_id: str = "default"):
    token_data = kv_load_token(pro_id)
    if not token_data:
        return {"error": f"No token for pro_id={pro_id}. Authorize via /login first."}
    return {
        "pro_id": pro_id,
        "has_access_token": bool(token_data.get("access_token")),
        "has_refresh_token": bool(token_data.get("refresh_token")),
        "scope": token_data.get("scope"),
    }


@app.post("/webhook")
async def webhook(request: Request):
    """Receives Thumbtack lead events: NegotiationCreatedV4, MessageCreatedV4"""
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    event_type = body.get("eventType") or body.get("type", "")
    data = body.get("data", body)

    pro_id = data.get("proId") or data.get("userId") or "default"
    access_token = await get_pro_token(pro_id)

    if event_type == "NegotiationCreatedV4":
        customer_name  = data.get("customerName") or data.get("customer", {}).get("name", "Customer")
        customer_phone = data.get("customerPhone") or data.get("customer", {}).get("phone", "")
        service        = data.get("serviceType") or data.get("category", "handyman service")
        details        = data.get("requestDescription") or data.get("description", "")
        negotiation_id = data.get("negotiationId") or data.get("id", "")

        ai_reply = generate_ai_response(customer_name, service, details)

        if access_token and negotiation_id:
            await send_thumbtack_message(negotiation_id, ai_reply, access_token)

        if customer_phone:
            trigger_vapi_call(customer_name, customer_phone, service)

    elif event_type == "MessageCreatedV4":
        customer_name  = data.get("senderName") or data.get("customerName", "Customer")
        customer_phone = data.get("customerPhone", "")
        message_text   = data.get("messageText") or data.get("text", "")
        negotiation_id = data.get("negotiationId") or data.get("id", "")
        service        = data.get("serviceType", "handyman service")

        if data.get("senderType") == "PRO":
            return {"status": "ok"}

        ai_reply = generate_ai_response(customer_name, service, message_text)
        if access_token and negotiation_id:
            await send_thumbtack_message(negotiation_id, ai_reply, access_token)

    return {"status": "ok"}
