from unittest.mock import MagicMock

from slack_sdk.errors import SlackApiError

from threads_only_bot.config import Config
from threads_only_bot.handler import build_notice, handle_message
from threads_only_bot.topics import CREATE_TOPIC_ACTION, DraftStore, draft_key

CONFIG = Config(
    bot_token="xoxb-test",
    app_token="xapp-test",
    user_token="xoxp-test",
    channel_ids=frozenset({"C_WATCHED"}),
    notice_text="Threads only.",
)

ELEMENTS = [{"type": "rich_text_section", "elements": [{"type": "text", "text": "hello "}, {"type": "user", "user_id": "U2"}]}]
DRAFT = {"type": "rich_text", "elements": ELEMENTS}


def top_level(**overrides):
    event = {
        "type": "message",
        "channel": "C_WATCHED",
        "user": "U_PERSON",
        "ts": "100.000",
        "text": "hello <@U2>",
        "blocks": [{"type": "rich_text", "block_id": "orig", "elements": ELEMENTS}],
    }
    event.update(overrides)
    return event


def clients():
    return MagicMock(name="bot_client"), MagicMock(name="admin_client")


def block_types(notice):
    return [b["type"] for b in notice["blocks"]]


def test_deletes_with_admin_token_and_shows_ephemeral_in_place():
    bot, admin = clients()
    drafts = DraftStore()

    handle_message(top_level(), CONFIG, bot, admin, drafts)

    admin.chat_delete.assert_called_once_with(channel="C_WATCHED", ts="100.000")
    kwargs = bot.chat_postEphemeral.call_args.kwargs
    assert kwargs["channel"] == "C_WATCHED"
    assert kwargs["user"] == "U_PERSON"
    assert "thread_ts" not in kwargs
    bot.chat_postMessage.assert_not_called()
    bot.chat_delete.assert_not_called()


def test_remembers_draft_for_the_create_topic_button():
    bot, admin = clients()
    drafts = DraftStore()

    handle_message(top_level(), CONFIG, bot, admin, drafts)

    assert drafts.get(draft_key("C_WATCHED", "100.000")) == DRAFT


def test_notice_is_hint_and_create_topic_button_without_text_copy():
    notice = build_notice(top_level(), CONFIG)

    assert block_types(notice) == ["section", "actions"]
    assert notice["blocks"][0]["text"]["text"].startswith("Threads only.\n")
    assert "/new-topic" in notice["blocks"][0]["text"]["text"]
    button = notice["blocks"][1]["elements"][0]
    assert button["action_id"] == CREATE_TOPIC_ACTION
    assert button["value"] == draft_key("C_WATCHED", "100.000")


def test_thread_broadcast_notice_goes_to_thread_without_button():
    bot, admin = clients()

    handle_message(top_level(subtype="thread_broadcast", thread_ts="50.000"), CONFIG, bot, admin, DraftStore())

    kwargs = bot.chat_postEphemeral.call_args.kwargs
    assert kwargs["thread_ts"] == "50.000"
    assert "actions" not in [b["type"] for b in kwargs["blocks"]]
    assert "Also send to channel" in kwargs["text"]


def test_thread_reply_is_untouched():
    bot, admin = clients()

    handle_message(top_level(thread_ts="50.000"), CONFIG, bot, admin, DraftStore())

    admin.chat_delete.assert_not_called()
    bot.chat_postEphemeral.assert_not_called()


def test_no_notice_when_delete_fails():
    bot, admin = clients()
    admin.chat_delete.side_effect = SlackApiError("cant_delete", {"ok": False, "error": "cant_delete_message"})

    handle_message(top_level(), CONFIG, bot, admin, DraftStore())

    bot.chat_postEphemeral.assert_not_called()


def test_notice_failure_does_not_raise():
    bot, admin = clients()
    bot.chat_postEphemeral.side_effect = SlackApiError("eph", {"ok": False, "error": "not_in_channel"})

    handle_message(top_level(), CONFIG, bot, admin, DraftStore())

    admin.chat_delete.assert_called_once()
