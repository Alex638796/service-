import logging
from datetime import datetime, timezone
from aiogram.types import Message, CallbackQuery

from config import config
from utils.helpers import load_user, save_user, upsert_user_on_activity
from models.config_model import UserProfile

logger = logging.getLogger(__name__)


# ─── Basic role checks ────────────────────────────────────────────────────────

def is_owner(message: Message) -> bool:
    """
    Returns True if the message sender is the configured owner.
    For use in message handlers only.
    """
    if message.from_user is None:
        return False
    return message.from_user.id == config.OWNER_ID


def is_owner_callback(callback: CallbackQuery) -> bool:
    """
    Returns True if the user who clicked the button is the configured owner.
    For use in callback query handlers only.

    IMPORTANT: Do NOT use is_owner(callback.message) in callback handlers.
    callback.message.from_user is the BOT itself (it sent the message),
    not the user who clicked. callback.from_user is the clicking user.
    """
    if callback.from_user is None:
        return False
    return callback.from_user.id == config.OWNER_ID


async def is_allowed(message: Message) -> bool:
    """
    Returns True if sender is an allowed user (not owner).
    Does NOT include owner — use is_allowed_or_owner() for general access checks.
    """
    if message.from_user is None:
        return False
    profile = await load_user(message.from_user.id)
    if profile is None:
        return False
    return profile.status == "allowed"


async def is_allowed_or_owner(message: Message) -> bool:
    """
    Main gate for all regular bot features.
    Returns True if sender is owner OR an allowed user.
    Handles banned and unknown users with appropriate responses.
    Returns False for all unauthorized cases.
    """
    if message.from_user is None:
        logger.debug("Auth check: from_user is None, denying.")
        return False

    user_id = message.from_user.id

    # Owner always passes
    if user_id == config.OWNER_ID:
        return True

    profile = await load_user(user_id)

    # Known allowed user
    if profile is not None and profile.status == "allowed":
        return True

    # Known banned user
    if profile is not None and profile.status == "banned":
        logger.info(f"Banned user {user_id} attempted access.")
        await message.answer(
            "🚫 You have been banned from using this bot."
        )
        return False

    # Unknown user or pending — register and notify owner
    await _handle_unknown_user(message, profile)
    return False


# ─── Decorator-style guard functions ──────────────────────────────────────────

async def owner_required(message: Message) -> bool:
    """
    Gate for owner-only commands (message handlers).
    Silent deny for non-owners.
    """
    if not is_owner(message):
        logger.warning(
            f"Unauthorized owner-only access by user_id="
            f"{getattr(message.from_user, 'id', 'unknown')}"
        )
        return False
    return True


async def owner_required_callback(callback: CallbackQuery) -> bool:
    """
    Gate for owner-only callback handlers.
    Sends a popup alert for non-owners.
    Uses callback.from_user, NOT callback.message.from_user.
    """
    if not is_owner_callback(callback):
        logger.warning(
            f"Unauthorized callback by user_id="
            f"{getattr(callback.from_user, 'id', 'unknown')}"
        )
        await callback.answer("Not authorized.", show_alert=True)
        return False
    return True


async def allowed_or_owner_required(message: Message) -> bool:
    """
    Gate for regular bot features.
    Sends appropriate denial message for banned/unknown users.
    """
    return await is_allowed_or_owner(message)


# ─── Unknown user handling ────────────────────────────────────────────────────

async def _handle_unknown_user(message: Message, existing_profile: UserProfile | None) -> None:
    """
    Called when an unknown or pending user tries to use the bot.
    - Creates or updates their profile with status=pending
    - Sends them a denial message
    - Notifies owner once (on first interaction only)
    """
    from aiogram import Bot
    user = message.from_user
    user_id = user.id
    now = datetime.now(timezone.utc)
    is_new = existing_profile is None

    if is_new:
        profile = UserProfile(
            user_id=user_id,
            username=user.username,
            first_name=user.first_name,
            status="pending",
            first_seen=now,
            last_seen=now,
            usage_count=1,
        )
        await save_user(profile)
        logger.info(f"New unknown user registered as pending: {user_id} (@{user.username})")
    else:
        # Already pending — just update last_seen
        existing_profile.last_seen = now
        existing_profile.username = user.username
        existing_profile.first_name = user.first_name
        existing_profile.usage_count += 1
        await save_user(existing_profile)

    # Denial message to user
    await message.answer(
        "🔒 This bot is private.\n\n"
        "Your request has been logged. "
        "Please wait for the owner to grant you access."
    )

    # Notify owner only on first interaction
    if is_new:
        try:
            bot: Bot = message.bot
            username_str = f"@{user.username}" if user.username else "no username"
            name_str = user.first_name or "Unknown"
            await bot.send_message(
                config.OWNER_ID,
                f"🔔 <b>New access request</b>\n\n"
                f"👤 Name: <b>{name_str}</b>\n"
                f"🆔 User ID: <code>{user_id}</code>\n"
                f"📛 Username: {username_str}\n\n"
                f"To allow: <code>/allow {user_id}</code>\n"
                f"To ban: <code>/ban {user_id}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Could not notify owner of new user {user_id}: {e}")
