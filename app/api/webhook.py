from __future__ import annotations

import hmac
import hashlib
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from app.models.youtube_channel import YouTubeChannel
from app.services.notifications import format_notification, send_with_retry
from app.services.subscriptions import get_subscribed_user_ids, record_sent_video
from app.services.youtube import parse_atom_feed

logger = logging.getLogger(__name__)
router = APIRouter()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    if "=" not in signature_header:
        return False
    algo, received = signature_header.split("=", 1)
    algo = algo.lower().strip()
    digestmod = {"sha1": hashlib.sha1, "sha256": hashlib.sha256}.get(algo)
    if digestmod is None:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, digestmod).hexdigest()
    return hmac.compare_digest(expected, received)


def _has_valid_signature(secret: str, body: bytes, headers: Mapping[str, str]) -> bool:
    signatures = [headers.get("X-Hub-Signature-256"), headers.get("X-Hub-Signature")]
    return any(_verify_signature(secret, body, signature) for signature in signatures if signature)


def _is_valid_topic(topic: str) -> bool:
    parsed = urlparse(topic)
    if parsed.scheme != "https":
        return False
    if parsed.netloc != "www.youtube.com":
        return False
    if parsed.path != "/xml/feeds/videos.xml":
        return False
    params = parse_qs(parsed.query, keep_blank_values=True)
    channel_ids = params.get("channel_id", [])
    return len(channel_ids) == 1 and bool(channel_ids[0].strip()) and len(params) == 1


@router.get("/youtube/webhook")
async def youtube_webhook_verify(request: Request) -> Response:
    settings = request.app.state.settings
    params = request.query_params
    mode = params.get("hub.mode", "")
    topic = params.get("hub.topic", "")
    challenge = params.get("hub.challenge", "")
    verify_token = params.get("hub.verify_token", "")

    if mode not in {"subscribe", "unsubscribe"}:
        raise HTTPException(status_code=400, detail="Invalid hub.mode")
    if not challenge:
        raise HTTPException(status_code=400, detail="Missing hub.challenge")
    if verify_token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid verification token")
    if not _is_valid_topic(topic):
        raise HTTPException(status_code=400, detail="Invalid topic")
    return Response(content=challenge, media_type="text/plain")


@router.post("/youtube/webhook")
async def youtube_webhook_receive(request: Request) -> Response:
    settings = request.app.state.settings
    runtime = request.app.state.runtime
    body = await request.body()
    if not _has_valid_signature(settings.webhook_secret, body, request.headers):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        # WebSub delivers Atom XML payloads. We ignore malformed bodies rather than
        # surfacing them to the hub, because hubs can retry and malformed payloads
        # are not actionable for the user-facing bot.
        text = body.decode("utf-8", errors="replace")
        entries = parse_atom_feed(text)
    except Exception:
        logger.exception("Malformed WebSub payload")
        return Response(status_code=204)

    if not entries:
        return Response(status_code=204)

    async with runtime.session_factory() as session:
        for entry in entries:
            channel = await session.scalar(select(YouTubeChannel).where(YouTubeChannel.channel_id == entry.channel_id))
            if channel is None:
                continue
            created = await record_sent_video(
                session,
                video_id=entry.video_id,
                channel_id=entry.channel_id,
                title=entry.title,
                published_at=entry.published,
                source="websub",
            )
            if not created:
                continue
            await session.commit()
            user_ids = await get_subscribed_user_ids(session, channel_id=entry.channel_id)
            if not user_ids:
                continue
            text = format_notification(channel.channel_name, entry.title, entry.video_id)
            for user_id in user_ids:
                await send_with_retry(runtime.telegram_app, user_id=user_id, text=text)
    return Response(status_code=204)
