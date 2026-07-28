import asyncio
import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

PING_INTERVAL_SECONDS = 600  # 10 minutes
PING_TIMEOUT_SECONDS = 15

# Per-user keep-alive task registry: user_id → asyncio.Task
_keepalive_tasks: dict[int, asyncio.Task] = {}


def start_keepalive(user_id: int) -> asyncio.Task | None:
    """
    Start a keep-alive pinger for this user's active forwarding job,
    unless one is already running for them (dedup guard).
    Returns the task, or None if not started.
    """
    existing = _keepalive_tasks.get(user_id)
    if existing and not existing.done():
        logger.info(f"[keepalive][user={user_id}] Already running, skipping duplicate start.")
        return existing

    task = asyncio.create_task(
        _keepalive_loop(user_id),
        name=f"keepalive_{user_id}",
    )
    _keepalive_tasks[user_id] = task
    logger.info(f"[keepalive][user={user_id}] Started.")
    return task


async def stop_keepalive(user_id: int) -> None:
    """
    Cancel and clean up the keep-alive task for this user, if any.
    Safe to call even if no task is running.
    """
    task = _keepalive_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # Defensive: keepalive cleanup must never raise into the caller.
            logger.warning(f"[keepalive][user={user_id}] Error during cancellation cleanup: {e}")
    logger.info(f"[keepalive][user={user_id}] Stopped.")


async def _keepalive_loop(user_id: int) -> None:
    base_url = os.getenv("RENDER_EXTERNAL_URL")
    if not base_url:
        logger.warning(
            f"[keepalive][user={user_id}] RENDER_EXTERNAL_URL not set — keepalive pinging disabled."
        )
        return

    health_url = base_url.rstrip("/") + "/health"

    try:
        async with aiohttp.ClientSession() as session:
            while True:
                await asyncio.sleep(PING_INTERVAL_SECONDS)
                try:
                    timeout = aiohttp.ClientTimeout(total=PING_TIMEOUT_SECONDS)
                    async with session.get(health_url, timeout=timeout) as resp:
                        logger.info(
                            f"[keepalive][user={user_id}] Ping sent to {health_url} "
                            f"→ status={resp.status}"
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # Never let a failed ping affect the forwarding task.
                    logger.warning(f"[keepalive][user={user_id}] Ping failed (ignored): {e}")
    except asyncio.CancelledError:
        logger.info(f"[keepalive][user={user_id}] Loop cancelled.")
        raise
