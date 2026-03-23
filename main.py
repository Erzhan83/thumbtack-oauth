from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx
import os
import json
import requests

app = FastAPI()

# ============================
# CONFIG (Render Environment Variables)
# ============================
CLIENT_ID     = os.getenv("THUMBTACK_CLIENT_ID", "YOUR_CLIENT_ID")
CLIENT_SECRET = os.getenv("THUMBTACK_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
VAPI_API_KEY   = os.getenv("VAPI_API_KEY", "b66dff86-76f4-4cca-af7a-39889f87e8b8")

REDIRECT_URI    = "https://thumbtack-oauth.onrender.com/callback"
TOKEN_FILE      = "/tmp/token.json"   # /tmp persists between restarts on Render
THUMBTACK_API   = "https://api.thumbtack.com"

# VAPI
VAPI_ASSISTANT_ID    = "2d48591e-a23d-4e33-af29-acfe4dddf78b"
VAPI_PHONE_NUMBER_ID = "c1072055-69d2-43e5-878b-6db30524a8a8"

# Isaac's system prompt for AI text responses (Thumbtack chat)
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


def load_token():
    try:
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_token(data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)


async def refresh_access_token(refresh_token: str) -> str:
    """Exchange refresh_token for a fresh access_token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{THUMBTACK_API}/v4/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
    if resp.status_code == 200:
        new_token = resp.json()
        save_token(new_token)
        return new_token.get("access_token")
    return None


async def get_valid_token() -> str:
    """Get current access token, refreshing if needed."""
    token_data = load_token()
    if not token_data:
        return None
    # Try using current access_token; if expired, refresh
    return token_data.get("access_token")


def generate_ai_response(customer_name: str, service: str, details: str) -> str:
    """Generate a personalized response using OpenAI."""
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
    return f"Hi {customer_name}! Thanks for your request. I'll review the details and get back to you shortly. — Isaac"


def trigger_vapi_call(customer_name: str, customer_phone: str, service: str):
    """Trigger VAPI outbound call to the customer."""
    # Format phone: ensure +1XXXXXXXXXX
    phone = customer_phone.strip().replace("(", "").replace(")", "").replace("-", "").replace(" ", "")
    if not phone.startswith("+"):
        phone = "+1" + phone if len(phone) == 10 else "+" + phone

    requests.post(
        "https://api.vapi.ai/call/phone",
        headers={
            "Authorization": f"Bearer {VAPI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "assistantId": VAPI_ASSISTANT_ID,
            "phoneNumberId": VAPI_PHONE_NUMBER_ID,
            "customer": {"number": phone, "name": customer_name},
            "assistantOverrides": {
                "variableValues": {
                    "customerName": customer_name,
                    "serviceNeeded": service,
                }
            },
        },
    )


async def send_thumbtack_message(negotiation_id: str, message: str, access_token: str):
    """Send a message in Thumbtack chat on behalf of the Pro."""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{THUMBTACK_API}/v4/negotiations/{negotiation_id}/messages",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"message": message},
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
        f"https://pro.thumbtack.com/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=openid%20offline_access"
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
            f"{THUMBTACK_API}/v4/oauth/token",
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
            content=f"❌ Token error: {response.text}", status_code=500
        )

    token_data = response.json()
    save_token(token_data)

    return HTMLResponse(content="""
        <h2>✅ Authorization successful!</h2>
        <p>Isaac Handyman is now connected to Thumbtack.</p>
        <p>The AI assistant will now respond to new leads automatically.</p>
        <p>You can close this page.</p>
    """)


@app.get("/token")
def get_token():
    """Show stored token info (for debugging)."""
    token_data = load_token()
    if not token_data:
        return {"error": "No token found. Authorize via /login first."}
    # Show only non-sensitive parts
    return {
        "has_access_token": bool(token_data.get("access_token")),
        "has_refresh_token": bool(token_data.get("refresh_token")),
        "scope": token_data.get("scope"),
        "token_type": token_data.get("token_type"),
    }


@app.post("/webhook")
async def webhook(request: Request):
    """
    Thumbtack sends lead events here.
    Handles: NegotiationCreatedV4, MessageCreatedV4
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}  # always return 200 to Thumbtack

    event_type = body.get("eventType") or body.get("type", "")
    data = body.get("data", body)  # some versions nest under "data"

    # Get valid access token
    access_token = await get_valid_token()

    if event_type == "NegotiationCreatedV4":
        # New lead/quote request
        customer_name  = data.get("customerName") or data.get("customer", {}).get("name", "Customer")
        customer_phone = data.get("customerPhone") or data.get("customer", {}).get("phone", "")
        service        = data.get("serviceType") or data.get("category", "handyman service")
        details        = data.get("requestDescription") or data.get("description", "")
        negotiation_id = data.get("negotiationId") or data.get("id", "")

        # 1. Generate AI text response
        ai_reply = generate_ai_response(customer_name, service, details)

        # 2. Send reply in Thumbtack chat
        if access_token and negotiation_id:
            await send_thumbtack_message(negotiation_id, ai_reply, access_token)

        # 3. Trigger VAPI outbound call
        if customer_phone:
            trigger_vapi_call(customer_name, customer_phone, service)

    elif event_type == "MessageCreatedV4":
        # Follow-up message from customer
        customer_name  = data.get("senderName") or data.get("customerName", "Customer")
        customer_phone = data.get("customerPhone", "")
        message_text   = data.get("messageText") or data.get("text", "")
        negotiation_id = data.get("negotiationId") or data.get("id", "")
        service        = data.get("serviceType", "handyman service")

        # Only respond if it's from the customer (not from us)
        sender_type = data.get("senderType", "")
        if sender_type == "PRO":
            return {"status": "ok"}  # skip our own messages

        # Generate and send reply
        ai_reply = generate_ai_response(customer_name, service, message_text)
        if access_token and negotiation_id:
            await send_thumbtack_message(negotiation_id, ai_reply, access_token)

    return {"status": "ok"}
