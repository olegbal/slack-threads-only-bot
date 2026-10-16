"""Creating new topics: /new-topic command, "Create topic" button next to a removed message, and the modal behind both."""

import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.webhook import WebhookClient

from .config import Config
from .images import IMAGE_TYPES, MAX_IMAGES, TopicImage, copy_images, image_block, is_supported_image, post_with_retry

log = logging.getLogger(__name__)

TOPIC_COMMAND = "/new-topic"
CREATE_TOPIC_ACTION = "create_topic"
TOPIC_MODAL_CALLBACK = "new_topic"
TOPIC_BLOCK = "topic"
TOPIC_INPUT = "topic_text"
PARTICIPANTS_BLOCK = "participants"
PARTICIPANTS_INPUT = "participants_select"
IMAGES_BLOCK = "images"
IMAGES_INPUT = "images_upload"
# Keeps the "Close topic" button value (author + participants JSON) under Slack's 2000-char limit.
MAX_PARTICIPANTS = 50

CLOSE_TOPIC_ACTION = "close_topic"
REOPEN_TOPIC_ACTION = "reopen_topic"
TOPIC_ACTIONS_BLOCK = "topic_actions"
TOPIC_STATUS_BLOCK = "topic_status"

Ack = Callable[..., Any]
RichText = dict[str, Any]


class DraftStore:
    """Texts of removed messages, used to prefill the topic form.

    Slack doesn't include an ephemeral message's content in button payloads, so the bot has to
    remember it. Kept in memory: after a restart the form just opens empty.
    """

    def __init__(self, max_items: int = 1000, ttl_seconds: float = 24 * 3600, clock: Callable[[], float] = time.monotonic):
        self._items: OrderedDict[str, tuple[float, RichText]] = OrderedDict()
        self._max_items = max_items
        self._ttl = ttl_seconds
        self._clock = clock

    def put(self, key: str, draft: RichText) -> None:
        self._items[key] = (self._clock(), draft)
        self._items.move_to_end(key)
        while len(self._items) > self._max_items:
            self._items.popitem(last=False)

    def get(self, key: str) -> RichText | None:
        item = self._items.get(key)
        if item is None:
            return None
        created, draft = item
        if self._clock() - created > self._ttl:
            del self._items[key]
            return None
        return draft

    def discard(self, key: str) -> None:
        self._items.pop(key, None)


def draft_key(channel: str, ts: str) -> str:
    return f"{channel}/{ts}"


def plain_rich_text(text: str) -> RichText:
    return {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}]}


def draft_from_event(event: Mapping[str, Any]) -> RichText | None:
    """The author's message as rich text, keeping formatting and mentions when Slack provides blocks."""
    for block in event.get("blocks") or []:
        if block.get("type") == "rich_text":
            return {"type": "rich_text", "elements": block["elements"]}
    text = event.get("text") or ""
    return plain_rich_text(text) if text.strip() else None


def rich_text_to_plain(node: Any) -> str:
    if isinstance(node, list):
        return "".join(rich_text_to_plain(child) for child in node)
    if not isinstance(node, Mapping):
        return ""
    kind = node.get("type")
    if kind == "text":
        return str(node.get("text", ""))
    if kind == "link":
        return str(node.get("text") or node.get("url", ""))
    if kind == "user":
        return f"<@{node.get('user_id')}>"
    if kind == "channel":
        return f"<#{node.get('channel_id')}>"
    if kind == "emoji":
        return f":{node.get('name')}:"
    text = rich_text_to_plain(node.get("elements", []))
    # Keep paragraphs, lists and quotes on separate lines in the notification preview.
    return f"{text}\n" if kind in {"rich_text_section", "rich_text_list", "rich_text_quote", "rich_text_preformatted"} else text


def topic_modal(channel_id: str, draft: RichText | None = None, **meta: str) -> dict[str, Any]:
    element: dict[str, Any] = {"type": "rich_text_input", "action_id": TOPIC_INPUT, "focus_on_load": True}
    if draft:
        element["initial_value"] = draft
    return {
        "type": "modal",
        "callback_id": TOPIC_MODAL_CALLBACK,
        "private_metadata": json.dumps({"channel": channel_id, **meta}),
        "title": {"type": "plain_text", "text": "New topic"},
        "submit": {"type": "plain_text", "text": "Publish"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"The topic will be posted in <#{channel_id}> with you as the author. Discussion continues in its thread.",
                },
            },
            {
                "type": "input",
                "block_id": TOPIC_BLOCK,
                "label": {"type": "plain_text", "text": "Topic"},
                "element": element,
            },
            {
                "type": "input",
                "block_id": IMAGES_BLOCK,
                "optional": True,
                "label": {"type": "plain_text", "text": "Images"},
                "hint": {"type": "plain_text", "text": "PNG, JPG or GIF. Shown in the topic under its text."},
                "element": {
                    "type": "file_input",
                    "action_id": IMAGES_INPUT,
                    "filetypes": sorted(IMAGE_TYPES),
                    "max_files": MAX_IMAGES,
                },
            },
            {
                "type": "input",
                "block_id": PARTICIPANTS_BLOCK,
                "optional": True,
                "label": {"type": "plain_text", "text": "Participants"},
                "hint": {"type": "plain_text", "text": "They'll be mentioned in the topic and notified."},
                "element": {
                    "type": "multi_users_select",
                    "action_id": PARTICIPANTS_INPUT,
                    "max_selected_items": MAX_PARTICIPANTS,
                    "placeholder": {"type": "plain_text", "text": "Who should join the discussion"},
                },
            },
        ],
    }


def topic_controls(author_id: str, participants: Sequence[str], closed: bool) -> dict[str, Any]:
    """The Close/Reopen button. Its value is the topic's source of truth for who may manage it."""
    button: dict[str, Any] = {
        "type": "button",
        "action_id": REOPEN_TOPIC_ACTION if closed else CLOSE_TOPIC_ACTION,
        "text": {"type": "plain_text", "text": "Reopen" if closed else "Close topic"},
        "value": json.dumps({"a": author_id, "p": list(participants)}, separators=(",", ":")),
    }
    return {"type": "actions", "block_id": TOPIC_ACTIONS_BLOCK, "elements": [button]}


def topic_message(
    author_id: str, body: RichText, participants: Sequence[str] = (), images: Sequence[TopicImage] = ()
) -> dict[str, Any]:
    preview = rich_text_to_plain(body).strip()
    header = f"Topic by <@{author_id}>"
    # Mentions notify participants and subscribe them to the thread.
    others = list(dict.fromkeys(p for p in participants if p != author_id))
    if others:
        header += " · Participants: " + ", ".join(f"<@{p}>" for p in others)
    return {
        "text": f"{header}: {preview}",
        "blocks": [
            {"type": "context", "elements": [{"type": "mrkdwn", "text": header}]},
            {"type": "rich_text", "elements": body.get("elements", [])},
            *(image_block(image) for image in images),
            topic_controls(author_id, others, closed=False),
        ],
    }


def channels_hint(config: Config) -> str:
    return ", ".join(f"<#{channel}>" for channel in sorted(config.channel_ids))


def delete_ephemeral(response_url: str) -> None:
    try:
        WebhookClient(response_url).send_dict({"delete_original": True})
    except Exception as e:  # cleanup only; the topic is already published
        log.warning("failed to remove ephemeral notice: %s", e)


def handle_topic_command(ack: Ack, command: Mapping[str, Any], config: Config, client: WebClient) -> None:
    channel = command["channel_id"]
    if channel not in config.channel_ids:
        ack(text=f"{TOPIC_COMMAND} only works in: {channels_hint(config)}")
        return
    ack()
    text = (command.get("text") or "").strip()
    client.views_open(trigger_id=command["trigger_id"], view=topic_modal(channel, plain_rich_text(text) if text else None))


def handle_create_topic_action(
    ack: Ack, body: Mapping[str, Any], config: Config, client: WebClient, drafts: DraftStore
) -> None:
    ack()
    key = body["actions"][0]["value"]
    channel = key.split("/", 1)[0]
    if channel not in config.channel_ids:
        return
    meta = {"draft_key": key}
    if body.get("response_url"):
        meta["response_url"] = body["response_url"]
    client.views_open(trigger_id=body["trigger_id"], view=topic_modal(channel, drafts.get(key), **meta))


def handle_topic_submission(
    ack: Ack, body: Mapping[str, Any], config: Config, client: WebClient, drafts: DraftStore
) -> None:
    view = body["view"]
    meta = json.loads(view["private_metadata"])
    channel = meta["channel"]
    author = body["user"]["id"]
    values = view["state"]["values"]
    value: RichText | None = values[TOPIC_BLOCK][TOPIC_INPUT].get("rich_text_value")
    participants: list[str] = values.get(PARTICIPANTS_BLOCK, {}).get(PARTICIPANTS_INPUT, {}).get("selected_users") or []
    files: list[dict[str, Any]] = values.get(IMAGES_BLOCK, {}).get(IMAGES_INPUT, {}).get("files") or []

    if channel not in config.channel_ids:
        ack(response_action="errors", errors={TOPIC_BLOCK: "This channel is no longer managed by the bot."})
        return
    if not value or not rich_text_to_plain(value).strip():
        ack(response_action="errors", errors={TOPIC_BLOCK: "Please enter the topic text."})
        return
    if not all(is_supported_image(f) for f in files):
        ack(response_action="errors", errors={IMAGES_BLOCK: "Only PNG, JPG and GIF images are supported."})
        return

    if not files:
        try:
            client.chat_postMessage(channel=channel, **topic_message(author, value, participants))
        except SlackApiError as e:
            error = e.response.get("error")
            log.error("topic post failed channel=%s user=%s error=%s", channel, author, error)
            ack(response_action="errors", errors={TOPIC_BLOCK: f"Couldn't publish the topic ({error})."})
            return
        ack()
    else:
        # Copying images can take longer than the 3 seconds Slack waits for the form, so close it first
        # and report a failure to the author separately.
        ack()
        try:
            images = copy_images(client, files)
            post_with_retry(client, channel=channel, **topic_message(author, value, participants, images))
        except (SlackApiError, OSError) as e:
            error = e.response.get("error") if isinstance(e, SlackApiError) else str(e)
            log.error("topic with images failed channel=%s user=%s error=%s", channel, author, error)
            notify_author(client, channel, author, f"Couldn't publish your topic with images ({error}). Please try again.")
            return

    log.info(
        "published topic channel=%s user=%s participants=%d images=%d", channel, author, len(participants), len(files)
    )
    if meta.get("draft_key"):
        drafts.discard(meta["draft_key"])
    if meta.get("response_url"):
        delete_ephemeral(meta["response_url"])


def notify_author(client: WebClient, channel: str, user: str, text: str) -> None:
    try:
        client.chat_postEphemeral(channel=channel, user=user, text=text)
    except SlackApiError as e:
        log.warning("chat.postEphemeral failed channel=%s user=%s error=%s", channel, user, e.response.get("error"))
