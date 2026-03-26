"""
AI Agent — ядро системы.

Отвечает за:
- загрузку/сохранение истории диалога из KV
- tool calling (check_calendar, book_appointment, request_human)
- state machine (active → booked / human_needed)
- guardrails (не отвечать если уже booked/human_needed)
- логирование каждого шага
"""

import json
import logging
import time

import httpx

from config import cfg
from kv import get_conversation, save_conversation
from models import Conversation, ConversationContext, State
from pro_config import ProConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function calling)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_calendar",
            "description": (
                "Проверяет доступные слоты для записи на конкретную дату. "
                "ОБЯЗАТЕЛЬНО вызывать перед тем, как предложить любое время клиенту."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Дата в формате YYYY-MM-DD",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Создаёт запись в Google Calendar. "
                "Вызывать ТОЛЬКО после того, как клиент подтвердил дату, время И предоставил адрес."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date":    {"type": "string", "description": "YYYY-MM-DD"},
                    "time":    {"type": "string", "description": "Например '10:00 AM'"},
                    "name":    {"type": "string", "description": "Полное имя клиента"},
                    "phone":   {"type": "string", "description": "Телефон клиента"},
                    "service": {"type": "string", "description": "Описание услуги"},
                    "address": {"type": "string", "description": "Адрес клиента"},
                },
                "required": ["date", "time", "name", "service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_human",
            "description": (
                "Вызывать когда ситуация требует вмешательства живого человека: "
                "клиент очень недоволен, нестандартный запрос вне прайса, "
                "юридические вопросы, или агент не уверен как ответить."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Причина передачи",
                    }
                },
                "required": ["reason"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Calendar worker calls
# ---------------------------------------------------------------------------

async def _check_calendar(date: str, calendar_url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{calendar_url}/calendar/slots",
                params={"date": date},
            )
        result = resp.json().get("result", "")
        logger.info("check_calendar date=%s result=%s", date, result[:100])
        return result
    except Exception as e:
        logger.error("check_calendar error: %s", e)
        return f"Не удалось проверить календарь: {e}"


async def _book_appointment(args: dict, calendar_url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{calendar_url}/calendar/book", json=args)
        result = resp.json().get("result", "")
        logger.info("book_appointment args=%s result=%s", args, result)
        return result
    except Exception as e:
        logger.error("book_appointment error: %s", e)
        return f"Не удалось создать запись: {e}"


# ---------------------------------------------------------------------------
# Main agent function
# ---------------------------------------------------------------------------

async def run_agent(
    negotiation_id: str,
    pro_id: str,
    pro_config: ProConfig,
    new_message: str,
    customer_name: str = "Customer",
    service: str = "",
) -> str:
    """
    Загружает историю, добавляет новое сообщение, запускает GPT с tool calling,
    сохраняет историю обратно. Возвращает текст ответа.
    """
    t_start = time.time()

    # --- Загрузка / создание диалога ---
    raw = await get_conversation(negotiation_id)
    if raw:
        convo = Conversation.from_dict(raw)
    else:
        convo = Conversation(
            negotiation_id=negotiation_id,
            pro_id=pro_id,
            context=ConversationContext(
                customer_name=customer_name,
                service=service,
            ),
        )

    # --- Guardrail: не отвечать если уже завершён ---
    if convo.state == State.BOOKED:
        logger.info("neg=%s уже BOOKED, пропускаем", negotiation_id)
        return ""
    if convo.state == State.HUMAN_NEEDED:
        logger.info("neg=%s ожидает human handoff, пропускаем", negotiation_id)
        return ""

    # --- Добавляем сообщение клиента в историю ---
    convo.add_message("user", new_message)

    logger.info(
        "agent_start neg=%s pro=%s state=%s history_len=%d msg=%.80s",
        negotiation_id, pro_id, convo.state, len(convo.history), new_message,
    )

    # --- OpenAI tool calling loop ---
    messages = [
        {"role": "system", "content": pro_config.system_prompt},
        *convo.history,
    ]

    reply_text = ""
    tool_calls_log = []

    async with httpx.AsyncClient(timeout=30) as http:
        for iteration in range(4):  # максимум 4 итерации tool calling
            resp = await http.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg().openai_api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       "gpt-4o-mini",
                    "messages":    messages,
                    "tools":       TOOLS,
                    "tool_choice": "auto",
                    "max_tokens":  400,
                    "temperature": 0.6,
                },
            )

            if resp.status_code != 200:
                logger.error("OpenAI error status=%s body=%s",
                             resp.status_code, resp.text[:300])
                reply_text = (
                    f"Hi {customer_name}! Thanks for reaching out. "
                    "I'll get back to you shortly. — Eran's assistant"
                )
                break

            data   = resp.json()
            choice = data["choices"][0]
            msg    = choice["message"]
            messages.append(msg)

            # Финальный ответ — выходим из цикла
            if choice["finish_reason"] != "tool_calls":
                reply_text = msg.get("content", "").strip()
                break

            # --- Обработка tool calls ---
            for tc in msg.get("tool_calls", []):
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"]["arguments"])
                tool_calls_log.append({"tool": fn_name, "args": fn_args})

                if fn_name == "check_calendar":
                    result = await _check_calendar(
                        fn_args["date"], pro_config.calendar_worker_url
                    )

                elif fn_name == "book_appointment":
                    # Guardrail: не бронировать если уже booked
                    if convo.state == State.BOOKED:
                        result = "Appointment already booked."
                    else:
                        result = await _book_appointment(
                            fn_args, pro_config.calendar_worker_url
                        )
                        if "confirmed" in result.lower() or "appointment" in result.lower():
                            convo.state = State.BOOKED
                            convo.context.confirmed_time = fn_args.get("time", "")
                            convo.context.preferred_date = fn_args.get("date", "")
                            convo.context.address = fn_args.get("address", "")
                            logger.info("neg=%s → BOOKED", negotiation_id)

                elif fn_name == "request_human":
                    reason = fn_args.get("reason", "")
                    convo.state = State.HUMAN_NEEDED
                    logger.warning("neg=%s → HUMAN_NEEDED reason=%s", negotiation_id, reason)
                    result = f"Human handoff requested: {reason}"

                else:
                    result = f"Unknown tool: {fn_name}"

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc["id"],
                    "content":      result,
                })

    # --- Добавляем ответ агента в историю ---
    if reply_text:
        convo.add_message("assistant", reply_text)

    # --- Сохраняем диалог ---
    await save_conversation(negotiation_id, convo.to_dict())

    elapsed = round(time.time() - t_start, 2)
    logger.info(
        "agent_done neg=%s state=%s tools=%s elapsed=%ss reply=%.80s",
        negotiation_id, convo.state, tool_calls_log, elapsed, reply_text,
    )

    return reply_text
