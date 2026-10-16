from dataclasses import replace

import pytest

from threads_only_bot.config import Config
from threads_only_bot.policy import is_thread_reply, is_violation

CONFIG = Config(
    bot_token="xoxb-test",
    app_token="xapp-test",
    user_token="xoxp-test",
)


def msg(**overrides):
    event = {"type": "message", "channel": "C_WATCHED", "user": "U_PERSON", "ts": "100.000", "text": "hi"}
    event.update(overrides)
    return event


def test_top_level_human_message_is_violation():
    assert is_violation(msg(), CONFIG)


def test_top_level_file_share_is_violation():
    assert is_violation(msg(subtype="file_share"), CONFIG)


def test_thread_reply_is_allowed():
    assert not is_violation(msg(thread_ts="50.000"), CONFIG)


def test_file_share_in_thread_is_allowed():
    assert not is_violation(msg(subtype="file_share", thread_ts="50.000"), CONFIG)


def test_any_channel_the_bot_is_in_is_threads_only():
    assert is_violation(msg(channel="C_ANY", channel_type="channel"), CONFIG)
    assert is_violation(msg(channel="G_PRIVATE", channel_type="group"), CONFIG)


def test_direct_messages_are_ignored():
    assert not is_violation(msg(channel="D1", channel_type="im"), CONFIG)
    assert not is_violation(msg(channel="G1", channel_type="mpim"), CONFIG)


@pytest.mark.parametrize(
    "extra",
    [
        {"bot_id": "B1"},
        {"bot_profile": {"id": "B1"}},
        {"subtype": "bot_message", "bot_id": "B1", "user": None},
    ],
)
def test_bots_may_post_top_level(extra):
    assert not is_violation(msg(**extra), CONFIG)


@pytest.mark.parametrize("subtype", ["message_changed", "message_deleted", "channel_join", "channel_topic"])
def test_non_post_subtypes_are_ignored(subtype):
    assert not is_violation(msg(subtype=subtype), CONFIG)


def test_allowed_user_may_post_top_level():
    config = replace(CONFIG, allowed_user_ids=frozenset({"U_PERSON"}))
    assert not is_violation(msg(), config)


def test_thread_broadcast_is_violation_by_default():
    assert is_violation(msg(subtype="thread_broadcast", thread_ts="50.000"), CONFIG)


def test_thread_broadcast_can_be_allowed():
    config = replace(CONFIG, allow_thread_broadcast=True)
    assert not is_violation(msg(subtype="thread_broadcast", thread_ts="50.000"), config)


def test_thread_reply_detection():
    assert is_thread_reply(msg(thread_ts="50.000"), CONFIG)
    assert is_thread_reply(msg(subtype="file_share", thread_ts="50.000"), CONFIG)
    assert not is_thread_reply(msg(), CONFIG)
    assert not is_thread_reply(msg(thread_ts="100.000"), CONFIG)  # the parent itself
    assert not is_thread_reply(msg(thread_ts="50.000", bot_id="B1"), CONFIG)
    assert not is_thread_reply(msg(thread_ts="50.000", channel="D1", channel_type="im"), CONFIG)
    assert not is_thread_reply(msg(thread_ts="50.000", subtype="message_changed"), CONFIG)


def test_allowed_user_reply_is_not_restricted():
    config = replace(CONFIG, allowed_user_ids=frozenset({"U_PERSON"}))
    assert not is_thread_reply(msg(thread_ts="50.000"), config)
