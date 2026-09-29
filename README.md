# Threads Only — Slack bot

Keeps selected channels threads-only:

- people reply **only in threads**;
- a new topic is created with **`/new-topic`**: a form opens, and the bot posts the topic to the channel marked "Topic by @author". Discussion happens in its thread;
- the form has an optional **Participants** field (up to 50 people). They are mentioned in the topic ("Participants: @a, @b") and get notified;
- every topic has a **Close topic** button. It opens a form with an optional **Conclusion**. Once closed, the topic shows "🔒 Closed by @who, when" plus the conclusion, the button turns into **Reopen**, and a note is posted to the thread. Closing is a status label only: people can still reply in the thread;
- a topic can be closed and reopened by its author, its participants and anyone in `ALLOWED_USER_IDS`. Everyone else gets an ephemeral "no permission" message;
- a message posted directly to the channel is removed. In its place the author sees an ephemeral notice (*Only visible to you*) with a **Create topic** button. The button opens the same form, prefilled with that text. Once the topic is published, the notice disappears;
- a thread reply sent with "Also send to channel" (`thread_broadcast`) is removed by default too, and the ephemeral notice appears in the same thread;
- messages from bots and integrations (including Workflow Builder) are left alone.

Works on any Slack plan via Socket Mode, so no public URL is needed.

## Why an admin token is required

Bots can't delete other people's messages. `chat.delete` with a user token can only delete what
that user could delete in Slack themselves. So the app must be installed by a **workspace owner
or admin**, and the bot deletes messages on their behalf.

## Slack app setup

1. <https://api.slack.com/apps> → **Create New App** → **From a manifest** → pick the workspace and paste [`manifest.yml`](manifest.yml).
2. **Basic Information → App-Level Tokens** → create a token with the `connections:write` scope. That's `SLACK_APP_TOKEN` (`xapp-…`).
3. **Install App** → install it **while signed in as a workspace owner or admin**. Then copy:
   - *Bot User OAuth Token* → `SLACK_BOT_TOKEN` (`xoxb-…`);
   - *User OAuth Token* → `SLACK_ADMIN_USER_TOKEN` (`xoxp-…`).
4. Invite the bot to every channel it should manage: `/invite @threads-only`. Without this Slack doesn't send the bot any messages from the channel. In private channels, the admin who installed the app must be a member too.
5. Channel ID: *Channel details* → bottom of the dialog (`C…`). List them in `CHANNEL_IDS`.

**If the app was created from an older manifest:** **App Manifest** → paste the new [`manifest.yml`](manifest.yml) → **Save Changes** → **Install App → Reinstall to Workspace** as the same admin. Tokens usually stay the same after reinstalling; if they change, update `.env`.

## Running

```bash
cp .env.example .env   # fill in the tokens and CHANNEL_IDS
```

Locally:

```bash
uv sync
set -a && source .env && set +a && uv run python -m threads_only_bot
```

With Docker:

```bash
docker build -t threads-only-bot .
docker run -d --restart unless-stopped --env-file .env --name threads-only-bot threads-only-bot
```

On a Debian/Ubuntu server with systemd (no Docker), as root:

```bash
apt-get install -y python3-venv git
useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin threadsbot
git clone https://github.com/olegbal/slack-threads-only-bot.git /opt/threads-only-bot
python3 -m venv /opt/threads-only-bot/.venv
/opt/threads-only-bot/.venv/bin/pip install "slack-bolt>=1.21,<2"
install -d -m 700 /etc/threads-only-bot
install -m 600 /path/to/.env /etc/threads-only-bot/env
cp /opt/threads-only-bot/deploy/threads-only-bot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now threads-only-bot
journalctl -u threads-only-bot -f
```

To update: `cd /opt/threads-only-bot && git pull && systemctl restart threads-only-bot`.

Run only one instance per Slack app: Socket Mode splits events between all connected instances.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | yes | `xoxb-…`, receives events, publishes topics and ephemeral notices |
| `SLACK_APP_TOKEN` | yes | `xapp-…`, for Socket Mode |
| `SLACK_ADMIN_USER_TOKEN` | yes | `xoxp-…` of a workspace owner or admin, deletes messages |
| `CHANNEL_IDS` | yes | Comma-separated channel IDs |
| `ALLOWED_USER_IDS` | no | People who may post directly to the channel and close or reopen any topic (comma-separated IDs) |
| `ALLOW_THREAD_BROADCAST` | no | `true` keeps replies sent with "Also send to channel" |
| `NOTICE_TEXT` | no | First line of the ephemeral notice shown when a message is removed. The `/new-topic` hint is always appended |
| `LOG_LEVEL` | no | Defaults to `INFO` |

## Limitations

- Removal happens after the message is posted, so it may flash in the channel for a split second, and a notification about it may already have gone out.
- Files attached to a removed message stay with the author and are not deleted. The `/new-topic` form accepts text only, no attachments.
- A topic is posted by the bot and marked "Topic by @author"; it doesn't appear as the person's own message.
- A topic's status, and who may close it, are stored in the topic message itself, so no database is needed. Topics created before this feature have no **Close topic** button.
- The ephemeral notice disappears after Slack is reloaded and doesn't sync across devices. This is a Slack limitation.
- The removed message's text used by the **Create topic** button is kept in the bot's memory for up to 24 hours. After a bot restart the form opens empty, and the original text can't be recovered.
- If the admin whose token is used loses their permissions, removal stops working. The logs will show `chat.delete failed ... error=cant_delete_message`.

## Tests

```bash
uv run pytest
```

## License

[MIT](LICENSE)
