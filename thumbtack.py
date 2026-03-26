"""Thumbtack API client — токены и отправка сообщений."""

import base64
import logging

import httpx

from config import cfg
from kv import get_token, save_token, token_is_fresh

logger = logging.getLogger(__name__)


def _basic_auth() -> str:
    c = cfg()
    encoded = base64.b64encode(
        f"{c.thumbtack_client_id}:{c.thumbtack_client_secret}".encode()
    ).decode()
    return f"Basic {encoded}"


async def refresh_token(pro_id: str) -> str | None:
    """Обновляет access_token через refresh_token. Возвращает новый access_token или None."""
    token_data = await get_token(pro_id)
    if not token_data or not token_data.get("refresh_token"):
        logger.warning("refresh_token: нет refresh_token для pro_id=%s", pro_id)
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            cfg().tt_token_url,
            headers={"Authorization": _basic_auth()},
            data={
                "grant_type":    "refresh_token",
                "refresh_token": token_data["refresh_token"],
            },
        )

    if resp.status_code != 200:
        logger.error("refresh_token failed pro_id=%s status=%s body=%s",
                     pro_id, resp.status_code, resp.text[:200])
        return None

    new_data = resp.json()
    # Сохраняем старый refresh_token если новый не пришёл
    if not new_data.get("refresh_token"):
        new_data["refresh_token"] = token_data["refresh_token"]

    await save_token(pro_id, new_data)
    logger.info("refresh_token: обновлён для pro_id=%s", pro_id)
    return new_data.get("access_token")


async def get_access_token(pro_id: str) -> str | None:
    """Возвращает живой access_token. Если истёк — обновляет через refresh."""
    token_data = await get_token(pro_id)
    if not token_data:
        logger.warning("get_access_token: нет токена для pro_id=%s", pro_id)
        return None
    if token_is_fresh(token_data):
        return token_data["access_token"]
    logger.info("get_access_token: токен истёк, обновляем pro_id=%s", pro_id)
    return await refresh_token(pro_id)


async def send_message(negotiation_id: str, text: str, access_token: str) -> bool:
    """Отправляет сообщение клиенту в Thumbtack чат."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{cfg().tt_api_base}/v4/negotiations/{negotiation_id}/messages",
            headers={
                "Authorization":  f"Bearer {access_token}",
                "Content-Type":   "application/json",
            },
            json={"text": text},
        )
    if resp.status_code not in (200, 201):
        logger.error("send_message failed neg=%s status=%s body=%s",
                     negotiation_id, resp.status_code, resp.text[:200])
        return False
    return True
