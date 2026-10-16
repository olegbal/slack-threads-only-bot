import json
from unittest.mock import MagicMock

import pytest
from slack_sdk.errors import SlackApiError

from threads_only_bot import topics
from threads_only_bot.config import Config
from threads_only_bot.topics import (
    PARTICIPANTS_BLOCK,
    PARTICIPANTS_INPUT,
    TOPIC_BLOCK,
    TOPIC_INPUT,
    TOPIC_MODAL_CALLBACK,
    NOT_A_MEMBER,
    DraftStore,
    draft_from_event,
    draft_key,
    handle_create_topic_action,
    handle_topic_command,
    handle_topic_submission,
    plain_rich_text,
    rich_text_to_plain,
)

CONFIG = Config(
    bot_token="xoxb-test",
    app_token="xapp-test",
    user_token="xoxp-test",
)

BODY = {
    "type": "rich_text",
    "elements": [
        {
            "type": "rich_text_section",
            "elements": [
                {"type": "text", "text": "Deploy at 5, "},
                {"type": "user", "user_id": "U2"},
                {"type": "text", "text": " please check "},
                {"type": "link", "url": "https://example.com"},
                {"type": "emoji", "name": "rocket"},
            ],
        }
    ],
}
KEY = draft_key("C_WATCHED", "100.000")


@pytest.fixture
def deleted_ephemerals(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(topics, "delete_ephemeral", calls.append)
    return calls


def modal_view(client):
    return client.views_open.call_args.kwargs["view"]


def submission(value, participants=None, **meta):
    values = {TOPIC_BLOCK: {TOPIC_INPUT: {"type": "rich_text_input", "rich_text_value": value}}}
    if participants is not None:
        values[PARTICIPANTS_BLOCK] = {
            PARTICIPANTS_INPUT: {"type": "multi_users_select", "selected_users": participants}
        }
    return {
        "user": {"id": "U_AUTHOR"},
        "view": {
            "callback_id": TOPIC_MODAL_CALLBACK,
            "private_metadata": json.dumps({"channel": "C_WATCHED", **meta}),
            "state": {"values": values},
        },
    }


def button_click(key=KEY, response_url="https://hooks.slack.com/actions/x"):
    return {"trigger_id": "T2", "response_url": response_url, "actions": [{"value": key}]}


def test_rich_text_to_plain():
    assert rich_text_to_plain(BODY).strip() == "Deploy at 5, <@U2> please check https://example.com:rocket:"


def test_draft_prefers_rich_text_block_over_text():
    event = {"text": "raw", "blocks": [{"type": "rich_text", "block_id": "x", "elements": BODY["elements"]}]}

    assert draft_from_event(event) == {"type": "rich_text", "elements": BODY["elements"]}


def test_draft_falls_back_to_text_and_skips_empty():
    assert draft_from_event({"text": "hi"}) == plain_rich_text("hi")
    assert draft_from_event({"text": "  "}) is None


def test_draft_store_expires_and_evicts():
    now = [0.0]
    store = DraftStore(max_items=2, ttl_seconds=10, clock=lambda: now[0])
    store.put("a", BODY)
    store.put("b", BODY)
    store.put("c", BODY)

    assert store.get("a") is None  # evicted as the oldest
    assert store.get("b") == BODY
    now[0] = 11
    assert store.get("c") is None  # expired


def test_command_in_watched_channel_opens_modal_with_text():
    ack, client = MagicMock(), MagicMock()

    handle_topic_command(ack, {"channel_id": "C_WATCHED", "trigger_id": "T1", "text": "Hello"}, client)

    ack.assert_called_once_with()
    view = modal_view(client)
    assert client.views_open.call_args.kwargs["trigger_id"] == "T1"
    assert json.loads(view["private_metadata"]) == {"channel": "C_WATCHED"}
    assert view["blocks"][1]["element"]["initial_value"] == plain_rich_text("Hello")


def test_command_without_text_opens_empty_modal():
    ack, client = MagicMock(), MagicMock()

    handle_topic_command(ack, {"channel_id": "C_WATCHED", "trigger_id": "T1", "text": ""}, client)

    assert "initial_value" not in modal_view(client)["blocks"][1]["element"]


def test_command_where_bot_is_not_a_member_explains_and_opens_nothing():
    for error in ("not_in_channel", "channel_not_found", "missing_scope"):
        ack, client = MagicMock(), MagicMock()
        client.conversations_history.side_effect = SlackApiError("hist", {"ok": False, "error": error})

        handle_topic_command(ack, {"channel_id": "C_OTHER", "trigger_id": "T1", "text": ""}, client)

        assert ack.call_args.kwargs["text"] == NOT_A_MEMBER
        client.views_open.assert_not_called()


def test_command_checks_membership_of_that_channel():
    ack, client = MagicMock(), MagicMock()

    handle_topic_command(ack, {"channel_id": "C_WATCHED", "trigger_id": "T1", "text": ""}, client)

    client.conversations_history.assert_called_once_with(channel="C_WATCHED", limit=1)
    client.views_open.assert_called_once()


def test_command_proceeds_when_membership_check_fails_for_other_reasons():
    ack, client = MagicMock(), MagicMock()
    client.conversations_history.side_effect = SlackApiError("hist", {"ok": False, "error": "ratelimited"})

    handle_topic_command(ack, {"channel_id": "C_WATCHED", "trigger_id": "T1", "text": ""}, client)

    client.views_open.assert_called_once()


def test_button_opens_modal_prefilled_from_remembered_draft():
    ack, client, drafts = MagicMock(), MagicMock(), DraftStore()
    drafts.put(KEY, BODY)

    handle_create_topic_action(ack, button_click(), client, drafts)

    ack.assert_called_once_with()
    view = modal_view(client)
    assert view["blocks"][1]["element"]["initial_value"] == BODY
    assert json.loads(view["private_metadata"]) == {
        "channel": "C_WATCHED",
        "draft_key": KEY,
        "response_url": "https://hooks.slack.com/actions/x",
    }


def test_button_after_restart_opens_empty_modal():
    ack, client = MagicMock(), MagicMock()

    handle_create_topic_action(ack, button_click(), client, DraftStore())

    assert "initial_value" not in modal_view(client)["blocks"][1]["element"]


def test_submission_publishes_topic_with_author(deleted_ephemerals):
    ack, client = MagicMock(), MagicMock()

    handle_topic_submission(ack, submission(BODY), client, DraftStore())

    ack.assert_called_once_with()
    kwargs = client.chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == "C_WATCHED"
    assert kwargs["text"].startswith("Topic by <@U_AUTHOR>: Deploy at 5")
    assert kwargs["blocks"][0]["elements"][0]["text"] == "Topic by <@U_AUTHOR>"
    assert kwargs["blocks"][1] == {"type": "rich_text", "elements": BODY["elements"]}
    assert deleted_ephemerals == []


def test_submission_from_button_cleans_up_notice_and_draft(deleted_ephemerals):
    ack, client, drafts = MagicMock(), MagicMock(), DraftStore()
    drafts.put(KEY, BODY)

    handle_topic_submission(ack, submission(BODY, draft_key=KEY, response_url="https://r"), client, drafts)

    assert deleted_ephemerals == ["https://r"]
    assert drafts.get(KEY) is None


def test_submission_rejects_blank_text():
    ack, client = MagicMock(), MagicMock()

    handle_topic_submission(ack, submission(plain_rich_text("   ")), client, DraftStore())

    assert ack.call_args.kwargs["response_action"] == "errors"
    client.chat_postMessage.assert_not_called()


def test_failed_post_keeps_notice_and_shows_error(deleted_ephemerals):
    ack, client, drafts = MagicMock(), MagicMock(), DraftStore()
    drafts.put(KEY, BODY)
    client.chat_postMessage.side_effect = SlackApiError("post", {"ok": False, "error": "not_in_channel"})

    handle_topic_submission(ack, submission(BODY, draft_key=KEY, response_url="https://r"), client, drafts)

    assert "/invite @threads-only" in ack.call_args.kwargs["errors"][TOPIC_BLOCK]
    assert deleted_ephemerals == []
    assert drafts.get(KEY) == BODY


def test_modal_has_optional_participants_picker():
    ack, client = MagicMock(), MagicMock()

    handle_topic_command(ack, {"channel_id": "C_WATCHED", "trigger_id": "T1", "text": ""}, client)

    block = next(b for b in modal_view(client)["blocks"] if b.get("block_id") == PARTICIPANTS_BLOCK)
    assert block["optional"] is True
    assert block["element"]["type"] == "multi_users_select"
    assert block["element"]["action_id"] == PARTICIPANTS_INPUT


def test_submission_mentions_participants_without_author_and_duplicates(deleted_ephemerals):
    ack, client = MagicMock(), MagicMock()

    handle_topic_submission(
        ack, submission(BODY, participants=["U_A", "U_AUTHOR", "U_B", "U_A"]), client, DraftStore()
    )

    kwargs = client.chat_postMessage.call_args.kwargs
    header = "Topic by <@U_AUTHOR> · Participants: <@U_A>, <@U_B>"
    assert kwargs["blocks"][0]["elements"][0]["text"] == header
    assert kwargs["text"].startswith(f"{header}: Deploy at 5")


def test_submission_with_empty_participants_has_plain_header(deleted_ephemerals):
    ack, client = MagicMock(), MagicMock()

    handle_topic_submission(ack, submission(BODY, participants=[]), client, DraftStore())

    assert client.chat_postMessage.call_args.kwargs["blocks"][0]["elements"][0]["text"] == "Topic by <@U_AUTHOR>"


def test_other_post_errors_are_shown_as_is():
    ack, client = MagicMock(), MagicMock()
    client.chat_postMessage.side_effect = SlackApiError("post", {"ok": False, "error": "msg_too_long"})

    handle_topic_submission(ack, submission(BODY), client, DraftStore())

    assert "msg_too_long" in ack.call_args.kwargs["errors"][TOPIC_BLOCK]
