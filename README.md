# YouTube Follow Bot

Private Telegram bot for approved users to subscribe to YouTube channels and receive upload notifications through YouTube WebSub push delivery.

## Stack

- Python 3.12+
- FastAPI
- python-telegram-bot
- SQLite
- SQLAlchemy 2.x
- uvicorn
- httpx
- yt-dlp
- asyncio

## Project layout

- `app/bot/` Telegram UX and handlers
- `app/api/` FastAPI webhook endpoint
- `app/services/` YouTube, WebSub, notification, subscription logic
- `app/db/` SQLAlchemy setup
- `app/models/` database models
- `app/tasks/` background scheduler
- `app/utils/` settings, logging, YouTube helpers

## Install

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip ffmpeg nginx sqlite3
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
python -m yt_dlp --version
```

## Configuration

Copy `.env.example` to `.env` and set:

- `BOT_TOKEN`
- `BASE_URL`
- `DATABASE_PATH`
- `WEBHOOK_SECRET`
- `ALLOWED_USER_IDS`
- `LOG_LEVEL`

Example:

```env
BOT_TOKEN=123456:ABCDEF
BASE_URL=https://example.com
DATABASE_PATH=./data/bot.sqlite3
WEBHOOK_SECRET=change-this-secret
ALLOWED_USER_IDS=12345,67890,11111
LOG_LEVEL=INFO
```

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Telegram UX

- `/start` shows the welcome screen and main menu
- `/help` shows supported link formats
- `/subscriptions` shows paginated subscriptions
- `/unsubscribe` shows the same list with remove buttons
- `/health` returns `OK`

Approved users can also use the menu buttons:

- `➕ Subscribe`
- `📺 My Subscriptions`
- `❌ Unsubscribe`
- `ℹ️ Help`

Unapproved users always receive:

`You are not allowed to use this bot.`

## WebSub flow

1. User submits a YouTube URL.
2. The bot uses `yt-dlp` to extract the canonical `channel_id`.
3. The subscription is stored per user in SQLite.
4. If this is the first global subscription for that channel, the app subscribes to WebSub using:

`https://pubsubhubbub.appspot.com/subscribe`

5. Topic format is exactly:

`https://www.youtube.com/xml/feeds/videos.xml?channel_id=CHANNEL_ID`

6. YouTube verifies ownership through `GET /youtube/webhook`.
7. On `POST /youtube/webhook`, the bot parses Atom XML, deduplicates by `video_id`, and notifies every subscribed user.

## Nginx

Example reverse proxy configuration:

```nginx
server {
    listen 80;
    server_name example.com;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name example.com;

    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 60s;
    }
}
```

## HTTPS with certbot

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d example.com
sudo certbot renew --dry-run
```

## Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable
sudo ufw status
```

## tmux

```bash
tmux new -s youtubebot
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Detach:

```bash
Ctrl-b d
```

Reattach:

```bash
tmux attach -t youtubebot
```

## Webhook verification tests

Example verification request:

```bash
curl -i "https://example.com/youtube/webhook?hub.mode=subscribe&hub.topic=https%3A%2F%2Fwww.youtube.com%2Fxml%2Ffeeds%2Fvideos.xml%3Fchannel_id%3DUC123&hub.challenge=test-challenge&hub.verify_token=change-this-secret"
```

Example POST payload test:

```bash
curl -i -X POST "https://example.com/youtube/webhook" \
  -H "Content-Type: application/atom+xml" \
  -H "X-Hub-Signature-256: sha256=<computed-hmac>" \
  --data-binary @sample-feed.xml
```

## Sample nginx/nginx reload loop

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Maintenance

### Update the app

```bash
cd /path/to/yoututubefollowbot
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart youtubebot
```

### Update yt-dlp safely

```bash
source .venv/bin/activate
pip install -U yt-dlp
python -m yt_dlp --version
```

### Debug failed webhook deliveries

- Check nginx access and error logs.
- Confirm `BASE_URL` matches the public HTTPS domain.
- Verify YouTube can reach `GET /youtube/webhook`.
- Check app logs for malformed XML or signature failures.

### Verify WebSub subscription status

YouTube does not provide a friendly dashboard for this in the app. Practical checks are:

- confirm the channel exists in SQLite
- confirm `last_websub_subscribed_at` is recent
- inspect logs for subscribe or renew failures
- trigger the 12-hour renewal loop manually by restarting the app

### Manually resubscribe channels

Restart the app or call the renewal workflow by re-running the subscription logic for active channels. The scheduler does this every 12 hours.

### Rotate logs

Use `logrotate` if you send logs to a file, or rely on journald if you run under systemd.

### Back up SQLite

Recommended pattern:

```bash
cp ./data/bot.sqlite3 ./backups/bot-$(date +%F).sqlite3
```

For safer backups, stop the app first or use SQLite's `.backup` feature.

## Security considerations

- Keep `BOT_TOKEN` private.
- Keep `WEBHOOK_SECRET` private.
- Restrict `ALLOWED_USER_IDS` to trusted Telegram IDs only.
- The webhook validates the challenge token and HMAC signatures.
- XML parsing is strict and malformed payloads are ignored.
- `video_id` is unique in `sent_videos` to prevent duplicate notifications.
- SQLite is fine for a small VPS, but back it up regularly.
- Add nginx rate limiting if the webhook becomes noisy.

## Notes

- The bot uses WebSub push notifications, not aggressive polling, for YouTube uploads.
- A reconciliation task runs every 6 hours to catch missed uploads.
- WebSub renewal runs every 12 hours.
