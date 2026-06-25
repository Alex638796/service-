import asyncio
import logging
import os
import sys

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from database import connect_db, close_db
from handlers import private, group, admin
from services.task_manager import resume_on_startup

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ─── Health check HTTP server ─────────────────────────────────────────────────

PORT = int(os.getenv("PORT", 10000))


async def handle_root(request: web.Request) -> web.Response:
    return web.Response(text="OK", status=200)


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK", status=200)


async def run_health_server() -> None:
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info(f"Health server listening on 0.0.0.0:{PORT}")
    await asyncio.Event().wait()


# ─── Bot + Dispatcher ─────────────────────────────────────────────────────────

async def run_bot() -> None:
    config.validate()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())

    # Router registration order matters.
    # group router first: catches /setdestination in group context.
    # admin router second: owner-only admin panel commands.
    # private router last: all regular user commands.
    dp.include_router(group.router)
    dp.include_router(admin.router)   # NEW: admin panel router
    dp.include_router(private.router)

    @dp.startup()
    async def on_startup() -> None:
        logger.info("Connecting to MongoDB...")
        await connect_db()
        bot_info = await bot.get_me()
        logger.info(f"Bot: @{bot_info.username} (id={bot_info.id})")
        await resume_on_startup(bot=bot, owner_chat_id=config.OWNER_ID)
        logger.info("Bot is ready.")

    @dp.shutdown()
    async def on_shutdown() -> None:
        logger.info("Shutting down...")
        await close_db()
        await bot.session.close()
        logger.info("Shutdown complete.")

    logger.info("Starting polling...")
    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,
    )


# ─── Entry point ──────────────────────────────────────────────────────────────

async def main() -> None:
    await asyncio.gather(
        run_health_server(),
        run_bot(),
    )


if __name__ == "__main__":
    asyncio.run(main())
