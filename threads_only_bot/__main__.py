import logging
import os
import sys
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from .config import ConfigError, load_config
from .handler import handle_message
from .lifecycle import (
    CLOSE_MODAL_CALLBACK,
    TopicCache,
    handle_close_click,
    handle_close_submission,
    handle_reopen_click,
)
from .topics import (
    CLOSE_TOPIC_ACTION,
    CREATE_TOPIC_ACTION,
    REOPEN_TOPIC_ACTION,
    TOPIC_COMMAND,
    TOPIC_MODAL_CALLBACK,
    Ack,
    DraftStore,
    handle_create_topic_action,
    handle_topic_command,
    handle_topic_submission,
)


def warn_about_unreachable_channels(client: WebClient, channel_ids: frozenset[str], log: logging.Logger) -> None:
    # Slack only delivers message events from channels the bot user has joined, so a missing
    # /invite looks exactly like "nothing happens".
    for channel in sorted(channel_ids):
        try:
            client.conversations_history(channel=channel, limit=1)
        except SlackApiError as e:
            error = e.response.get("error")
            hint = " (run /invite @threads-only in that channel)" if error in {"not_in_channel", "channel_not_found"} else ""
            log.warning("channel %s is not reachable by the bot: %s%s", channel, error, hint)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config()
    except ConfigError as e:
        sys.exit(f"Config error: {e}")

    app = App(token=config.bot_token)
    admin_client = WebClient(token=config.user_token)
    drafts = DraftStore()
    topics = TopicCache()
    try:
        admin = admin_client.auth_test()
    except SlackApiError as e:
        sys.exit(f"SLACK_ADMIN_USER_TOKEN is not usable: {e.response.get('error')}")
    # A burst of posts can hit Tier 3 limits on chat.delete; wait and retry instead of dropping them.
    for client in (app.client, admin_client):
        client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=3))

    @app.event("message")
    def on_message(event: dict[str, Any]) -> None:
        handle_message(event, config, app.client, admin_client, drafts, topics)

    @app.command(TOPIC_COMMAND)
    def on_topic_command(ack: Ack, command: dict[str, Any], client: WebClient) -> None:
        handle_topic_command(ack, command, config, client)

    @app.action(CREATE_TOPIC_ACTION)
    def on_create_topic(ack: Ack, body: dict[str, Any], client: WebClient) -> None:
        handle_create_topic_action(ack, body, config, client, drafts)

    @app.view(TOPIC_MODAL_CALLBACK)
    def on_topic_submit(ack: Ack, body: dict[str, Any], client: WebClient) -> None:
        handle_topic_submission(ack, body, config, client, drafts)

    @app.action(CLOSE_TOPIC_ACTION)
    def on_close_click(ack: Ack, body: dict[str, Any], client: WebClient) -> None:
        handle_close_click(ack, body, config, client)

    @app.action(REOPEN_TOPIC_ACTION)
    def on_reopen_click(ack: Ack, body: dict[str, Any], client: WebClient) -> None:
        handle_reopen_click(ack, body, config, client, topics)

    @app.view(CLOSE_MODAL_CALLBACK)
    def on_close_submit(ack: Ack, body: dict[str, Any], client: WebClient) -> None:
        handle_close_submission(ack, body, config, client, topics)

    log = logging.getLogger(__name__)
    log.info("deleting as user %s, watching channels: %s", admin["user_id"], ", ".join(sorted(config.channel_ids)))
    warn_about_unreachable_channels(app.client, config.channel_ids, log)
    SocketModeHandler(app, config.app_token).start()


if __name__ == "__main__":
    main()
