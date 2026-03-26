"""
Cloudflare KV — единственный слой работы с хранилищем.

Ключи:
  pro:{pro_id}              — OAuth токен Pro
  convo:{negotiation_id}    — состояние + история диалога
  config:{pro_id}           — конфиг Pro (prompt, pricing, vapi, ...)
"""

import json
import time
import base64
import logging

import httpx

from config import cfg

logger = logging.getLogger(__name__)

_KV_BASE = "https://api.cloudflare.com/client/v4/accounts/{account}/storage/kv/namespaces/{ns}/values/{key}"


def _url(key: str) -> str:
    c = cfg()
    return _KV_BASE.format(account=c.cf_account_id, ns=c.cf_kv_ns_id, key=key)


def _headers() -> dict:
    return {"Authorization": f"Bearer {cfg().cf_api_token}"}


# ---------------------------------------------------------------------------
# Low-level
# ---------------------------------------------------------------------------

async def kv_get(key: str) -> dict | None:
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(_url(key), headers=_headers())
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        logger.warning("kv_get %s → %s", key, resp.status_code)
        return None
    try:
        return json.loads(resp.text)
    except Exception:
        return None


async def kv_set(key: str, value: dict) -> bool:
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.put(_url(key), headers=_headers(), content=json.dumps(value))
    if resp.status_code not in (200, 201):
        logger.error("kv_set %s → %s: %s", key, resp.status_code, resp.text[:200])
        return False
    return True


async def kv_list(prefix: str) -> list[str]:
    c = cfg()
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{c.cf_account_id}"
        f"/storage/kv/namespaces/{c.cf_kv_ns_id}/keys?prefix={prefix}"
    )
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return []
    return [k["name"] for k in resp.json().get("result", [])]


# ---------------------------------------------------------------------------
# OAuth tokens
# ---------------------------------------------------------------------------

async def get_token(pro_id: str) -> dict | None:
    return await kv_get(f"pro:{pro_id}")


async def save_token(pro_id: str, token_data: dict) -> bool:
    return await kv_set(f"pro:{pro_id}", token_data)


async def list_pro_ids() -> list[str]:
    keys = await kv_list("pro:")
    return [k.replace("pro:", "") for k in keys]


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

async def get_conversation(negotiation_id: str) -> dict | None:
    return await kv_get(f"convo:{negotiation_id}")


async def save_conversation(negotiation_id: str, data: dict) -> bool:
    data["updated_at"] = time.time()
    return await kv_set(f"convo:{negotiation_id}", data)


# ---------------------------------------------------------------------------
# Pro configs
# ---------------------------------------------------------------------------

async def get_pro_config(pro_id: str) -> dict | None:
    return await kv_get(f"config:{pro_id}")


async def save_pro_config(pro_id: str, config: dict) -> bool:
    return await kv_set(f"config:{pro_id}", config)


# ---------------------------------------------------------------------------
# JWT helpers (не KV, но удобно держать здесь)
# ---------------------------------------------------------------------------

def decode_jwt_payload(token: str) -> dict:
    try:
        part = token.split(".")[1]
        part += "=" * (4 - len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def token_is_fresh(token_data: dict, buffer_sec: int = 60) -> bool:
    """Возвращает True если access_token жив ещё минимум buffer_sec секунд."""
    at = token_data.get("access_token", "")
    if not at:
        return False
    claims = decode_jwt_payload(at)
    return claims.get("exp", 0) > time.time() + buffer_sec
