"""Closing and reopening topics.

The topic message itself holds the state: the Close/Reopen button carries author and participants,
and a status block shows who closed it and why. No storage needed, and it survives restarts.
"""

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .config import Config
from .topics import (
    CLOSE_TOPIC_ACTION,
    REOPEN_TOPIC_ACTION,
    TOPIC_ACTIONS_BLOCK,
    TOPIC_STATUS_BLOCK,
    Ack,
    topic_controls,
)

log = logging.getLogger(__name__)

CLOSE_MODAL_CALLBACK = "close_topic"
CONCLUSION_BLOCK = "conclusion"
CONCLUSION_INPUT = "conclusion_text"
NO_PERMISSION = "Only the topic's author and participants can close or reopen it."


@dataclass(frozen=True)
class TopicState:
    author: str
    participants: tuple[str, ...]
    closed: bool


def topic_state(blocks: list[Mapping[str, Any]]) -> TopicState | None:
    """Read the state from a topic message; None for anything that isn't a bot-created topic."""
    for block in blocks:
        if block.get("block_id") != TOPIC_ACTIONS_BLOCK:
            continue
        for element in block.get("elements", []):
            action = element.get("action_id")
            if action in {CLOSE_TOPIC_ACTION, REOPEN_TOPIC_ACTION}:
                data = json.loads(element["value"])
                return TopicState(data["a"], tuple(data.get("p", [])), closed=action == REOPEN_TOPIC_ACTION)
    return None


def can_manage(user: str, state: TopicState, config: Config) -> bool:
    return user == state.author or user in state.participants or user in config.allowed_user_ids


def escape_mrkdwn(text: str) -> str:
    # Stops a conclusion like "<!channel>" from pinging everyone.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_date(ts: int) -> str:
    fallback = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"<!date^{ts}^{{date_short_pretty}} {{time}}|{fallback}>"


def status_block(user: str, conclusion: str, now: int) -> dict[str, Any]:
    text = f"🔒 *Closed* by <@{user}>, {slack_date(now)}"
    if conclusion:
        text += f"\n*Conclusion:* {escape_mrkdwn(conclusion)}"
    return {"type": "context", "block_id": TOPIC_STATUS_BLOCK, "elements": [{"type": "mrkdwn", "text": text}]}


def thread_note(user: str, closed: bool, conclusion: str) -> str:
    if not closed:
        return f"🔓 <@{user}> reopened the topic."
    note = f"🔒 <@{user}> closed the topic."
    return f"{note}\n*Conclusion:* {escape_mrkdwn(conclusion)}" if conclusion else note


def with_state(blocks: list[dict[str, Any]], state: TopicState, closed: bool, status: dict[str, Any] | None) -> list[dict[str, Any]]:
    kept = [b for b in blocks if b.get("block_id") not in {TOPIC_STATUS_BLOCK, TOPIC_ACTIONS_BLOCK}]
    return kept + ([status] if status else []) + [topic_controls(state.author, state.participants, closed)]


def close_modal(channel: str, ts: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": CLOSE_MODAL_CALLBACK,
        "private_metadata": json.dumps({"channel": channel, "ts": ts}),
        "title": {"type": "plain_text", "text": "Close topic"},
        "submit": {"type": "plain_text", "text": "Close"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": CONCLUSION_BLOCK,
                "optional": True,
                "label": {"type": "plain_text", "text": "Conclusion"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": CONCLUSION_INPUT,
                    "multiline": True,
                    "max_length": 500,
                    "placeholder": {"type": "plain_text", "text": "What was decided or how it was resolved"},
                },
            }
        ],
    }


def fetch_message(client: WebClient, channel: str, ts: str) -> dict[str, Any] | None:
    response = client.conversations_history(channel=channel, latest=ts, inclusive=True, limit=1)
    messages = response.get("messages") or []
    return messages[0] if messages and messages[0].get("ts") == ts else None


def apply_state(
    client: WebClient,
    channel: str,
    message: Mapping[str, Any],
    state: TopicState,
    user: str,
    *,
    closed: bool,
    conclusion: str = "",
) -> None:
    ts = message["ts"]
    status = status_block(user, conclusion, int(time.time())) if closed else None
    try:
        client.chat_update(
            channel=channel,
            ts=ts,
            text=message.get("text", ""),
            blocks=with_state(list(message.get("blocks", [])), state, closed, status),
        )
    except SlackApiError as e:
        log.error("topic update failed channel=%s ts=%s error=%s", channel, ts, e.response.get("error"))
        notify(client, channel, user, "Couldn't update the topic status, please try again.")
        return

    log.info("topic %s channel=%s ts=%s by=%s", "closed" if closed else "reopened", channel, ts, user)
    try:
        # A note in the thread keeps the history and notifies everyone following it.
        client.chat_postMessage(channel=channel, thread_ts=ts, text=thread_note(user, closed, conclusion))
    except SlackApiError as e:
        log.warning("thread note failed channel=%s ts=%s error=%s", channel, ts, e.response.get("error"))


def notify(client: WebClient, channel: str, user: str, text: str) -> None:
    try:
        client.chat_postEphemeral(channel=channel, user=user, text=text)
    except SlackApiError as e:
        log.warning("chat.postEphemeral failed channel=%s user=%s error=%s", channel, user, e.response.get("error"))


def _clicked_topic(body: Mapping[str, Any], config: Config, client: WebClient) -> tuple[str, dict[str, Any], TopicState] | None:
    user = body["user"]["id"]
    channel = body["channel"]["id"]
    message = body.get("message") or {}
    state = topic_state(message.get("blocks", []))
    if state is None:
        return None
    if not can_manage(user, state, config):
        notify(client, channel, user, NO_PERMISSION)
        return None
    return channel, message, state


def handle_close_click(ack: Ack, body: Mapping[str, Any], config: Config, client: WebClient) -> None:
    ack()
    clicked = _clicked_topic(body, config, client)
    if clicked is None:
        return
    channel, message, state = clicked
    if state.closed:  # stale button from an outdated view of the message
        return
    client.views_open(trigger_id=body["trigger_id"], view=close_modal(channel, message["ts"]))


def handle_reopen_click(ack: Ack, body: Mapping[str, Any], config: Config, client: WebClient) -> None:
    ack()
    clicked = _clicked_topic(body, config, client)
    if clicked is None:
        return
    channel, message, state = clicked
    if not state.closed:
        return
    apply_state(client, channel, message, state, body["user"]["id"], closed=False)


def handle_close_submission(ack: Ack, body: Mapping[str, Any], config: Config, client: WebClient) -> None:
    view = body["view"]
    meta = json.loads(view["private_metadata"])
    channel, ts = meta["channel"], meta["ts"]
    user = body["user"]["id"]
    conclusion = (view["state"]["values"].get(CONCLUSION_BLOCK, {}).get(CONCLUSION_INPUT, {}).get("value") or "").strip()

    # Re-read the message: it may have been closed by someone else while the form was open.
    try:
        message = fetch_message(client, channel, ts)
    except SlackApiError as e:
        log.error("topic fetch failed channel=%s ts=%s error=%s", channel, ts, e.response.get("error"))
        message = None
    state = topic_state(message.get("blocks", [])) if message else None

    if message is None or state is None:
        ack(response_action="errors", errors={CONCLUSION_BLOCK: "Topic not found. It may have been deleted."})
        return
    if not can_manage(user, state, config):
        ack(response_action="errors", errors={CONCLUSION_BLOCK: NO_PERMISSION})
        return
    ack()
    if state.closed:
        return
    apply_state(client, channel, message, state, user, closed=True, conclusion=conclusion)
