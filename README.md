# Threads Only — Slack bot

Keeps channels threads-only. The bot manages **every channel it's a member of**: invite it with `/invite @threads-only` to turn a channel threads-only, remove it from the channel (*Integrations → Remove app*) to turn that off. There's no channel list to configure.

In a managed channel:

- people reply **only in threads**;
- a new topic is created with **`/new-topic`**: a form opens, and the bot posts the topic to the channel marked "Topic by @author". Discussion happens in its thread;
- the form has an optional **Images** field (PNG, JPG or GIF, up to 10). Images are shown inside the topic, under its text;
- the form has an optional **Participants** field (up to 50 people). They are mentioned in the topic ("Participants: @a, @b") and get notified;
- every topic has a **Close topic** button. It opens a form with an optional **Conclusion**. Once closed, the topic shows "🔒 Closed by @who, when" plus the conclusion, the button turns into **Reopen**, and a note is posted to the thread;
- replies in a closed topic's thread are removed, and the author sees an ephemeral notice in the thread (pointing to **Reopen** if they're allowed to use it). Bots and people in `ALLOWED_USER_IDS` can still reply;
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
4. Invite the bot to every channel it should manage: `/invite @threads-only`. In private channels, the admin who installed the app must be a member too, otherwise the bot can't delete messages there.

**If the app was created from an older manifest:** **App Manifest** → paste the new [`manifest.yml`](manifest.yml) → **Save Changes** → **Install App → Reinstall to Workspace** as the same admin. Tokens usually stay the same after reinstalling; if they change, update `.env`.

## Running

```bash
cp .env.example .env   # fill in the tokens
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
| `ALLOWED_USER_IDS` | no | People who may post directly to the channel, reply in closed topics, and close or reopen any topic (comma-separated IDs) |
| `ALLOW_THREAD_BROADCAST` | no | `true` keeps replies sent with "Also send to channel" |
| `NOTICE_TEXT` | no | First line of the ephemeral notice shown when a message is removed. The `/new-topic` hint is always appended |
| `LOG_LEVEL` | no | Defaults to `INFO` |

## Limitations

- Anyone who can invite the bot to a channel can make that channel threads-only. Keep that in mind before inviting it to busy shared channels.
- Removal happens after the message is posted, so it may flash in the channel for a split second, and a notification about it may already have gone out.
- Files attached to a removed message stay with the author and are not deleted, and the **Create topic** button can't carry them over: add them again in the form's **Images** field.
- Images from the form are copied: the bot downloads each one and uploads its own private copy, which is shown only inside the topic. The person's original upload stays in their files. Other file types (PDF, WebP, HEIC, …) aren't accepted; attach them in the thread instead.
- A topic is posted by the bot and marked "Topic by @author"; it doesn't appear as the person's own message.
- A topic's status, and who may close it, are stored in the topic message itself, so no database is needed. The bot caches statuses in memory so it doesn't fetch the topic on every reply; after a restart it reads them from the messages again. Topics created before this feature have no **Close topic** button.
- The ephemeral notice disappears after Slack is reloaded and doesn't sync across devices. This is a Slack limitation.
- The removed message's text used by the **Create topic** button is kept in the bot's memory for up to 24 hours. After a bot restart the form opens empty, and the original text can't be recovered.
- If the admin whose token is used loses their permissions, removal stops working. The logs will show `chat.delete failed ... error=cant_delete_message`.

## Tests

```bash
uv run pytest
```

## License

[MIT](LICENSE)
