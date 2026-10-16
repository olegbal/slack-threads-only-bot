import json
from email.message import Message
from unittest.mock import MagicMock

import pytest
from slack_sdk.errors import SlackApiError

from threads_only_bot import images
from threads_only_bot.config import Config
from threads_only_bot.images import (
    IMAGE_BLOCK_PREFIX,
    TopicImage,
    copy_images,
    image_block,
    is_supported_image,
    normalize_image_blocks,
    post_with_retry,
)
from threads_only_bot.lifecycle import status_block, topic_state, with_state
from threads_only_bot.topics import (
    IMAGES_BLOCK,
    IMAGES_INPUT,
    TOPIC_BLOCK,
    TOPIC_INPUT,
    DraftStore,
    handle_topic_command,
    handle_topic_submission,
    plain_rich_text,
    topic_message,
)

CONFIG = Config(
    bot_token="xoxb-test",
    app_token="xapp-test",
    user_token="xoxp-test",
)


def uploaded(file_id="F_USER1", name="screen.png", filetype="png"):
    return {
        "id": file_id,
        "name": name,
        "filetype": filetype,
        "mimetype": f"image/{filetype}",
        "url_private_download": f"https://files.slack.com/{file_id}/download/{name}",
    }


def submission(files):
    values = {TOPIC_BLOCK: {TOPIC_INPUT: {"type": "rich_text_input", "rich_text_value": plain_rich_text("Look")}}}
    if files is not None:
        values[IMAGES_BLOCK] = {IMAGES_INPUT: {"type": "file_input", "files": files}}
    return {
        "user": {"id": "U_AUTHOR"},
        "view": {"private_metadata": json.dumps({"channel": "C_WATCHED"}), "state": {"values": values}},
    }


def bot_client():
    client = MagicMock()
    client.token = "xoxb-test"
    client.files_upload_v2.side_effect = lambda file_uploads: {
        "files": [{"id": f"F_BOT{i}", "name": u["filename"]} for i, u in enumerate(file_uploads, 1)]
    }
    return client


@pytest.fixture
def downloads(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_download(url, token):
        calls.append((url, token))
        return b"\x89PNG..."

    monkeypatch.setattr(images, "download", fake_download)
    return calls


def test_supported_image_types():
    assert is_supported_image(uploaded(filetype="png"))
    assert is_supported_image(uploaded(filetype="jpg"))
    assert is_supported_image({"filetype": "", "mimetype": "image/gif"})
    assert not is_supported_image(uploaded(name="x.webp", filetype="webp"))
    assert not is_supported_image(uploaded(name="x.pdf", filetype="pdf"))


def test_modal_has_optional_image_upload():
    client = MagicMock()

    handle_topic_command(MagicMock(), {"channel_id": "C_WATCHED", "trigger_id": "T1", "text": ""}, client)

    view = client.views_open.call_args.kwargs["view"]
    block = next(b for b in view["blocks"] if b.get("block_id") == IMAGES_BLOCK)
    assert block["optional"] is True
    assert block["element"]["type"] == "file_input"
    assert block["element"]["max_files"] == 10
    assert set(block["element"]["filetypes"]) == {"png", "jpg", "jpeg", "gif"}


def test_copy_images_downloads_with_bot_token_and_uploads_privately(downloads):
    client = bot_client()

    result = copy_images(client, [uploaded("F_USER1", "a.png"), uploaded("F_USER2", "b.jpg", "jpg")])

    assert downloads == [
        ("https://files.slack.com/F_USER1/download/a.png", "xoxb-test"),
        ("https://files.slack.com/F_USER2/download/b.jpg", "xoxb-test"),
    ]
    kwargs = client.files_upload_v2.call_args.kwargs
    assert "channel" not in kwargs and "channels" not in kwargs  # nothing posted on its own
    assert [u["filename"] for u in kwargs["file_uploads"]] == ["a.png", "b.jpg"]
    assert result == [TopicImage("F_BOT1", "a.png"), TopicImage("F_BOT2", "b.jpg")]


def test_submission_with_images_closes_form_then_posts_topic_with_image_blocks(downloads):
    ack, client = MagicMock(), bot_client()

    handle_topic_submission(ack, submission([uploaded("F_USER1", "a.png")]), client, DraftStore())

    ack.assert_called_once_with()
    blocks = client.chat_postMessage.call_args.kwargs["blocks"]
    assert [b["type"] for b in blocks] == ["context", "rich_text", "image", "actions"]
    assert blocks[2] == {
        "type": "image",
        "block_id": f"{IMAGE_BLOCK_PREFIX}F_BOT1",
        "slack_file": {"id": "F_BOT1"},
        "alt_text": "a.png",
    }


def test_submission_without_images_has_no_image_blocks():
    client = MagicMock()

    handle_topic_submission(MagicMock(), submission([]), client, DraftStore())

    assert "image" not in [b["type"] for b in client.chat_postMessage.call_args.kwargs["blocks"]]
    client.files_upload_v2.assert_not_called()


def test_unsupported_file_is_rejected_in_the_form():
    ack, client = MagicMock(), bot_client()

    handle_topic_submission(ack, submission([uploaded(name="doc.pdf", filetype="pdf")]), client, DraftStore())

    assert IMAGES_BLOCK in ack.call_args.kwargs["errors"]
    client.files_upload_v2.assert_not_called()
    client.chat_postMessage.assert_not_called()


def test_image_failure_is_reported_to_author(downloads):
    ack, client = MagicMock(), bot_client()
    client.files_upload_v2.side_effect = SlackApiError("up", {"ok": False, "error": "missing_scope"})

    handle_topic_submission(ack, submission([uploaded()]), client, DraftStore())

    ack.assert_called_once_with()
    client.chat_postMessage.assert_not_called()
    kwargs = client.chat_postEphemeral.call_args.kwargs
    assert kwargs["user"] == "U_AUTHOR"
    assert "missing_scope" in kwargs["text"]


def test_download_failure_is_reported_to_author(monkeypatch):
    def broken(url, token):
        raise OSError("got an HTML page instead of the file")

    monkeypatch.setattr(images, "download", broken)
    client = bot_client()

    handle_topic_submission(MagicMock(), submission([uploaded()]), client, DraftStore())

    assert "HTML page" in client.chat_postEphemeral.call_args.kwargs["text"]


def test_post_retries_while_image_is_not_ready():
    client, sleeps = MagicMock(), []
    not_ready = SlackApiError("blocks", {"ok": False, "error": "invalid_blocks"})
    client.chat_postMessage.side_effect = [not_ready, not_ready, {"ok": True}]

    post_with_retry(client, sleep=sleeps.append, channel="C", text="t")

    assert client.chat_postMessage.call_count == 3
    assert sleeps == [1.0, 1.0]


def test_post_does_not_retry_other_errors():
    client = MagicMock()
    client.chat_postMessage.side_effect = SlackApiError("x", {"ok": False, "error": "not_in_channel"})

    with pytest.raises(SlackApiError):
        post_with_retry(client, sleep=lambda _: None, channel="C", text="t")
    assert client.chat_postMessage.call_count == 1


def test_post_gives_up_after_attempts():
    client = MagicMock()
    client.chat_postMessage.side_effect = SlackApiError("blocks", {"ok": False, "error": "invalid_blocks"})

    with pytest.raises(SlackApiError):
        post_with_retry(client, attempts=3, sleep=lambda _: None, channel="C", text="t")
    assert client.chat_postMessage.call_count == 3


def test_image_blocks_read_back_from_slack_are_rebuilt():
    from_history = {
        "type": "image",
        "block_id": f"{IMAGE_BLOCK_PREFIX}F_BOT1",
        "slack_file": {"id": "F_BOT1", "url": "https://files.slack.com/..."},
        "image_url": "https://files.slack.com/...",
        "image_width": 800,
        "image_height": 600,
        "alt_text": "a.png",
    }

    assert normalize_image_blocks([from_history, {"type": "section"}]) == [
        image_block(TopicImage("F_BOT1", "a.png")),
        {"type": "section"},
    ]


def test_closing_a_topic_keeps_its_images():
    msg = topic_message("U_AUTHOR", plain_rich_text("Look"), images=[TopicImage("F_BOT1", "a.png")])

    blocks = with_state(msg["blocks"], topic_state(msg["blocks"]), True, status_block("U_AUTHOR", "", 1_700_000_000))

    assert image_block(TopicImage("F_BOT1", "a.png")) in blocks


class FakeResponse:
    def __init__(self, content_type, body):
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_sends_bot_token(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        return FakeResponse("image/png", b"PNG")

    monkeypatch.setattr(images.urllib.request, "urlopen", fake_urlopen)

    assert images.download("https://files.slack.com/x", "xoxb-test") == b"PNG"
    assert seen["auth"] == "Bearer xoxb-test"


def test_download_rejects_sign_in_page(monkeypatch):
    monkeypatch.setattr(images.urllib.request, "urlopen", lambda request, timeout: FakeResponse("text/html; charset=utf-8", b"<html>"))

    with pytest.raises(OSError, match="files:read"):
        images.download("https://files.slack.com/x", "xoxb-test")
