"""
first_message.py — structured lead context builder.

When a new Thumbtack lead arrives (NegotiationCreatedV4), we parse the
payload, extract what's already known, determine what's still missing,
and build a structured context block that's injected as the first "user"
message to the AI agent.

This prevents the model from asking questions that were already answered
in the lead, and lets it skip straight to price + scheduling when possible.
"""

import re


# ---------------------------------------------------------------------------
# Job type detection
# ---------------------------------------------------------------------------

_JOB_KEYWORDS: dict[str, list[str]] = {
    "tv_mounting": [
        "tv mount", "mount tv", "mounting", "television", "flat screen",
        "wall mount", "tv bracket", "mount my tv", "hang tv",
    ],
    "furniture_assembly": [
        "furniture", "assemble", "assembly", "ikea", "chair", "desk",
        "bed frame", "bookcase", "dresser", "shelf", "shelves", "couch",
        "sofa", "crib", "cabinet", "entertainment center", "tv stand",
        "dining table", "nightstand", "wardrobe",
    ],
    "fan_replacement": [
        "ceiling fan", "fan install", "fan replacement", "fan",
    ],
    "light_fixture": [
        "light fixture", "lighting", "chandelier", "pendant", "recessed",
        "flush mount", "sconce", "light install", "fixture replace",
    ],
    "appliance_installation": [
        "oven", "stove", "microwave", "dishwasher", "appliance",
        "range", "range hood", "hood install",
    ],
    "general_handyman": [
        "handyman", "repair", "fix", "patch", "drywall", "door",
        "hinge", "lock", "caulk", "misc", "various",
    ],
}


def detect_job_type(service: str, details: str) -> str:
    text = (service + " " + details).lower()
    # TV mounting checked before furniture (both can contain "tv stand")
    for job_type in [
        "tv_mounting", "furniture_assembly", "fan_replacement",
        "light_fixture", "appliance_installation", "general_handyman",
    ]:
        for kw in _JOB_KEYWORDS[job_type]:
            if kw in text:
                return job_type
    return "fallback"


# ---------------------------------------------------------------------------
# Per-job fact extractors
# ---------------------------------------------------------------------------

def _extract_tv_facts(details: str) -> dict:
    facts: dict = {}
    text = details.lower()

    m = re.search(r'(\d{2,3})\s*(?:inch|in\b|"|-inch)', text)
    if m:
        facts["tv_size"] = m.group(1) + '"'

    for wall in ["brick", "concrete", "stone", "drywall", "plaster", "wood stud"]:
        if wall in text:
            facts["wall_type"] = wall
            break

    if re.search(r"not\s+(?:above|over)\s+(?:the\s+)?fireplace|no\s+fireplace", text):
        facts["above_fireplace"] = False
    elif re.search(r"above\s+(?:the\s+)?fireplace|over\s+(?:the\s+)?fireplace|fireplace\s+wall", text):
        facts["above_fireplace"] = True

    if re.search(r"have.{0,15}mount|own.{0,10}mount|my mount", text):
        facts["has_mount"] = True
    elif re.search(r"no mount|need.{0,10}mount|don.t have.{0,10}mount", text):
        facts["has_mount"] = False

    return facts


def _extract_furniture_facts(details: str) -> dict:
    facts: dict = {}
    text = details.lower()

    m = re.search(r'(\d+)\s+(?:piece|item|furniture|chair|desk|bed|shelf|unit)', text)
    if m:
        facts["item_count"] = int(m.group(1))

    for brand in ["ikea", "wayfair", "ashley", "west elm", "cb2",
                  "restoration hardware", "pottery barn", "amazon"]:
        if brand in text:
            facts["brand"] = brand
            break

    return facts


def _extract_fan_facts(details: str) -> dict:
    facts: dict = {}
    text = details.lower()
    if re.search(r"replac|existing|swap|current fan", text):
        facts["is_replacement"] = True
    if re.search(r"new wiring|no box|no fixture|no electrical", text):
        facts["needs_new_wiring"] = True
    return facts


def _extract_light_facts(details: str) -> dict:
    facts: dict = {}
    text = details.lower()

    m = re.search(r'(\d+)\s*(?:ft|feet|foot)', text)
    if m:
        facts["ceiling_height_ft"] = int(m.group(1))

    if re.search(r"replac|existing|swap|current fixture", text):
        facts["is_replacement"] = True
    if re.search(r"new wiring|new install|no existing|run wire", text):
        facts["needs_new_wiring"] = True

    return facts


_EXTRACTORS = {
    "tv_mounting":        _extract_tv_facts,
    "furniture_assembly": _extract_furniture_facts,
    "fan_replacement":    _extract_fan_facts,
    "light_fixture":      _extract_light_facts,
}


def extract_known_facts(job_type: str, details: str) -> dict:
    extractor = _EXTRACTORS.get(job_type)
    return extractor(details) if extractor else {}


# ---------------------------------------------------------------------------
# Missing fields per job type
# ---------------------------------------------------------------------------

# (field_key, human-readable label)
_REQUIRED_FIELDS: dict[str, list[tuple[str, str]]] = {
    "tv_mounting": [
        ("tv_size",         "TV size (inches)"),
        ("wall_type",       "wall type (drywall / brick / concrete)"),
        ("above_fireplace", "whether it's above a fireplace"),
        ("has_mount",       "whether client has a mount"),
    ],
    "furniture_assembly": [
        # photo_or_link is always required — client-selected category often doesn't
        # match the actual item (e.g., "cabinets" for a 10ft kitchen island).
        # Price can only be confirmed after seeing the item.
        ("photo_or_link", "photo of the item or a product link (to confirm type and give accurate price)"),
        ("item_count",    "number of items"),
    ],
    "fan_replacement": [
        ("is_replacement", "existing fixture to replace (required for this service)"),
    ],
    "light_fixture": [
        ("ceiling_height_ft", "ceiling height"),
        ("is_replacement",    "existing fixture to replace (required for this service)"),
    ],
    "appliance_installation": [],
    "general_handyman":       [],
    "fallback":               [],
}


def get_missing_fields(job_type: str, known: dict) -> list[str]:
    return [
        label
        for key, label in _REQUIRED_FIELDS.get(job_type, [])
        if key not in known
    ]


# ---------------------------------------------------------------------------
# Out-of-scope detection
# ---------------------------------------------------------------------------

_OOS_PATTERNS: list[tuple[str, str]] = [
    (r"new wiring|new circuit|electrical panel|breaker box|run wire",
     "new electrical wiring (we do replacements only — recommend a licensed electrician)"),
    (r"\b(1[5-9]|2\d)\s*(?:ft|feet|foot)\b",
     "ceiling over 14 ft (safety policy — we don't work above 14 ft)"),
    (r"\broofing?\b|\broof repair\b",
     "roofing work (outside our scope)"),
    (r"\bplumbing\b|\bpipes?\b|\bleaky pipe\b",
     "plumbing work (outside our scope)"),
    (r"\bhvac\b|\bair.?conditioning\b|\bac unit\b",
     "HVAC / AC (outside our scope)"),
    (r"\btile\s+(?:work|install|floor)\b|\bflooring install\b",
     "tile / flooring installation (outside our scope)"),
    (r"\bpainting?\b|\bpaint\s+(?:wall|room|house)\b",
     "painting (outside our scope)"),
]


def check_out_of_scope(details: str) -> str | None:
    """Returns a human-readable reason string if out of scope, else None."""
    text = details.lower()
    for pattern, reason in _OOS_PATTERNS:
        if re.search(pattern, text):
            return reason
    return None


# ---------------------------------------------------------------------------
# Structured context builder (main export)
# ---------------------------------------------------------------------------

def _format_known(known: dict) -> str:
    parts = []
    for k, v in known.items():
        label = k.replace("_", " ")
        if isinstance(v, bool):
            parts.append(label if v else f"NOT {label}")
        else:
            parts.append(f"{label}: {v}")
    return ", ".join(parts) if parts else "nothing specific detected"


def build_lead_context(
    customer_name: str,
    service: str,
    details: str,
) -> str:
    """
    Returns a structured context block injected as the first 'user' message.
    Tells the agent what's already known vs. what to ask for — prevents
    redundant questions.
    """
    job_type = detect_job_type(service, details)
    known    = extract_known_facts(job_type, details)
    missing  = get_missing_fields(job_type, known)
    oos      = check_out_of_scope(details)

    lines = [
        "[NEW LEAD — FIRST CONTACT]",
        f"Customer name : {customer_name}",
        f"Service       : {service}",
        f"Lead details  : {details.strip() if details else '(none provided)'}",
        f"Job type      : {job_type}",
        f"Already known : {_format_known(known)}",
    ]

    if oos:
        lines += [
            f"⚠ OUT OF SCOPE : {oos}",
            "",
            "INSTRUCTION: This job is likely outside our scope.",
            "Acknowledge the request politely, explain the specific limitation (1 sentence),",
            "and suggest the appropriate specialist. Do NOT try to sell.",
            "Keep it warm and helpful. 2-3 sentences max.",
        ]
    elif not missing:
        lines += [
            "Missing info  : none — all required info is available",
            "",
            "INSTRUCTION: You have enough info to give a price right now.",
            "State the price (or starting price), mention job duration,",
            "then ask for their preferred date/time and address.",
            "Skip all clarifying questions. Be direct. 3-4 sentences max.",
        ]
    else:
        lines += [
            f"Still needed  : {', '.join(missing)}",
            "",
            f"INSTRUCTION: Ask ONLY about the first missing item: '{missing[0]}'.",
            "Do NOT ask about anything already listed under 'Already known'.",
            "Acknowledge the job briefly, then ask the one question. 2-3 sentences.",
        ]

    return "\n".join(lines)
