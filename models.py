from dataclasses import dataclass, field
from typing import Optional
import time


# ---------------------------------------------------------------------------
# Conversation state machine
# ---------------------------------------------------------------------------

class State:
    ACTIVE       = "active"         # идёт диалог
    BOOKED       = "booked"         # запись создана
    REJECTED     = "rejected"       # клиент отказался
    HUMAN_NEEDED = "human_needed"   # нужен живой человек


@dataclass
class ConversationContext:
    """Данные, извлечённые из диалога."""
    customer_name:  str = ""
    customer_phone: str = ""
    service:        str = ""
    price_quoted:   Optional[float] = None
    preferred_date: str = ""
    confirmed_time: str = ""
    address:        str = ""
    booking_id:     str = ""   # Google Calendar event id после booking


@dataclass
class Conversation:
    negotiation_id: str
    pro_id:         str
    state:          str = State.ACTIVE
    context:        ConversationContext = field(default_factory=ConversationContext)
    # Последние MAX_HISTORY сообщений в формате OpenAI [{role, content}]
    history:        list = field(default_factory=list)
    created_at:     float = field(default_factory=time.time)
    updated_at:     float = field(default_factory=time.time)

    MAX_HISTORY = 20  # ограничение чтобы не переполнять контекст

    def add_message(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.MAX_HISTORY:
            self.history = self.history[-self.MAX_HISTORY:]
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "negotiation_id": self.negotiation_id,
            "pro_id":         self.pro_id,
            "state":          self.state,
            "context": {
                "customer_name":  self.context.customer_name,
                "customer_phone": self.context.customer_phone,
                "service":        self.context.service,
                "price_quoted":   self.context.price_quoted,
                "preferred_date": self.context.preferred_date,
                "confirmed_time": self.context.confirmed_time,
                "address":        self.context.address,
                "booking_id":     self.context.booking_id,
            },
            "history":    self.history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Conversation":
        ctx = ConversationContext(**d.get("context", {}))
        return cls(
            negotiation_id = d["negotiation_id"],
            pro_id         = d["pro_id"],
            state          = d.get("state", State.ACTIVE),
            context        = ctx,
            history        = d.get("history", []),
            created_at     = d.get("created_at", time.time()),
            updated_at     = d.get("updated_at", time.time()),
        )


# ---------------------------------------------------------------------------
# Pro configuration
# ---------------------------------------------------------------------------

@dataclass
class VapiConfig:
    assistant_id:    str
    phone_number_id: str


@dataclass
class BusinessHours:
    start: int = 8   # 8 AM
    end:   int = 18  # 6 PM


@dataclass
class ProConfig:
    pro_id:               str
    business_id:          str
    name:                 str
    calendar_worker_url:  str
    vapi:                 VapiConfig
    timezone:             str
    business_hours:       BusinessHours
    system_prompt:        str
    service_area:         str = "South Florida"

    @classmethod
    def from_dict(cls, d: dict) -> "ProConfig":
        return cls(
            pro_id              = d["pro_id"],
            business_id         = d["business_id"],
            name                = d["name"],
            calendar_worker_url = d["calendar_worker_url"],
            vapi                = VapiConfig(**d["vapi"]),
            timezone            = d.get("timezone", "America/New_York"),
            business_hours      = BusinessHours(**d.get("business_hours", {})),
            system_prompt       = d["system_prompt"],
            service_area        = d.get("service_area", "South Florida"),
        )

    def to_dict(self) -> dict:
        return {
            "pro_id":               self.pro_id,
            "business_id":          self.business_id,
            "name":                 self.name,
            "calendar_worker_url":  self.calendar_worker_url,
            "vapi": {
                "assistant_id":    self.vapi.assistant_id,
                "phone_number_id": self.vapi.phone_number_id,
            },
            "timezone":       self.timezone,
            "business_hours": {
                "start": self.business_hours.start,
                "end":   self.business_hours.end,
            },
            "system_prompt": self.system_prompt,
            "service_area":  self.service_area,
        }
