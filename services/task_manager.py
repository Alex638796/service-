import asyncio
import logging
from typing import Optional

from aiogram import Bot

from models.config_model import BotConfig, BotSettings
from utils.helpers import save_state, load_state, load_config, load_settings
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
    The forwarding loop checks this flag before each message and exits cleanly,
    at which point forward_range()._finalize_cancelled() persists
    status="cancelled" / cancelled=True so this job can never be auto-resumed.
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
    Called at bot startup (Phase 1: Auto Resume).

    Finds jobs left active=True by an unexpected interruption (Render
    sleep/restart, process crash) and relaunches them from
    last_processed_message_id + 1 via the normal start_forwarding_task()
    lifecycle — so Keep Alive restarts automatically too.

    Jobs with status in ("cancelled", "completed") are intentionally
    skipped even if active happens to still be True, so an intentional
    Stop can never be auto-resumed.
    """
    from database import col_state

    cursor = col_state().find({"active": True})
    candidates = await cursor.to_list(length=100)

    if not candidates:
        return

    to_resume = []
    for doc in candidates:
        status = doc.get("status", "idle")
        if status in ("cancelled", "completed"):
            # Stale/inconsistent active=True from an older document shape or
            # a race — never auto-resume these.
            continue
        to_resume.append(doc)

    if not to_resume:
        return

    logger.info(f"Found {len(to_resume)} interrupted forwarding task(s) to auto-resume on startup.")

    resumed, failed_to_resume = [], []

    for doc in to_resume:
        user_id = doc.get("user_id")
        if user_id is None:
            continue

        # Guard against duplicate tasks (e.g. re-entrant startup call).
        existing = _active_tasks.get(user_id)
        if existing and not existing.done():
            logger.info(f"[user={user_id}] Task already running, skipping duplicate resume.")
            continue

        try:
            state = ForwardingState.from_dict(doc)
            start_id = state.start_message_id
            end_id = state.end_message_id
            if start_id is None or end_id is None:
                logger.warning(f"[user={user_id}] Incomplete state, cannot resume. Marking failed.")
                state.active = False
                state.status = "failed"
                await save_state(state)
                failed_to_resume.append(user_id)
                continue

            # Mark recovering before relaunch (backend-only in Phase 1).
            state.status = "recovering"
            await save_state(state)

            cfg = await load_config(user_id)
            settings = await load_settings(user_id)

            if not cfg.is_fully_configured():
                logger.warning(f"[user={user_id}] Config missing, cannot resume. Marking failed.")
                state.active = False
                state.status = "failed"
                await save_state(state)
                failed_to_resume.append(user_id)
                continue

            await start_forwarding_task(
                bot=bot,
                chat_id=user_id,   # owner_chat_id for a private-chat job == user's own chat
                user_id=user_id,
                cfg=cfg,
                settings=settings,
                start_id=start_id,
                end_id=end_id,
            )
            resumed.append((user_id, state.last_processed_message_id, end_id))

        except Exception as e:
            logger.error(f"[user={user_id}] Failed to auto-resume job: {e}", exc_info=True)
            failed_to_resume.append(user_id)

    if not resumed and not failed_to_resume:
        return

    lines = [f"🔁 <b>Bot restarted — auto-resuming {len(resumed)} job(s)</b>\n"]
    for uid, last, end in resumed:
        next_id = (last + 1) if last is not None else "?"
        lines.append(f"👤 User <code>{uid}</code>: continuing from <code>{next_id}</code> → <code>{end}</code>")
    if failed_to_resume:
        lines.append(f"\n⚠️ Could not resume {len(failed_to_resume)} job(s), marked as failed.")

    try:
        await bot.send_message(owner_chat_id, "\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Could not send startup auto-resume notification: {e}")
