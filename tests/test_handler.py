from dataclasses import replace
from unittest.mock import MagicMock

from slack_sdk.errors import SlackApiError

from threads_only_bot.config import Config
from threads_only_bot.handler import CLOSED_NOTICE, build_notice, handle_message
from threads_only_bot.lifecycle import TopicCache, status_block, topic_state, with_state
from threads_only_bot.topics import CREATE_TOPIC_ACTION, DraftStore, draft_key, plain_rich_text, topic_message

CONFIG = Config(
    bot_token="xoxb-test",
    app_token="xapp-test",
    user_token="xoxp-test",
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

    handle_message(top_level(), CONFIG, bot, admin, drafts, TopicCache())

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

    handle_message(top_level(), CONFIG, bot, admin, drafts, TopicCache())

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

    handle_message(top_level(subtype="thread_broadcast", thread_ts="50.000"), CONFIG, bot, admin, DraftStore(), TopicCache())

    kwargs = bot.chat_postEphemeral.call_args.kwargs
    assert kwargs["thread_ts"] == "50.000"
    assert "actions" not in [b["type"] for b in kwargs["blocks"]]
    assert "Also send to channel" in kwargs["text"]


def test_thread_reply_is_untouched():
    bot, admin = clients()

    handle_message(top_level(thread_ts="50.000"), CONFIG, bot, admin, DraftStore(), TopicCache())

    admin.chat_delete.assert_not_called()
    bot.chat_postEphemeral.assert_not_called()


def test_no_notice_when_delete_fails():
    bot, admin = clients()
    admin.chat_delete.side_effect = SlackApiError("cant_delete", {"ok": False, "error": "cant_delete_message"})

    handle_message(top_level(), CONFIG, bot, admin, DraftStore(), TopicCache())

    bot.chat_postEphemeral.assert_not_called()


def test_notice_failure_does_not_raise():
    bot, admin = clients()
    bot.chat_postEphemeral.side_effect = SlackApiError("eph", {"ok": False, "error": "not_in_channel"})

    handle_message(top_level(), CONFIG, bot, admin, DraftStore(), TopicCache())

    admin.chat_delete.assert_called_once()


TOPIC_TS = "50.000"


def topic(closed):
    msg = topic_message("U_AUTHOR", plain_rich_text("Deploy"), ["U_PART"])
    blocks = msg["blocks"]
    if closed:
        status = status_block("U_AUTHOR", "", 1_700_000_000)
        blocks = with_state(blocks, topic_state(blocks), True, status)
    return {"ts": TOPIC_TS, "text": msg["text"], "blocks": blocks}


def bot_seeing(parent):
    bot = MagicMock(name="bot_client")
    bot.conversations_history.return_value = {"messages": [parent]}
    return bot


def reply(user="U_PERSON", **overrides):
    return top_level(**{"user": user, "ts": "60.000", "thread_ts": TOPIC_TS, **overrides})


def test_reply_in_closed_topic_is_removed_with_notice_in_thread():
    bot, admin = bot_seeing(topic(closed=True)), MagicMock()

    handle_message(reply(), CONFIG, bot, admin, DraftStore(), TopicCache())

    admin.chat_delete.assert_called_once_with(channel="C_WATCHED", ts="60.000")
    kwargs = bot.chat_postEphemeral.call_args.kwargs
    assert kwargs["thread_ts"] == TOPIC_TS
    assert kwargs["user"] == "U_PERSON"
    assert kwargs["text"].startswith(CLOSED_NOTICE)
    assert "Ask its author or participants" in kwargs["text"]


def test_participant_is_pointed_to_reopen_button():
    bot, admin = bot_seeing(topic(closed=True)), MagicMock()

    handle_message(reply(user="U_PART"), CONFIG, bot, admin, DraftStore(), TopicCache())

    admin.chat_delete.assert_called_once()
    assert "*Reopen*" in bot.chat_postEphemeral.call_args.kwargs["text"]


def test_file_reply_in_closed_topic_is_removed():
    bot, admin = bot_seeing(topic(closed=True)), MagicMock()

    handle_message(reply(subtype="file_share"), CONFIG, bot, admin, DraftStore(), TopicCache())

    admin.chat_delete.assert_called_once()


def test_reply_in_open_topic_is_untouched():
    bot, admin = bot_seeing(topic(closed=False)), MagicMock()

    handle_message(reply(), CONFIG, bot, admin, DraftStore(), TopicCache())

    admin.chat_delete.assert_not_called()
    bot.chat_postEphemeral.assert_not_called()


def test_reply_in_ordinary_thread_is_untouched():
    bot, admin = bot_seeing({"ts": TOPIC_TS, "text": "alert", "bot_id": "B1"}), MagicMock()

    handle_message(reply(), CONFIG, bot, admin, DraftStore(), TopicCache())

    admin.chat_delete.assert_not_called()


def test_bot_and_allowed_user_replies_in_closed_topic_are_untouched():
    config = replace(CONFIG, allowed_user_ids=frozenset({"U_ADMIN"}))
    for event in (reply(bot_id="B1"), reply(subtype="bot_message", bot_id="B1"), reply(user="U_ADMIN")):
        bot, admin = bot_seeing(topic(closed=True)), MagicMock()
        handle_message(event, config, bot, admin, DraftStore(), TopicCache())
        admin.chat_delete.assert_not_called()
        bot.conversations_history.assert_not_called()


def test_topic_status_is_cached_between_replies():
    bot, admin = bot_seeing(topic(closed=True)), MagicMock()
    topics = TopicCache()

    handle_message(reply(ts="60.000"), CONFIG, bot, admin, DraftStore(), topics)
    handle_message(reply(ts="61.000"), CONFIG, bot, admin, DraftStore(), topics)

    bot.conversations_history.assert_called_once()
    assert admin.chat_delete.call_count == 2


def test_lookup_error_leaves_reply_alone_and_is_not_cached():
    bot, admin = MagicMock(), MagicMock()
    bot.conversations_history.side_effect = [
        SlackApiError("hist", {"ok": False, "error": "ratelimited"}),
        {"messages": [topic(closed=True)]},
    ]
    topics = TopicCache()

    handle_message(reply(ts="60.000"), CONFIG, bot, admin, DraftStore(), topics)
    admin.chat_delete.assert_not_called()

    handle_message(reply(ts="61.000"), CONFIG, bot, admin, DraftStore(), topics)
    admin.chat_delete.assert_called_once_with(channel="C_WATCHED", ts="61.000")
