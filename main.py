"""
Thumbtack AI Agent — FastAPI app.

Routes:
  GET  /              — healthcheck
  GET  /login         — старт OAuth flow
  GET  /callback      — OAuth callback от Thumbtack
  POST /webhook       — входящие события Thumbtack
  GET  /pros          — список подключённых Pro (admin)
  GET  /token/{id}    — статус токена (admin)
  GET  /convo/{id}    — состояние диалога (admin/debug)
"""

import logging
import secrets
import time
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from config import cfg, ConfigError
from kv import get_conversation, save_conversation, decode_jwt_payload, save_token, list_pro_ids, get_token, token_is_fresh
from thumbtack import get_access_token, refresh_token, send_message
from ai_agent import run_agent
from models import State
from vapi import trigger_call
from pro_config import load_pro_config
from first_message import build_lead_context
import httpx

# ---------------------------------------------------------------------------
# Logging (структурированный вывод в stdout, Render захватывает)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Thumbtack AI Agent")


@app.on_event("startup")
async def startup():
    try:
        cfg()  # Проверяем env при старте
        logger.info("startup: конфиг загружен успешно")
    except ConfigError as e:
        # Log but don't crash — missing vars will surface on first request
        logger.critical("startup config error: %s", e)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "status":  "Thumbtack AI Agent running",
        "version": "2.0",
    }


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------

@app.get("/login")
def login():
    """Редирект на Thumbtack для авторизации Pro."""
    c     = cfg()
    state = secrets.token_urlsafe(16)
    url   = (
        f"{c.tt_auth_url}"
        f"?client_id={c.thumbtack_client_id}"
        f"&redirect_uri={c.redirect_uri}"
        f"&response_type=code"
        f"&scope={quote(c.tt_scopes)}"
        f"&audience=urn%3Apartner-api"
        f"&state={state}"
    )
    return RedirectResponse(url=url)

@app.get("/callback")
async def callback(
    request: Request,
    code:              str = None,
    error:             str = None,
    error_description: str = None,
    state:             str = None,
):
    if error:
        logger.warning("oauth_callback error=%s desc=%s", error, error_description)
        return HTMLResponse(
            content=f"<h3>Error: {error}</h3><p>{error_description or ''}</p>",
            status_code=400,
        )
    if not code:
        return HTMLResponse(content="No authorization code received.", status_code=400)

    c = cfg()
    import base64
    encoded = base64.b64encode(
        f"{c.thumbtack_client_id}:{c.thumbtack_client_secret}".encode()
    ).decode()

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            c.tt_token_url,
            headers={"Authorization": f"Basic {encoded}"},
            data={
                "grant_type":   "authorization_code",
                "code":          code,
                "redirect_uri":  c.redirect_uri,
            },
        )

    if resp.status_code != 200:
        logger.error("oauth token exchange failed: %s %s", resp.status_code, resp.text[:200])
        return HTMLResponse(content=f"Token error: {resp.text}", status_code=500)

    token_data = resp.json()
    claims     = decode_jwt_payload(token_data.get("access_token", ""))
    pro_id     = claims.get("sub") or "default"

    await save_token(pro_id, token_data)
    logger.info("oauth_callback: Pro %s авторизован, scope=%s", pro_id, token_data.get("scope"))

    return HTMLResponse(content=f"""
        <h2>✅ Authorization successful!</h2>
        <p>Pro ID: <code>{pro_id}</code></p>
        <p>Scope: <code>{token_data.get('scope', 'n/a')}</code></p>
        <p>Has refresh_token: <code>{bool(token_data.get('refresh_token'))}</code></p>
        <p>AI will now handle your Thumbtack leads automatically.</p>
    """)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@app.post("/webhook")
async def webhook(request: Request):
    t_start = time.time()
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    event_type     = body.get("eventType") or body.get("type", "")
    data           = body.get("data", body)
    pro_id         = data.get("proId") or data.get("userId") or "default"
    negotiation_id = data.get("negotiationId") or data.get("id", "")

    logger.info(
        "webhook event=%s pro=%s neg=%s",
        event_type, pro_id, negotiation_id,
    )

    # Если Pro сам написал — переводим диалог в PRO_ACTIVE, агент замолчит
    if data.get("senderType") == "PRO":
        if negotiation_id:
            raw = await get_conversation(negotiation_id)
            if raw and raw.get("state") == State.ACTIVE:
                raw["state"] = State.PRO_ACTIVE
                await save_conversation(negotiation_id, raw)
                logger.info("pro_takeover neg=%s → PRO_ACTIVE", negotiation_id)
        return {"status": "ok"}

    access_token = await get_access_token(pro_id)
    pro_config   = await load_pro_config(pro_id)

    # -----------------------------------------------------------------------
    if event_type == "NegotiationCreatedV4":
        customer_name  = data.get("customerName") or data.get("customer", {}).get("name", "Customer")
        customer_phone = data.get("customerPhone") or data.get("customer", {}).get("phone", "")
        service        = data.get("serviceType") or data.get("category", "handyman service")
        details        = data.get("requestDescription") or data.get("description", "")

        # Build structured context so the agent knows what's already known
        # and doesn't ask redundant questions on first reply.
        lead_context = build_lead_context(customer_name, service, details)

        reply = await run_agent(
            negotiation_id=negotiation_id,
            pro_id=pro_id,
            pro_config=pro_config,
            new_message=lead_context,
            customer_name=customer_name,
            service=service,
        )

        if reply and access_token and negotiation_id:
            await send_message(negotiation_id, reply, access_token)

        if customer_phone:
            await trigger_call(customer_name, customer_phone, service, pro_config)

    # -----------------------------------------------------------------------
    elif event_type == "MessageCreatedV4":
        customer_name = data.get("senderName") or data.get("customerName", "Customer")
        customer_phone = data.get("customerPhone", "")
        message_text  = data.get("messageText") or data.get("text", "")
        service       = data.get("serviceType", "handyman service")

        if not message_text:
            return {"status": "ok"}

        reply = await run_agent(
            negotiation_id=negotiation_id,
            pro_id=pro_id,
            pro_config=pro_config,
            new_message=message_text,
            customer_name=customer_name,
            service=service,
        )

        if reply and access_token and negotiation_id:
            await send_message(negotiation_id, reply, access_token)

    elapsed = round(time.time() - t_start, 2)
    logger.info("webhook done event=%s neg=%s elapsed=%ss", event_type, negotiation_id, elapsed)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Admin / debug routes
# ---------------------------------------------------------------------------

@app.get("/pros")
async def list_pros():
    ids = await list_pro_ids()
    return {"connected_pros": ids, "count": len(ids)}


@app.get("/token/{pro_id}")
async def token_status(pro_id: str):
    data = await get_token(pro_id)
    if not data:
        return {"error": f"No token for pro_id={pro_id}. Authorize via /login."}
    claims = decode_jwt_payload(data.get("access_token", ""))
    exp    = claims.get("exp", 0)
    return {
        "pro_id":              pro_id,
        "has_access_token":    bool(data.get("access_token")),
        "has_refresh_token":   bool(data.get("refresh_token")),
        "scope":               data.get("scope"),
        "expires_in_seconds":  max(0, int(exp - time.time())),
        "is_fresh":            token_is_fresh(data),
    }


@app.get("/convo/{negotiation_id}")
async def convo_status(negotiation_id: str):
    data = await get_conversation(negotiation_id)
    if not data:
        return {"error": f"No conversation for negotiation_id={negotiation_id}"}
    # Убираем историю из ответа для краткости
    summary = {k: v for k, v in data.items() if k != "history"}
    summary["history_len"] = len(data.get("history", []))
    return summary
