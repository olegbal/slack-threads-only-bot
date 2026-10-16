"""Images attached to a topic in the /new-topic form.

Files uploaded through a modal's file_input stay private to the person who uploaded them, so channel
members couldn't see them. The bot downloads each image, uploads its own private copy (no channel,
so nothing is posted on its own), and shows that copy inside the topic message via image blocks.
"""

import logging
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

log = logging.getLogger(__name__)

IMAGE_TYPES = frozenset({"png", "jpg", "jpeg", "gif"})  # what Slack image blocks can display
MAX_IMAGES = 10  # file_input limit
IMAGE_BLOCK_PREFIX = "topic_image:"


@dataclass(frozen=True)
class TopicImage:
    file_id: str
    name: str


def is_supported_image(file: Mapping[str, Any]) -> bool:
    filetype = str(file.get("filetype") or "").lower()
    mimetype = str(file.get("mimetype") or "").lower()
    return filetype in IMAGE_TYPES or mimetype in {f"image/{t}" for t in IMAGE_TYPES}


def image_block(image: TopicImage) -> dict[str, Any]:
    # The file id lives in block_id so the block can be rebuilt exactly when the topic is updated.
    return {
        "type": "image",
        "block_id": f"{IMAGE_BLOCK_PREFIX}{image.file_id}",
        "slack_file": {"id": image.file_id},
        "alt_text": image.name or "image",
    }


def normalize_image_blocks(blocks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild our image blocks from their block_id.

    Messages read back from Slack carry extra resolved fields on image blocks (urls, sizes), which
    chat.update rejects; the file id in block_id is all that's needed.
    """
    result = []
    for block in blocks:
        block_id = str(block.get("block_id") or "")
        if block.get("type") == "image" and block_id.startswith(IMAGE_BLOCK_PREFIX):
            name = str(block.get("alt_text") or "image")
            result.append(image_block(TopicImage(block_id.removeprefix(IMAGE_BLOCK_PREFIX), name)))
        else:
            result.append(block)
    return result


def download(url: str, token: str, timeout: float = 30) -> bytes:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        # Without files:read Slack answers with its HTML sign-in page instead of the file.
        if response.headers.get_content_type() == "text/html":
            raise OSError("got an HTML page instead of the file (is the files:read scope granted?)")
        return response.read()


def copy_images(client: WebClient, files: Sequence[Mapping[str, Any]]) -> list[TopicImage]:
    """Upload private bot-owned copies of the person's uploads; returns them in the original order."""
    uploads = []
    for file in files:
        name = str(file.get("name") or f"image.{file.get('filetype') or 'png'}")
        url = file.get("url_private_download") or file.get("url_private")
        if not url or not client.token:
            raise OSError(f"no download URL for file {file.get('id')}")
        uploads.append({"file": download(str(url), client.token), "filename": name, "alt_txt": name})

    # No channel: the copies stay private to the bot and are shared only through the topic's blocks.
    response = client.files_upload_v2(file_uploads=uploads)
    uploaded = response.get("files") or []
    return [TopicImage(str(f["id"]), str(f.get("name") or u["filename"])) for f, u in zip(uploaded, uploads)]


def post_with_retry(
    client: WebClient,
    attempts: int = 5,
    delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    **message: Any,
) -> None:
    # Right after an upload completes, Slack can briefly reject slack_file references with invalid_blocks.
    for attempt in range(1, attempts + 1):
        try:
            client.chat_postMessage(**message)
            return
        except SlackApiError as e:
            if e.response.get("error") != "invalid_blocks" or attempt == attempts:
                raise
            log.info("image not ready yet, retrying post (attempt %d/%d)", attempt, attempts)
            sleep(delay)
