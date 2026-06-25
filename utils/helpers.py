import logging
from typing import Optional
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from aiogram.types import Message

from database import col_config, col_state, col_settings, col_users
from models.config_model import BotConfig, ForwardingState, BotSettings, UserProfile
from config import config

logger = logging.getLogger(__name__)


# ─── Key helpers ──────────────────────────────────────────────────────────────
# Private bot used {"_id": "singleton"} for all collections.
# Multi-user bot keys every document by user_id instead.

def _user_key(user_id: int) -> dict:
    return {"_id": user_id}


# ─── BotConfig helpers ────────────────────────────────────────────────────────

async def load_config(user_id: int) -> BotConfig:
    doc = await col_config().find_one(_user_key(user_id))
    if doc is None:
        return BotConfig(user_id=user_id)
    return BotConfig.from_dict(doc)


async def save_config(cfg: BotConfig) -> None:
    await col_config().update_one(
        _user_key(cfg.user_id),
        {"$set": {**_user_key(cfg.user_id), **cfg.to_dict()}},
        upsert=True,
    )


# ─── ForwardingState helpers ──────────────────────────────────────────────────

async def load_state(user_id: int) -> ForwardingState:
    doc = await col_state().find_one(_user_key(user_id))
    if doc is None:
        return ForwardingState(user_id=user_id)
    return ForwardingState.from_dict(doc)


async def save_state(state: ForwardingState) -> None:
    await col_state().update_one(
        _user_key(state.user_id),
        {"$set": {**_user_key(state.user_id), **state.to_dict()}},
        upsert=True,
    )


async def checkpoint_message_id(user_id: int, message_id: int) -> None:
    """Update only last_processed_message_id for frequent checkpointing."""
    await col_state().update_one(
        _user_key(user_id),
        {"$set": {"last_processed_message_id": message_id}},
        upsert=True,
    )


async def is_stop_requested(user_id: int) -> bool:
    doc = await col_state().find_one(_user_key(user_id), {"stop_flag": 1})
    if doc is None:
        return False
    return doc.get("stop_flag", False)


# ─── BotSettings helpers ──────────────────────────────────────────────────────

async def load_settings(user_id: int) -> BotSettings:
    doc = await col_settings().find_one(_user_key(user_id))
    if doc is None:
        return BotSettings(user_id=user_id, delay_seconds=config.DEFAULT_DELAY_SECONDS)
    return BotSettings.from_dict(doc)


async def save_settings(settings: BotSettings) -> None:
    await col_settings().update_one(
        _user_key(settings.user_id),
        {"$set": {**_user_key(settings.user_id), **settings.to_dict()}},
        upsert=True,
    )


async def set_topic_capture_mode(user_id: int, enabled: bool) -> None:
    expiry = None
    if enabled:
        expiry = datetime.now(timezone.utc) + timedelta(seconds=config.TOPIC_CAPTURE_EXPIRY)
    await col_settings().update_one(
        _user_key(user_id),
        {"$set": {
            "topic_capture_mode": enabled,
            "topic_capture_expires": expiry,
        }},
        upsert=True,
    )


async def is_topic_capture_active(user_id: int) -> bool:
    doc = await col_settings().find_one(
        _user_key(user_id),
        {"topic_capture_mode": 1, "topic_capture_expires": 1}
    )
    if doc is None:
        return False
    if not doc.get("topic_capture_mode", False):
        return False
    expiry = doc.get("topic_capture_expires")
    if expiry is None:
        return False
    now = datetime.now(timezone.utc)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return now < expiry


# ─── UserProfile helpers (NEW) ────────────────────────────────────────────────

async def load_user(user_id: int) -> Optional[UserProfile]:
    """Load user profile. Returns None if user does not exist in DB."""
    doc = await col_users().find_one(_user_key(user_id))
    if doc is None:
        return None
    return UserProfile.from_dict(doc)


async def save_user(profile: UserProfile) -> None:
    await col_users().update_one(
        _user_key(profile.user_id),
        {"$set": {**_user_key(profile.user_id), **profile.to_dict()}},
        upsert=True,
    )


async def upsert_user_on_activity(message: Message) -> UserProfile:
    """
    Called whenever an allowed user runs a command.
    Creates profile on first interaction, updates last_seen and usage_count.
    Returns the current profile.
    """
    user = message.from_user
    user_id = user.id
    now = datetime.now(timezone.utc)

    existing = await load_user(user_id)
    if existing is None:
        # First time we see this user — create pending profile
        profile = UserProfile(
            user_id=user_id,
            username=user.username,
            first_name=user.first_name,
            status="pending",
            first_seen=now,
            last_seen=now,
            usage_count=1,
        )
    else:
        # Update existing profile
        profile = existing
        profile.username = user.username
        profile.first_name = user.first_name
        profile.last_seen = now
        profile.usage_count += 1

    await save_user(profile)
    return profile


async def set_user_status(user_id: int, status: str) -> bool:
    """
    Set user status to 'allowed', 'banned', or 'pending'.
    Returns True if user was found and updated, False if user not in DB.
    """
    result = await col_users().update_one(
        _user_key(user_id),
        {"$set": {"status": status}},
    )
    return result.matched_count > 0


async def get_all_users() -> list[UserProfile]:
    """Return all user profiles sorted by first_seen descending."""
    cursor = col_users().find({}).sort("first_seen", -1)
    docs = await cursor.to_list(length=200)
    return [UserProfile.from_dict(d) for d in docs]


async def get_users_by_status(status: str) -> list[UserProfile]:
    cursor = col_users().find({"status": status}).sort("first_seen", -1)
    docs = await cursor.to_list(length=200)
    return [UserProfile.from_dict(d) for d in docs]


async def count_users_by_status() -> dict:
    """Return counts for each status. Used in admin stats."""
    pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    cursor = col_users().aggregate(pipeline)
    results = await cursor.to_list(length=10)
    counts = {"allowed": 0, "banned": 0, "pending": 0}
    for r in results:
        counts[r["_id"]] = r["count"]
    return counts


# ─── Message ID extraction from forwarded messages ───────────────────────────
# Unchanged from private bot.

def extract_forwarded_message_id(message: Message) -> Optional[int]:
    """
    Extract the original message_id from a message forwarded from a channel.
    Handles both Bot API 7.0+ (forward_origin) and legacy (forward_from_message_id).
    """
    if message.forward_origin is not None:
        origin = message.forward_origin
        if hasattr(origin, "message_id") and origin.message_id:
            return origin.message_id
    if message.forward_from_message_id:
        return message.forward_from_message_id
    return None


def extract_forwarded_chat_id(message: Message) -> Optional[int]:
    if message.forward_origin is not None:
        origin = message.forward_origin
        if hasattr(origin, "chat") and origin.chat:
            return origin.chat.id
    if message.forward_from_chat:
        return message.forward_from_chat.id
    return None


def extract_forwarded_chat_title(message: Message) -> Optional[str]:
    if message.forward_origin is not None:
        origin = message.forward_origin
        if hasattr(origin, "chat") and origin.chat:
            return origin.chat.title
    if message.forward_from_chat:
        return message.forward_from_chat.title
    return None


# ─── Status formatting ────────────────────────────────────────────────────────

async def build_status_text(user_id: int) -> str:
    """Build status text for a specific user."""
    cfg = await load_config(user_id)
    state = await load_state(user_id)
    settings = await load_settings(user_id)

    source_line = (
        f"📢 <b>Source:</b> {cfg.source_title} (<code>{cfg.source_chat_id}</code>)"
        if cfg.is_source_configured()
        else "📢 <b>Source:</b> ❌ Not configured"
    )

    if cfg.is_destination_configured():
        if cfg.destination_type == "forum_topic":
            dest_line = (
                f"🗂 <b>Destination:</b> {cfg.destination_title} "
                f"(<code>{cfg.destination_chat_id}</code>)\n"
                f"   📌 <b>Topic thread ID:</b> <code>{cfg.destination_thread_id}</code>"
            )
        else:
            dest_line = (
                f"👥 <b>Destination:</b> {cfg.destination_title} "
                f"(<code>{cfg.destination_chat_id}</code>) [normal group]"
            )
    else:
        dest_line = "🗂 <b>Destination:</b> ❌ Not configured"

    if state.active:
        fwd_status = (
            f"⚙️ <b>Forwarding:</b> 🟢 Active\n"
            f"   Range: <code>{state.start_message_id}</code> → <code>{state.end_message_id}</code>\n"
            f"   Last processed: <code>{state.last_processed_message_id}</code>"
        )
    elif state.last_processed_message_id:
        fwd_status = (
            f"⚙️ <b>Forwarding:</b> ⏹ Stopped\n"
            f"   Last processed: <code>{state.last_processed_message_id}</code>"
        )
    else:
        fwd_status = "⚙️ <b>Forwarding:</b> ⏹ Idle"

    delay_line = f"⏱ <b>Delay:</b> {settings.delay_seconds}s per message"

    return "\n".join([source_line, dest_line, fwd_status, delay_line])
