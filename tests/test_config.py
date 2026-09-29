import pytest

from threads_only_bot.config import DEFAULT_NOTICE, ConfigError, load_config

BASE_ENV = {
    "SLACK_BOT_TOKEN": "xoxb-1",
    "SLACK_APP_TOKEN": "xapp-1",
    "SLACK_ADMIN_USER_TOKEN": "xoxp-1",
    "CHANNEL_IDS": " C1, C2 ,,",
}


def test_loads_minimal_env():
    config = load_config(BASE_ENV)

    assert config.channel_ids == {"C1", "C2"}
    assert config.allowed_user_ids == frozenset()
    assert config.allow_thread_broadcast is False
    assert config.notice_text == DEFAULT_NOTICE


def test_optional_settings():
    config = load_config(
        {**BASE_ENV, "ALLOWED_USER_IDS": "U1,U2", "ALLOW_THREAD_BROADCAST": "true", "NOTICE_TEXT": "Custom"}
    )

    assert config.allowed_user_ids == {"U1", "U2"}
    assert config.allow_thread_broadcast is True
    assert config.notice_text == "Custom"


def test_requires_channels():
    with pytest.raises(ConfigError, match="CHANNEL_IDS"):
        load_config({**BASE_ENV, "CHANNEL_IDS": ""})


def test_rejects_bot_token_in_admin_slot():
    with pytest.raises(ConfigError, match="SLACK_ADMIN_USER_TOKEN"):
        load_config({**BASE_ENV, "SLACK_ADMIN_USER_TOKEN": "xoxb-oops"})
