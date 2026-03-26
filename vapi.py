"""VAPI — outbound звонки клиентам."""

import logging
import re

import httpx

from config import cfg
from models import ProConfig

logger = logging.getLogger(__name__)


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if len(digits) == 10:
        return "+1" + digits
    if not digits.startswith("+"):
        return "+" + digits
    return digits


async def trigger_call(
    customer_name:  str,
    customer_phone: str,
    service:        str,
    pro_config:     ProConfig,
) -> bool:
    if not cfg().enable_voice_agent:
        logger.info("Voice agent disabled by config (ENABLE_VOICE_AGENT=false); skipping VAPI call for %s", customer_name)
        return False

    phone = _normalize_phone(customer_phone)
    if not phone:
        logger.warning("trigger_call: некорректный телефон '%s'", customer_phone)
        return False

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.vapi.ai/call/phone",
            headers={
                "Authorization": f"Bearer {cfg().vapi_api_key}",
                "Content-Type":  "application/json",
            },
            json={
                "assistantId":   pro_config.vapi.assistant_id,
                "phoneNumberId": pro_config.vapi.phone_number_id,
                "customer": {
                    "number": phone,
                    "name":   customer_name,
                },
                "assistantOverrides": {
                    "variableValues": {
                        "customerName":  customer_name,
                        "serviceNeeded": service,
                    }
                },
            },
        )

    if resp.status_code not in (200, 201):
        logger.error("trigger_call failed phone=%s status=%s body=%s",
                     phone, resp.status_code, resp.text[:200])
        return False

    logger.info("trigger_call ok phone=%s service=%s", phone, service)
    return True
