import asyncio
import logging
from typing import Optional

from aiogram import Bot

from models.config_model import BotConfig, BotSettings
from utils.helpers import save_state, load_state
from models.config_model import ForwardingState
from services.keepalive import start_keepalive, stop_keepalive

logger = logging.getLogger(__name__)

# Per-user task registry: user_id → asyncio.Task
# Replaces the single global _active_task from the private bot.
_active_tasks: dict[int, asyncio.Task] = {}


async def start_forwarding_task(
    bot: Bot,
    chat_id: int,
    user_id: int,                    # NEW: per-user task key
    cfg: BotConfig,
    settings: BotSettings,
    start_id: int,
    end_id: int,
) -> None:
    """
    Launch the forwarding coroutine as a background asyncio.Task for a specific user.
    Cancels any existing task for that user first (safety guard).
    """
    # Cancel any existing task for this user
    existing = _active_tasks.get(user_id)
    if existing and not existing.done():
        logger.warning(f"[user={user_id}] Cancelling existing forwarding task before starting new one.")
        existing.cancel()
        try:
            await existing
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(
        _run_with_error_handling(
            bot=bot,
            user_chat_id=chat_id,
            user_id=user_id,
            cfg=cfg,
            settings=settings,
            start_id=start_id,
            end_id=end_id,
        ),
        name=f"forward_{user_id}_{start_id}_{end_id}",
    )
    _active_tasks[user_id] = task
    logger.info(f"[user={user_id}] Forwarding task created: {task.get_name()}")

    # Keep Render's free-tier instance awake only while this forwarding job runs.
    start_keepalive(user_id)


async def stop_forwarding_task(user_id: int) -> None:
    """
    Set the stop_flag in MongoDB for a specific user.
    The forwarding loop checks this flag before each message and exits cleanly.
    """
    state = await load_state(user_id)
    state.stop_flag = True
    await save_state(state)
    logger.info(f"[user={user_id}] Stop flag set in MongoDB.")


async def _run_with_error_handling(
    bot: Bot,
    user_chat_id: int,
    user_id: int,
    cfg: BotConfig,
    settings: BotSettings,
    start_id: int,
    end_id: int,
) -> None:
    """
    Wraps forward_range() with top-level error handling.
    Ensures forwarding_state.active is reset even on unexpected errors.
    """
    from services.forwarding import forward_range
    from aiogram.exceptions import TelegramForbiddenError

    try:
        await forward_range(
            bot=bot,
            owner_chat_id=user_chat_id,
            user_id=user_id,
            cfg=cfg,
            settings=settings,
            start_id=start_id,
            end_id=end_id,
        )
    except TelegramForbiddenError as e:
        logger.error(f"[user={user_id}] Forwarding aborted — bot forbidden: {e}")
        await _reset_active_state(user_id)
        try:
            await bot.send_message(
                user_chat_id,
                "🚨 <b>Forwarding aborted!</b>\n\n"
                "The bot was denied access to the destination.\n"
                "Check that the bot is still an admin in the destination group/topic.",
                parse_mode="HTML",
            )
        except Exception:
            pass

    except asyncio.CancelledError:
        logger.info(f"[user={user_id}] Forwarding task was cancelled.")
        await _reset_active_state(user_id)

    except Exception as e:
        logger.error(f"[user={user_id}] Unexpected error in forwarding task: {e}", exc_info=True)
        await _reset_active_state(user_id)
        try:
            await bot.send_message(
                user_chat_id,
                f"🚨 <b>Forwarding task crashed!</b>\n\n"
                f"Error: <code>{str(e)[:200]}</code>\n\n"
                f"Check logs for details. You can restart forwarding from the last checkpoint.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    finally:
        # Clean up task reference
        _active_tasks.pop(user_id, None)
        # Always stop the keep-alive pinger when forwarding ends, regardless of outcome.
        await stop_keepalive(user_id)


async def _reset_active_state(user_id: int) -> None:
    try:
        state = await load_state(user_id)
        state.active = False
        state.stop_flag = False
        await save_state(state)
    except Exception as e:
        logger.error(f"[user={user_id}] Failed to reset active state: {e}")


async def resume_on_startup(bot: Bot, owner_chat_id: int) -> None:
    """
    Called at bot startup. Scans ALL users' forwarding states for interrupted tasks.
    Notifies the owner with a summary of any interrupted tasks found.
    """
    from database import col_state

    # Find all documents with active=True across all users
    cursor = col_state().find({"active": True})
    interrupted = await cursor.to_list(length=100)

    if not interrupted:
        return

    logger.info(f"Found {len(interrupted)} interrupted forwarding task(s) on startup.")

    # Reset all active flags
    for doc in interrupted:
        user_id = doc.get("user_id")
        if user_id is None:
            continue
        try:
            state = ForwardingState.from_dict(doc)
            state.active = False
            state.stop_flag = False
            await save_state(state)
        except Exception as e:
            logger.error(f"Failed to reset state for user {user_id} on startup: {e}")

    # Notify owner
    lines = [f"⚠️ <b>Bot restarted — {len(interrupted)} task(s) interrupted</b>\n"]
    for doc in interrupted:
        uid = doc.get("user_id", "?")
        last = doc.get("last_processed_message_id") or doc.get("start_message_id", "?")
        end = doc.get("end_message_id", "?")
        lines.append(
            f"👤 User <code>{uid}</code>: "
            f"last=<code>{last}</code>, end=<code>{end}</code>"
        )
    lines.append("\nEach affected user must re-run /range to resume.")

    try:
        await bot.send_message(
            owner_chat_id,
            "\n".join(lines),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Could not send startup restart notification: {e}")
