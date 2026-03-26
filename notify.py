"""Telegram уведомления → HandyBot (Ержан)."""
from __future__ import annotations

import logging
import httpx

logger = logging.getLogger(__name__)

_BOT_TOKEN = "8029784213:AAFG7-bKtyXBsvu5cG-mQOsBlr4sfxqIfNE"
_CHAT_ID   = "505466255"


async def notify(text: str) -> None:
    """Отправляет сообщение Ержану через HandyBot Telegram."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id":    _CHAT_ID,
                    "text":       text,
                    "parse_mode": "HTML",
                },
            )
    except Exception as e:
        logger.warning("notify failed: %s", e)
