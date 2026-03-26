"""Telegram уведомления → HandyBot (Ержан)."""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


async def notify(text: str) -> None:
    """Отправляет сообщение Ержану через HandyBot Telegram."""
    token   = os.getenv("HANDYBOT_TELEGRAM_TOKEN")
    chat_id = os.getenv("HANDYBOT_CHAT_ID")
    if not token or not chat_id:
        logger.debug("notify: HANDYBOT_TELEGRAM_TOKEN or HANDYBOT_CHAT_ID not set, skipping")
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id":    chat_id,
                    "text":       text,
                    "parse_mode": "HTML",
                },
            )
    except Exception as e:
        logger.warning("notify failed: %s", e)
