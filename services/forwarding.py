import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest, TelegramForbiddenError

from models.config_model import BotConfig, BotSettings
from utils.helpers import (
    is_stop_requested,
    save_state,
    load_state,
    checkpoint_progress,
    set_job_status,
    save_progress_message_ref,
    mark_pinned,
    mark_completion_sent,
    set_job_started_at,
)

logger = logging.getLogger(__name__)

PROGRESS_EDIT_INTERVAL = 20  # Phase 3: edit progress msg every ~20 successful forwards
CONSECUTIVE_FAILURE_LIMIT = 5  # safety valve for persistent destination-level failures

STATUS_LABELS = {
    "forwarding": "🔄 Forwarding",
    "sleeping": "😴 Sleeping",
    "recovering": "🔁 Recovering",
    "failed": "❌ Failed",
    "cancelled": "🛑 Cancelled",
    "completed": "✅ Completed",
}


def _dest_label(cfg: BotConfig) -> str:
    """Derive a human destination label from existing config fields only."""
    title = cfg.destination_title or "Unknown"
    if cfg.destination_thread_id is not None:
        return f"Topic: {title}"
    return title


def _progress_bar(percent: int, width: int = 20) -> str:
    percent = max(0, min(100, percent))
    filled = round(width * percent / 100)
    return "█" * filled + "░" * (width - filled)


def _format_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "Calculating..."
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    if m == 0:
        return f"~{s}s"
    return f"~{m}m {s}s"


def _build_progress_text(
    cfg: BotConfig,
    start_id: int,
    end_id: int,
    current_id: int,
    forwarded: int,
    skipped: int,
    failed: int,
    status: str,
    eta_seconds: Optional[float],
    sleeping_seconds: Optional[int] = None,
) -> str:
    total = end_id - start_id + 1
    done = current_id - start_id + 1
    percent = int((done / total) * 100) if total > 0 else 0

    status_label = STATUS_LABELS.get(status, status)
    if status == "sleeping" and sleeping_seconds is not None:
        status_line = f"😴 Sleeping for {sleeping_seconds}s"
    else:
        status_line = status_label

    lines = [
        "╭─────── AESTHETIC ───────╮",
        "",
        "📤 Forwarding Messages",
        "",
        "📍 Source",
        f"└─ {cfg.source_title or 'Unknown'}",
        "",
        "🎯 Destination",
        f"└─ {_dest_label(cfg)}",
        "",
        "📦 Progress",
        f"{_progress_bar(percent)} {percent}%",
        "",
        "📨 Forwarded",
        f"└─ {forwarded} / {total}",
        "",
        "⏭️ Skipped",
        f"└─ {skipped}",
        "",
        "❌ Failed",
        f"└─ {failed}",
        "",
        "🆔 Current",
        f"└─ #{current_id}",
        "",
        "⏳ ETA",
        f"└─ {_format_eta(eta_seconds)}",
        "",
        "🔄 Status" if status not in ("sleeping",) else "😴 Status",
        f"└─ {status_line}" if status != "sleeping" else f"└─ Sleeping for {sleeping_seconds}s",
        "",
        "╰────────────────────────╯",
    ]
    return "\n".join(lines)


def _build_summary_text(
    cfg: BotConfig,
    start_id: int,
    end_id: int,
    forwarded: int,
    skipped: int,
    failed: int,
    status: str,
    pinned_done: bool,
) -> str:
    status_label = STATUS_LABELS.get(status, status)
    header = {
        "completed": "✅ FORWARDING COMPLETED",
        "cancelled": "🛑 FORWARDING CANCELLED",
        "failed": "❌ FORWARDING FAILED",
    }.get(status, status_label)

    lines = [
        "╭─────── AESTHETIC ───────╮",
        "",
        header,
        "",
        "📍 Source",
        f"└─ {cfg.source_title or 'Unknown'}",
        "",
        "🎯 Destination",
        f"└─ {_dest_label(cfg)}",
        "",
        "📦 Range",
        f"└─ #{start_id} → #{end_id}",
        "",
        "📨 Forwarded",
        f"└─ {forwarded}",
        "",
        "⏭️ Skipped",
        f"└─ {skipped}",
        "",
        "❌ Failed",
        f"└─ {failed}",
        "",
        "📌 First message",
        f"└─ {'Pinned' if pinned_done else 'Not pinned'}",
        "",
        "🔄 Status",
        f"└─ {status_label}",
        "",
        "╰────────────────────────╯",
    ]
    return "\n".join(lines)


class ProgressUI:
    """
    Phase 3: owns the single editable progress message for one job.
    Wraps create / edit-with-fallback-recreate / persistence of its own ID.
    """

    def __init__(self, bot: Bot, user_id: int, chat_id: int, message_id: Optional[int] = None):
        self.bot = bot
        self.user_id = user_id
        self.chat_id = chat_id
        self.message_id = message_id

    async def ensure_created(self, initial_text: str) -> None:
        if self.message_id is not None:
            return
        try:
            msg = await self.bot.send_message(self.chat_id, initial_text, parse_mode="HTML")
            self.message_id = msg.message_id
            await save_progress_message_ref(self.user_id, self.chat_id, self.message_id)
        except Exception as e:
            logger.error(f"[user={self.user_id}] Failed to create progress message: {e}")

    async def update(self, text: str) -> None:
        """Edit the persistent progress message; recreate it if edit fails for any reason."""
        if self.message_id is None:
            await self.ensure_created(text)
            return
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception as e:
            # Covers: message deleted, "message is not modified", too old to edit, etc.
            logger.warning(
                f"[user={self.user_id}] Could not edit progress message ({e}). Recreating."
            )
            try:
                msg = await self.bot.send_message(self.chat_id, text, parse_mode="HTML")
                self.message_id = msg.message_id
                await save_progress_message_ref(self.user_id, self.chat_id, self.message_id)
            except Exception as e2:
                logger.error(f"[user={self.user_id}] Failed to recreate progress message: {e2}")


async def forward_range(
    bot: Bot,
    owner_chat_id: int,
    user_id: int,
    cfg: BotConfig,
    settings: BotSettings,
    start_id: int,
    end_id: int,
) -> None:
    """
    Core forwarding loop.

    Phase 1: resumes from max(start_id, last_processed_message_id + 1).
    Phase 2: pins the first successfully forwarded destination message (once),
             sends "That's it ♥️" to the destination on completion (once).
    Phase 3: single editable progress message in owner_chat_id, edited every
             ~20 successful forwards and finalized into a summary at the end.
    """
    destination_chat_id = cfg.destination_chat_id
    destination_thread_id = cfg.destination_thread_id
    source_chat_id = cfg.source_chat_id
    delay = settings.delay_seconds

    state = await load_state(user_id)
    resume_start = start_id
    if state.last_processed_message_id is not None:
        resume_start = max(start_id, state.last_processed_message_id + 1)

    total = end_id - start_id + 1
    forwarded = 0
    skipped = state.skipped_count or 0
    failed = state.failed_count or 0
    consecutive_failures = 0
    pinned_done = state.pinned_done or False
    completion_sent = state.completion_sent or False

    is_resume = resume_start > start_id

    # job_started_at: keep the original start time across resumes for stable ETA math.
    job_started_at = state.job_started_at
    if job_started_at is None:
        job_started_at = datetime.now(timezone.utc)
        await set_job_started_at(user_id, job_started_at)
    if job_started_at.tzinfo is None:
        job_started_at = job_started_at.replace(tzinfo=timezone.utc)

    progress_chat_id = state.progress_chat_id or owner_chat_id
    progress = ProgressUI(bot, user_id, progress_chat_id, state.progress_message_id)

    # Phase 3: immediately reflect Recovering vs fresh Forwarding, using the
    # existing progress message if one is already persisted.
    initial_status = "recovering" if is_resume else "forwarding"
    initial_text = _build_progress_text(
        cfg, start_id, end_id,
        current_id=max(resume_start - 1, start_id),
        forwarded=forwarded, skipped=skipped, failed=failed,
        status=initial_status, eta_seconds=None,
    )
    if progress.message_id is not None:
        await progress.update(initial_text)  # existing message -> edit to Recovering
    else:
        await progress.ensure_created(initial_text)  # fresh job -> create new message

    await set_job_status(user_id, "forwarding")

    logger.info(
        f"[user={user_id}] Forwarding task started: source={source_chat_id}, "
        f"dest={destination_chat_id}, thread={destination_thread_id}, "
        f"range={start_id}\u2013{end_id}, resume_start={resume_start}, "
        f"resumed={is_resume}, total={total}"
    )

    if resume_start > end_id:
        # Nothing left to do (e.g. crash happened right after the last message
        # was checkpointed but before finalize ran).
        await _finalize_completed(
            bot, user_id, cfg, start_id, end_id, forwarded, skipped, failed,
            pinned_done, completion_sent, progress,
        )
        return

    last_progress_edit_at = resume_start - 1  # for the "flip Recovering->Forwarding" edit below
    flipped_to_forwarding = not is_resume  # if fresh job, we're already showing Forwarding

    for message_id in range(resume_start, end_id + 1):
        if await is_stop_requested(user_id):
            logger.info(f"[user={user_id}] Stop flag detected at message_id={message_id}. Halting.")
            await _finalize_cancelled(user_id, cfg, start_id, end_id, forwarded, skipped, failed, pinned_done, progress)
            return

        if not flipped_to_forwarding:
            # First loop iteration after a resume: flip Recovering -> Forwarding.
            text = _build_progress_text(
                cfg, start_id, end_id, message_id, forwarded, skipped, failed,
                status="forwarding", eta_seconds=None,
            )
            await progress.update(text)
            flipped_to_forwarding = True

        result, dest_message_id = await _copy_single_message(
            bot=bot,
            source_chat_id=source_chat_id,
            destination_chat_id=destination_chat_id,
            destination_thread_id=destination_thread_id,
            message_id=message_id,
            on_flood_wait=lambda wait_s: _notify_sleeping(
                progress, cfg, start_id, end_id, message_id, forwarded, skipped, failed, wait_s
            ),
        )

        if result == "success":
            forwarded += 1
            consecutive_failures = 0

            # Phase 2: pin the first successful destination message, exactly once.
            if not pinned_done and dest_message_id is not None:
                try:
                    await bot.pin_chat_message(
                        chat_id=destination_chat_id,
                        message_id=dest_message_id,
                        disable_notification=True,
                    )
                    logger.info(f"[user={user_id}] Pinned first forwarded message id={dest_message_id}.")
                except Exception as e:
                    # Pin failure must never stop forwarding.
                    logger.warning(f"[user={user_id}] Pin failed (ignored): {e}")
                finally:
                    # Mark done regardless of pin success — we only ever attempt
                    # a pin for the first successful message of a job.
                    pinned_done = True
                    await mark_pinned(user_id)

        elif result == "skipped":
            skipped += 1
            consecutive_failures = 0
        else:  # "failed"
            failed += 1
            consecutive_failures += 1

        await checkpoint_progress(user_id, message_id, failed, skipped)

        if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            logger.error(
                f"[user={user_id}] Aborting job: {consecutive_failures} consecutive failures "
                f"at message_id={message_id}."
            )
            await _finalize_failed(user_id, cfg, start_id, end_id, forwarded, skipped, failed, pinned_done, progress)
            return

        if forwarded > 0 and forwarded % PROGRESS_EDIT_INTERVAL == 0:
            eta = _estimate_eta(job_started_at, message_id, start_id, end_id)
            text = _build_progress_text(
                cfg, start_id, end_id, message_id, forwarded, skipped, failed,
                status="forwarding", eta_seconds=eta,
            )
            await progress.update(text)

        await asyncio.sleep(delay)

    await _finalize_completed(
        bot, user_id, cfg, start_id, end_id, forwarded, skipped, failed,
        pinned_done, completion_sent, progress,
    )


def _estimate_eta(job_started_at: datetime, current_id: int, start_id: int, end_id: int) -> Optional[float]:
    """
    Approximate ETA from elapsed time / messages-done-so-far, extrapolated to
    remaining messages. Avoids erratic estimates by requiring a minimum of
    progress before producing a number.
    """
    done = current_id - start_id + 1
    remaining = end_id - current_id
    if done <= 0 or remaining <= 0:
        return None
    now = datetime.now(timezone.utc)
    elapsed = (now - job_started_at).total_seconds()
    if elapsed <= 0:
        return None
    rate = elapsed / done  # seconds per message, averaged over the whole job incl. resumes
    return rate * remaining


async def _notify_sleeping(
    progress: "ProgressUI",
    cfg: BotConfig,
    start_id: int,
    end_id: int,
    current_id: int,
    forwarded: int,
    skipped: int,
    failed: int,
    wait_seconds: int,
) -> None:
    """Called only for meaningful waits (FloodWait), not the routine per-message delay."""
    text = _build_progress_text(
        cfg, start_id, end_id, current_id, forwarded, skipped, failed,
        status="sleeping", eta_seconds=None, sleeping_seconds=wait_seconds,
    )
    await progress.update(text)


async def _copy_single_message(
    bot: Bot,
    source_chat_id: int,
    destination_chat_id: int,
    destination_thread_id: Optional[int],
    message_id: int,
    max_retries: int = 3,
    on_flood_wait=None,
) -> Tuple[str, Optional[int]]:
    """
    Returns (result, destination_message_id) where result is one of:
      "success" - copied ok, destination_message_id is set
      "skipped" - expected unavailable/non-forwardable condition
      "failed"  - attempted but ultimately failed after retries/errors
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

            sent = await bot.copy_message(**kwargs)
            return "success", sent.message_id

        except TelegramRetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(
                f"FloodWait on message_id={message_id}: sleeping {wait}s (attempt {attempt}/{max_retries})"
            )
            if on_flood_wait is not None:
                try:
                    await on_flood_wait(wait)
                except Exception as ui_e:
                    logger.warning(f"Progress 'Sleeping' update failed (ignored): {ui_e}")
            await asyncio.sleep(wait)

        except TelegramBadRequest as e:
            error_text = str(e).lower()
            if "message to copy not found" in error_text or "message_id_invalid" in error_text:
                logger.warning(f"Message {message_id} not found. Skipping.")
                return "skipped", None
            if "replied message not found" in error_text:
                if destination_thread_id is not None:
                    logger.warning(f"Topic thread error for message {message_id}. Retrying without thread_id.")
                    try:
                        sent = await bot.copy_message(
                            chat_id=destination_chat_id,
                            from_chat_id=source_chat_id,
                            message_id=message_id,
                        )
                        return "success", sent.message_id
                    except Exception as fallback_e:
                        logger.error(f"Fallback also failed for message {message_id}: {fallback_e}")
                        return "failed", None
            logger.error(f"TelegramBadRequest for message {message_id}: {e}")
            return "failed", None

        except TelegramForbiddenError as e:
            logger.error(f"Bot forbidden (message {message_id}): {e}")
            return "failed", None

        except Exception as e:
            logger.error(f"Unexpected error copying message {message_id} (attempt {attempt}): {e}")
            if attempt == max_retries:
                return "failed", None
            await asyncio.sleep(2)

    return "failed", None


async def _send_destination_completion(bot: Bot, user_id: int, cfg: BotConfig, completion_sent: bool) -> bool:
    """Send 'That's it ♥️' to the destination exactly once. Returns updated completion_sent."""
    if completion_sent:
        return completion_sent
    try:
        kwargs = {"chat_id": cfg.destination_chat_id, "text": "That's it ♥️"}
        if cfg.destination_thread_id is not None:
            kwargs["message_thread_id"] = cfg.destination_thread_id
        await bot.send_message(**kwargs)
    except Exception as e:
        logger.warning(f"[user={user_id}] Could not send destination completion message: {e}")
    # Mark sent regardless of delivery outcome — we only ever attempt this once
    # per job, matching the pin's "attempt once" semantics.
    await mark_completion_sent(user_id)
    return True


async def _finalize_completed(
    bot: Bot,
    user_id: int,
    cfg: BotConfig,
    start_id: int,
    end_id: int,
    forwarded: int,
    skipped: int,
    failed: int,
    pinned_done: bool,
    completion_sent: bool,
    progress: "ProgressUI",
) -> None:
    try:
        state = await load_state(user_id)
        state.active = False
        state.stop_flag = False
        state.status = "completed"
        state.cancelled = False
        await save_state(state)
    except Exception as e:
        logger.error(f"[user={user_id}] Failed to finalize completed state: {e}")

    logger.info(f"[user={user_id}] Forwarding completed. Forwarded={forwarded}, Skipped={skipped}, Failed={failed}")

    completion_sent = await _send_destination_completion(bot, user_id, cfg, completion_sent)

    summary = _build_summary_text(cfg, start_id, end_id, forwarded, skipped, failed, "completed", pinned_done)
    await progress.update(summary)


async def _finalize_cancelled(
    user_id: int,
    cfg: BotConfig,
    start_id: int,
    end_id: int,
    forwarded: int,
    skipped: int,
    failed: int,
    pinned_done: bool,
    progress: "ProgressUI",
) -> None:
    """
    Intentional Stop: persist status="cancelled" and cancelled=True so
    resume_on_startup() never relaunches this job.
    """
    try:
        state = await load_state(user_id)
        state.active = False
        state.stop_flag = False
        state.status = "cancelled"
        state.cancelled = True
        await save_state(state)
    except Exception as e:
        logger.error(f"[user={user_id}] Failed to finalize cancelled state: {e}")

    summary = _build_summary_text(cfg, start_id, end_id, forwarded, skipped, failed, "cancelled", pinned_done)
    await progress.update(summary)


async def _finalize_failed(
    user_id: int,
    cfg: BotConfig,
    start_id: int,
    end_id: int,
    forwarded: int,
    skipped: int,
    failed: int,
    pinned_done: bool,
    progress: "ProgressUI",
) -> None:
    try:
        state = await load_state(user_id)
        state.active = False
        state.stop_flag = False
        state.status = "failed"
        await save_state(state)
    except Exception as e:
        logger.error(f"[user={user_id}] Failed to finalize failed state: {e}")

    summary = _build_summary_text(cfg, start_id, end_id, forwarded, skipped, failed, "failed", pinned_done)
    await progress.update(summary)
