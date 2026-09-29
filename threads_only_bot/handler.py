import logging
from collections.abc import Mapping
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .config import Config
from .policy import is_violation
from .topics import CREATE_TOPIC_ACTION, TOPIC_COMMAND, DraftStore, draft_from_event, draft_key

log = logging.getLogger(__name__)

BROADCAST_NOTICE = (
    "Thread replies in this channel can't also be sent to the channel, so your reply was removed. "
    "Send it again without “Also send to channel”."
)


def build_notice(event: Mapping[str, Any], config: Config) -> dict[str, Any]:
    broadcast = event.get("subtype") == "thread_broadcast"
    lead = BROADCAST_NOTICE if broadcast else config.notice_text
    hint = "" if broadcast else f"\nStart a new topic with the button below or `{TOPIC_COMMAND}`, and reply in threads."

    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": f"{lead}{hint}"}}]

    if not broadcast:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": CREATE_TOPIC_ACTION,
                        "text": {"type": "plain_text", "text": "Create topic"},
                        "style": "primary",
                        "value": draft_key(event["channel"], event["ts"]),
                    }
                ],
            }
        )

    return {"text": lead, "blocks": blocks}


def handle_message(
    event: Mapping[str, Any],
    config: Config,
    bot_client: WebClient,
    admin_client: WebClient,
    drafts: DraftStore,
) -> None:
    if not is_violation(event, config):
        return

    channel = event["channel"]
    user = event["user"]
    ts = event["ts"]

    try:
        admin_client.chat_delete(channel=channel, ts=ts)
    except SlackApiError as e:
        # Don't tell the user their message was removed if it wasn't.
        log.error("chat.delete failed channel=%s ts=%s error=%s", channel, ts, e.response.get("error"))
        return

    log.info("deleted top-level message channel=%s ts=%s user=%s", channel, ts, user)

    draft = draft_from_event(event)
    if draft:
        drafts.put(draft_key(channel, ts), draft)

    # Ephemeral: shown right where the message was, visible only to its author.
    notice: dict[str, Any] = {"channel": channel, "user": user, **build_notice(event, config)}
    if event.get("subtype") == "thread_broadcast":
        notice["thread_ts"] = event["thread_ts"]

    try:
        bot_client.chat_postEphemeral(**notice)
    except SlackApiError as e:
        log.warning("chat.postEphemeral failed channel=%s user=%s error=%s", channel, user, e.response.get("error"))
