import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_NOTICE = "This channel is threads-only, so your message was removed."


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    app_token: str
    user_token: str
    channel_ids: frozenset[str]
    allowed_user_ids: frozenset[str] = frozenset()
    allow_thread_broadcast: bool = False
    notice_text: str = DEFAULT_NOTICE


def _id_set(raw: str | None) -> frozenset[str]:
    return frozenset(part.strip() for part in (raw or "").split(",") if part.strip())


def _required(env: Mapping[str, str], name: str, prefix: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set")
    if not value.startswith(prefix):
        raise ConfigError(f"{name} must start with {prefix}")
    return value


def load_config(env: Mapping[str, str] = os.environ) -> Config:
    channel_ids = _id_set(env.get("CHANNEL_IDS"))
    if not channel_ids:
        raise ConfigError("CHANNEL_IDS is not set (comma-separated channel IDs, e.g. C0123ABCD)")

    return Config(
        bot_token=_required(env, "SLACK_BOT_TOKEN", "xoxb-"),
        app_token=_required(env, "SLACK_APP_TOKEN", "xapp-"),
        # Bots can't delete other people's messages; only a workspace owner/admin user token can.
        user_token=_required(env, "SLACK_ADMIN_USER_TOKEN", "xoxp-"),
        channel_ids=channel_ids,
        allowed_user_ids=_id_set(env.get("ALLOWED_USER_IDS")),
        allow_thread_broadcast=env.get("ALLOW_THREAD_BROADCAST", "false").strip().lower() in {"1", "true", "yes"},
        notice_text=env.get("NOTICE_TEXT", "").strip() or DEFAULT_NOTICE,
    )
