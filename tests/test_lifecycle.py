import json
from dataclasses import replace
from unittest.mock import MagicMock

from slack_sdk.errors import SlackApiError

from threads_only_bot.config import Config
from threads_only_bot.lifecycle import (
    CLOSE_MODAL_CALLBACK,
    NO_PERMISSION,
    CONCLUSION_BLOCK,
    CONCLUSION_INPUT,
    TopicState,
    handle_close_click,
    handle_close_submission,
    handle_reopen_click,
    status_block,
    topic_state,
    with_state,
)
from threads_only_bot.topics import (
    CLOSE_TOPIC_ACTION,
    REOPEN_TOPIC_ACTION,
    TOPIC_ACTIONS_BLOCK,
    TOPIC_STATUS_BLOCK,
    plain_rich_text,
    topic_message,
)

CONFIG = Config(
    bot_token="xoxb-test",
    app_token="xapp-test",
    user_token="xoxp-test",
    channel_ids=frozenset({"C_WATCHED"}),
    allowed_user_ids=frozenset({"U_ADMIN"}),
)
TS = "200.000"


def open_topic():
    msg = topic_message("U_AUTHOR", plain_rich_text("Deploy"), ["U_PART"])
    return {"ts": TS, "text": msg["text"], "blocks": msg["blocks"]}


def closed_topic():
    topic = open_topic()
    state = topic_state(topic["blocks"])
    topic["blocks"] = with_state(topic["blocks"], state, True, status_block("U_AUTHOR", "done", 1_700_000_000))
    return topic


def click(message, user="U_AUTHOR"):
    return {"user": {"id": user}, "channel": {"id": "C_WATCHED"}, "trigger_id": "T1", "message": message}


def close_submit(user="U_AUTHOR", conclusion="Resolved"):
    return {
        "user": {"id": user},
        "view": {
            "callback_id": CLOSE_MODAL_CALLBACK,
            "private_metadata": json.dumps({"channel": "C_WATCHED", "ts": TS}),
            "state": {"values": {CONCLUSION_BLOCK: {CONCLUSION_INPUT: {"type": "plain_text_input", "value": conclusion}}}},
        },
    }


def client_with(message):
    client = MagicMock()
    client.conversations_history.return_value = {"messages": [message] if message else []}
    return client


def block_ids(blocks):
    return [b.get("block_id") for b in blocks]


def button(blocks):
    return next(b for b in blocks if b.get("block_id") == TOPIC_ACTIONS_BLOCK)["elements"][0]


def test_new_topic_has_close_button_with_author_and_participants():
    topic = open_topic()

    assert button(topic["blocks"])["action_id"] == CLOSE_TOPIC_ACTION
    assert topic_state(topic["blocks"]) == TopicState("U_AUTHOR", ("U_PART",), closed=False)


def test_topic_state_ignores_foreign_messages():
    assert topic_state([{"type": "section"}]) is None


def test_author_click_opens_close_form():
    ack, client = MagicMock(), MagicMock()

    handle_close_click(ack, click(open_topic()), CONFIG, client)

    ack.assert_called_once_with()
    view = client.views_open.call_args.kwargs["view"]
    assert view["callback_id"] == CLOSE_MODAL_CALLBACK
    assert json.loads(view["private_metadata"]) == {"channel": "C_WATCHED", "ts": TS}
    assert view["blocks"][0]["optional"] is True


def test_participant_and_allowed_user_can_close():
    for user in ("U_PART", "U_ADMIN"):
        client = MagicMock()
        handle_close_click(MagicMock(), click(open_topic(), user=user), CONFIG, client)
        client.views_open.assert_called_once()


def test_stranger_gets_ephemeral_refusal():
    client = MagicMock()

    handle_close_click(MagicMock(), click(open_topic(), user="U_STRANGER"), CONFIG, client)

    client.views_open.assert_not_called()
    kwargs = client.chat_postEphemeral.call_args.kwargs
    assert kwargs["user"] == "U_STRANGER"
    assert kwargs["text"] == NO_PERMISSION


def test_close_submission_updates_topic_and_notes_thread():
    ack, client = MagicMock(), client_with(open_topic())

    handle_close_submission(ack, close_submit(conclusion="Resolved <!channel>"), CONFIG, client)

    ack.assert_called_once_with()
    update = client.chat_update.call_args.kwargs
    assert update["channel"] == "C_WATCHED" and update["ts"] == TS
    blocks = update["blocks"]
    assert block_ids(blocks)[-2:] == [TOPIC_STATUS_BLOCK, TOPIC_ACTIONS_BLOCK]
    status = blocks[-2]["elements"][0]["text"]
    assert "<@U_AUTHOR>" in status
    assert "Resolved &lt;!channel&gt;" in status  # no @channel ping via the conclusion
    assert button(blocks)["action_id"] == REOPEN_TOPIC_ACTION
    assert topic_state(blocks) == TopicState("U_AUTHOR", ("U_PART",), closed=True)

    note = client.chat_postMessage.call_args.kwargs
    assert note["thread_ts"] == TS
    assert "closed the topic" in note["text"]
    assert "*Conclusion:* Resolved" in note["text"]


def test_close_without_conclusion():
    client = client_with(open_topic())

    handle_close_submission(MagicMock(), close_submit(conclusion=""), CONFIG, client)

    status = client.chat_update.call_args.kwargs["blocks"][-2]["elements"][0]["text"]
    assert "Conclusion" not in status
    assert "Conclusion" not in client.chat_postMessage.call_args.kwargs["text"]


def test_close_submission_rechecks_permission():
    ack, client = MagicMock(), client_with(open_topic())

    handle_close_submission(ack, close_submit(user="U_STRANGER"), CONFIG, client)

    assert ack.call_args.kwargs["errors"][CONCLUSION_BLOCK] == NO_PERMISSION
    client.chat_update.assert_not_called()


def test_close_submission_for_missing_topic_shows_error():
    ack, client = MagicMock(), client_with(None)

    handle_close_submission(ack, close_submit(), CONFIG, client)

    assert ack.call_args.kwargs["response_action"] == "errors"
    client.chat_update.assert_not_called()


def test_already_closed_topic_is_not_closed_twice():
    ack, client = MagicMock(), client_with(closed_topic())

    handle_close_submission(ack, close_submit(), CONFIG, client)

    ack.assert_called_once_with()
    client.chat_update.assert_not_called()


def test_reopen_removes_status_and_restores_close_button():
    client = MagicMock()

    handle_reopen_click(MagicMock(), click(closed_topic(), user="U_PART"), CONFIG, client)

    blocks = client.chat_update.call_args.kwargs["blocks"]
    assert TOPIC_STATUS_BLOCK not in block_ids(blocks)
    assert button(blocks)["action_id"] == CLOSE_TOPIC_ACTION
    assert "reopened the topic" in client.chat_postMessage.call_args.kwargs["text"]


def test_reopen_by_stranger_is_refused():
    client = MagicMock()

    handle_reopen_click(MagicMock(), click(closed_topic(), user="U_STRANGER"), CONFIG, client)

    client.chat_update.assert_not_called()
    assert client.chat_postEphemeral.call_args.kwargs["text"] == NO_PERMISSION


def test_failed_update_tells_user_and_skips_thread_note():
    client = client_with(open_topic())
    client.chat_update.side_effect = SlackApiError("upd", {"ok": False, "error": "cant_update_message"})

    handle_close_submission(MagicMock(), close_submit(), CONFIG, client)

    client.chat_postMessage.assert_not_called()
    assert client.chat_postEphemeral.call_args.kwargs["user"] == "U_AUTHOR"


def test_allowed_users_config_is_respected():
    config = replace(CONFIG, allowed_user_ids=frozenset())
    client = MagicMock()

    handle_close_click(MagicMock(), click(open_topic(), user="U_ADMIN"), config, client)

    client.views_open.assert_not_called()
