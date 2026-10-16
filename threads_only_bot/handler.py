import logging
from collections.abc import Mapping
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .config import Config
from .lifecycle import TopicCache, TopicState, can_manage
from .policy import is_thread_reply, is_violation
from .topics import CREATE_TOPIC_ACTION, TOPIC_COMMAND, DraftStore, draft_from_event, draft_key

log = logging.getLogger(__name__)

BROADCAST_NOTICE = (
    "Thread replies in this channel can't also be sent to the channel, so your reply was removed. "
    "Send it again without “Also send to channel”."
)
CLOSED_NOTICE = "This topic is closed, so your reply was removed."


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


def closed_topic_notice(user: str, state: TopicState, config: Config) -> str:
    if can_manage(user, state, config):
        return f"{CLOSED_NOTICE} Reopen it with the *Reopen* button on the topic to continue."
    return f"{CLOSED_NOTICE} Ask its author or participants to reopen it."


def delete_message(admin_client: WebClient, channel: str, ts: str) -> bool:
    try:
        admin_client.chat_delete(channel=channel, ts=ts)
    except SlackApiError as e:
        # Callers skip the notice then: don't tell the user their message was removed if it wasn't.
        log.error("chat.delete failed channel=%s ts=%s error=%s", channel, ts, e.response.get("error"))
        return False
    return True


def post_ephemeral(bot_client: WebClient, **notice: Any) -> None:
    try:
        bot_client.chat_postEphemeral(**notice)
    except SlackApiError as e:
        log.warning(
            "chat.postEphemeral failed channel=%s user=%s error=%s",
            notice.get("channel"),
            notice.get("user"),
            e.response.get("error"),
        )


def handle_message(
    event: Mapping[str, Any],
    config: Config,
    bot_client: WebClient,
    admin_client: WebClient,
    drafts: DraftStore,
    topics: TopicCache,
) -> None:
    if is_violation(event, config):
        remove_top_level_post(event, config, bot_client, admin_client, drafts)
    elif is_thread_reply(event, config):
        state = topics.lookup(bot_client, event["channel"], event["thread_ts"])
        if state is not None and state.closed:
            remove_closed_topic_reply(event, state, config, bot_client, admin_client)


def remove_top_level_post(
    event: Mapping[str, Any],
    config: Config,
    bot_client: WebClient,
    admin_client: WebClient,
    drafts: DraftStore,
) -> None:
    channel = event["channel"]
    user = event["user"]
    ts = event["ts"]

    if not delete_message(admin_client, channel, ts):
        return
    log.info("deleted top-level message channel=%s ts=%s user=%s", channel, ts, user)

    draft = draft_from_event(event)
    if draft:
        drafts.put(draft_key(channel, ts), draft)

    # Ephemeral: shown right where the message was, visible only to its author.
    notice: dict[str, Any] = {"channel": channel, "user": user, **build_notice(event, config)}
    if event.get("subtype") == "thread_broadcast":
        notice["thread_ts"] = event["thread_ts"]
    post_ephemeral(bot_client, **notice)


def remove_closed_topic_reply(
    event: Mapping[str, Any],
    state: TopicState,
    config: Config,
    bot_client: WebClient,
    admin_client: WebClient,
) -> None:
    channel = event["channel"]
    user = event["user"]
    ts = event["ts"]

    if not delete_message(admin_client, channel, ts):
        return
    log.info("deleted reply in closed topic channel=%s ts=%s user=%s", channel, ts, user)

    text = closed_topic_notice(user, state, config)
    post_ephemeral(bot_client, channel=channel, user=user, thread_ts=event["thread_ts"], text=text)
