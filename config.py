import os


class ConfigError(Exception):
    pass


class Config:
    REQUIRED = [
        "THUMBTACK_CLIENT_ID",
        "THUMBTACK_CLIENT_SECRET",
        "OPENAI_API_KEY",
        "CF_ACCOUNT_ID",
        "CF_API_TOKEN",
        "CF_KV_NS_ID",
    ]

    def __init__(self):
        missing = [k for k in self.REQUIRED if not os.getenv(k)]
        if missing:
            raise ConfigError(f"Missing required env vars: {', '.join(missing)}")

        self.thumbtack_client_id     = os.environ["THUMBTACK_CLIENT_ID"]
        self.thumbtack_client_secret = os.environ["THUMBTACK_CLIENT_SECRET"]
        self.openai_api_key          = os.environ["OPENAI_API_KEY"]
        self.cf_account_id           = os.environ["CF_ACCOUNT_ID"]
        self.cf_api_token            = os.environ["CF_API_TOKEN"]
        self.cf_kv_ns_id             = os.environ["CF_KV_NS_ID"]

        # Feature flags
        # ENABLE_VOICE_AGENT=true  — включить VAPI-звонки при новом лиде
        # ENABLE_VOICE_AGENT=false — отключить (дефолт)
        self.enable_voice_agent = os.getenv("ENABLE_VOICE_AGENT", "false").lower() == "true"

        # VAPI_API_KEY требуется только если voice включён
        if self.enable_voice_agent and not os.getenv("VAPI_API_KEY"):
            raise ConfigError("VAPI_API_KEY required when ENABLE_VOICE_AGENT=true")
        self.vapi_api_key = os.getenv("VAPI_API_KEY", "")
        self.redirect_uri            = os.getenv(
            "REDIRECT_URI",
            "https://thumbtack-oauth.onrender.com/callback",
        )

        # Thumbtack endpoints
        self.tt_auth_url  = "https://auth.thumbtack.com/oauth2/auth"
        self.tt_token_url = "https://auth.thumbtack.com/oauth2/token"
        self.tt_api_base  = "https://api.thumbtack.com/api"
        self.tt_scopes    = " ".join([
            "offline_access",
            "supply::businesses.list",
            "supply::negotiations.read",
            "supply::messages.read",
            "supply::messages.write",
            "supply::webhooks.read",
            "supply::webhooks.write",
        ])


_cfg: Config | None = None


def cfg() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config()
    return _cfg
