import asyncio
import logging
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest, TelegramForbiddenError

from models.config_model import BotConfig, BotSettings
from utils.helpers import (
    checkpoint_message_id,
    is_stop_requested,
    save_state,
    load_state,
)

logger = logging.getLogger(__name__)

PROGRESS_NOTIFY_INTERVAL = 25


async def forward_range(
    bot: Bot,
    owner_chat_id: int,
    user_id: int,                    # NEW: needed for per-user DB operations
    cfg: BotConfig,
    settings: BotSettings,
    start_id: int,
    end_id: int,
) -> None:
    """
    Core forwarding loop. Unchanged from private bot except:
    - Accepts user_id parameter
    - Passes user_id to all DB helper calls (checkpoint, stop check, state save)
    """
    destination_chat_id = cfg.destination_chat_id
    destination_thread_id = cfg.destination_thread_id
    source_chat_id = cfg.source_chat_id
    delay = settings.delay_seconds

    total = end_id - start_id + 1
    forwarded = 0
    skipped = 0

    logger.info(
        f"[user={user_id}] Forwarding task started: source={source_chat_id}, "
        f"dest={destination_chat_id}, thread={destination_thread_id}, "
        f"range={start_id}–{end_id}, total={total}"
    )

    for message_id in range(start_id, end_id + 1):
        # Check stop flag before each message
        if await is_stop_requested(user_id):
            logger.info(f"[user={user_id}] Stop flag detected at message_id={message_id}. Halting.")
            await _finalize_state(user_id, active=False, stop_flag=False)
            await bot.send_message(
                owner_chat_id,
                f"⏹ <b>Forwarding stopped.</b>\n\n"
                f"Forwarded: <b>{forwarded}</b> | Skipped: <b>{skipped}</b>\n"
                f"Last processed: <code>{message_id - 1}</code>",
                parse_mode="HTML",
            )
            return

        success = await _copy_single_message(
            bot=bot,
            source_chat_id=source_chat_id,
            destination_chat_id=destination_chat_id,
            destination_thread_id=destination_thread_id,
            message_id=message_id,
        )

        if success:
            forwarded += 1
        else:
            skipped += 1

        await checkpoint_message_id(user_id, message_id)

        if forwarded > 0 and forwarded % PROGRESS_NOTIFY_INTERVAL == 0:
            percent = int(((message_id - start_id + 1) / total) * 100)
            try:
                await bot.send_message(
                    owner_chat_id,
                    f"📊 <b>Progress:</b> {percent}%\n"
                    f"Forwarded: <b>{forwarded}</b> | Skipped: <b>{skipped}</b>\n"
                    f"Current ID: <code>{message_id}</code> / <code>{end_id}</code>",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"[user={user_id}] Could not send progress update: {e}")

        await asyncio.sleep(delay)

    await _finalize_state(user_id, active=False, stop_flag=False)
    logger.info(f"[user={user_id}] Forwarding completed. Forwarded={forwarded}, Skipped={skipped}")

    try:
        await bot.send_message(
            owner_chat_id,
            f"✅ <b>Forwarding complete!</b>\n\n"
            f"📨 Forwarded: <b>{forwarded}</b>\n"
            f"⏭ Skipped: <b>{skipped}</b>\n"
            f"Range: <code>{start_id}</code> → <code>{end_id}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"[user={user_id}] Could not send completion message: {e}")


async def _copy_single_message(
    bot: Bot,
    source_chat_id: int,
    destination_chat_id: int,
    destination_thread_id: Optional[int],
    message_id: int,
    max_retries: int = 3,
) -> bool:
    """
    Unchanged from private bot. No user_id needed here —
    this function only talks to the Telegram API, not MongoDB.
    """
    for attempt in range(1, max_retries + 1):
        try:
            kwargs = {
                "chat_id": destination_chat_id,
                "from_chat_id": source_chat_id,
                "message_id": message_id,
            }
            if destination_thread_id is not None:
                kwargs["message_thread_id"] = destination_thread_id

            await bot.copy_message(**kwargs)
            return True

        except TelegramRetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(
                f"FloodWait on message_id={message_id}: sleeping {wait}s (attempt {attempt}/{max_retries})"
            )
            await asyncio.sleep(wait)

        except TelegramBadRequest as e:
            error_text = str(e).lower()
            if "message to copy not found" in error_text or "message_id_invalid" in error_text:
                logger.warning(f"Message {message_id} not found. Skipping.")
                return False
            if "replied message not found" in error_text:
                if destination_thread_id is not None:
                    logger.warning(f"Topic thread error for message {message_id}. Retrying without thread_id.")
                    try:
                        await bot.copy_message(
                            chat_id=destination_chat_id,
                            from_chat_id=source_chat_id,
                            message_id=message_id,
                        )
                        return True
                    except Exception as fallback_e:
                        logger.error(f"Fallback also failed for message {message_id}: {fallback_e}")
                        return False
            logger.error(f"TelegramBadRequest for message {message_id}: {e}")
            return False

        except TelegramForbiddenError as e:
            logger.error(f"Bot forbidden (message {message_id}): {e}")
            raise

        except Exception as e:
            logger.error(f"Unexpected error copying message {message_id} (attempt {attempt}): {e}")
            if attempt == max_retries:
                return False
            await asyncio.sleep(2)

    return False


async def _finalize_state(user_id: int, active: bool, stop_flag: bool) -> None:
    try:
        state = await load_state(user_id)
        state.active = active
        state.stop_flag = stop_flag
        await save_state(state)
    except Exception as e:
        logger.error(f"[user={user_id}] Failed to finalize forwarding state: {e}")
