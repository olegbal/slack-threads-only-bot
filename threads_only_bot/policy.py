from collections.abc import Mapping
from typing import Any

from .config import Config

# Subtypes that represent a person actually posting. Everything else (edits, deletions,
# joins, topic changes, bot_message, ...) is left alone.
USER_POST_SUBTYPES: frozenset[str | None] = frozenset({None, "file_share", "me_message", "thread_broadcast"})


def is_violation(event: Mapping[str, Any], config: Config) -> bool:
    """True if a message event is a human post outside a thread in a threads-only channel."""
    if event.get("channel") not in config.channel_ids:
        return False

    subtype = event.get("subtype")
    if subtype not in USER_POST_SUBTYPES:
        return False

    # Bots and integrations (incl. Workflow Builder) are the ones allowed to start threads.
    if event.get("bot_id") or event.get("bot_profile"):
        return False

    user = event.get("user")
    if not user or user in config.allowed_user_ids:
        return False

    # A thread reply sent "also to channel" is both in the thread and in the channel.
    if subtype == "thread_broadcast":
        return not config.allow_thread_broadcast

    thread_ts = event.get("thread_ts")
    return thread_ts is None or thread_ts == event.get("ts")
