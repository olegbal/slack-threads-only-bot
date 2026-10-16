from collections.abc import Mapping
from typing import Any

from .config import Config

# Subtypes that represent a person actually posting. Everything else (edits, deletions,
# joins, topic changes, bot_message, ...) is left alone.
USER_POST_SUBTYPES: frozenset[str | None] = frozenset({None, "file_share", "me_message", "thread_broadcast"})


def _is_restricted_human_post(event: Mapping[str, Any], config: Config) -> bool:
    if event.get("channel") not in config.channel_ids:
        return False
    if event.get("subtype") not in USER_POST_SUBTYPES:
        return False
    # Bots and integrations (incl. Workflow Builder) are the ones allowed to start threads.
    if event.get("bot_id") or event.get("bot_profile"):
        return False
    user = event.get("user")
    return bool(user) and user not in config.allowed_user_ids


def _in_thread(event: Mapping[str, Any]) -> bool:
    thread_ts = event.get("thread_ts")
    return thread_ts is not None and thread_ts != event.get("ts")


def is_violation(event: Mapping[str, Any], config: Config) -> bool:
    """True if a message event is a human post outside a thread in a threads-only channel."""
    if not _is_restricted_human_post(event, config):
        return False
    # A thread reply sent "also to channel" is both in the thread and in the channel.
    if event.get("subtype") == "thread_broadcast":
        return not config.allow_thread_broadcast
    return not _in_thread(event)


def is_thread_reply(event: Mapping[str, Any], config: Config) -> bool:
    """True if a message event is a human reply inside a thread in a threads-only channel."""
    return _is_restricted_human_post(event, config) and _in_thread(event)
