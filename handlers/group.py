import logging
from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ChatType

from utils.auth import is_allowed_or_owner
from utils.helpers import (
    is_topic_capture_active,
    set_topic_capture_mode,
    load_config,
    save_config,
)
from models.config_model import BotConfig

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("setdestination"))
async def cmd_setdestination(message: Message, bot: Bot) -> None:
    """
    Handles /setdestination sent inside a forum topic or normal group.
    Requires topic capture mode to be armed via /arm_topic_mode in private chat.

    Change from private bot:
    - Uses is_allowed_or_owner() instead of owner_required()
    - Passes user_id to all config/settings DB operations
    """
    if not await is_allowed_or_owner(message):
        return

    user_id = message.from_user.id

    # Must be in a group or supergroup
    chat_type = message.chat.type
    if chat_type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply(
            "⚠️ This command must be sent inside a group or supergroup, not in private chat.\n"
            "Use /arm_topic_mode in private chat first, then send /setdestination in your group/topic."
        )
        return

    # Topic capture mode must be armed for this user
    if not await is_topic_capture_active(user_id):
        await message.reply(
            "⚠️ Topic capture mode is not active.\n\n"
            "Go to bot private chat and run /arm_topic_mode first, "
            "then come back and send /setdestination here."
        )
        return

    chat_id = message.chat.id
    chat_title = message.chat.title or str(chat_id)
    thread_id = message.message_thread_id
    is_forum = getattr(message.chat, "is_forum", False)

    if is_forum:
        # Forum topic supergroup
        if not thread_id:
            await message.reply(
                "⚠️ Could not detect a topic thread ID.\n\n"
                "Make sure you are sending this command <b>inside a specific topic</b>, "
                "not in the General area of the forum.\n"
                "Please navigate into a named topic and try again.",
                parse_mode="HTML",
            )
            return

        # Verify via getChat
        try:
            chat_info = await bot.get_chat(chat_id)
            verified_forum = getattr(chat_info, "is_forum", False)
            if not verified_forum:
                await message.reply(
                    "⚠️ This supergroup does not have forum topics enabled.\n"
                    "Please enable Topics in group settings first."
                )
                return
        except Exception as e:
            logger.warning(f"Could not verify chat forum status: {e}")

        cfg = await load_config(user_id)
        cfg.user_id = user_id
        cfg.destination_chat_id = chat_id
        cfg.destination_title = chat_title
        cfg.destination_type = "forum_topic"
        cfg.destination_thread_id = thread_id
        await save_config(cfg)

        await set_topic_capture_mode(user_id, False)

        await message.reply(
            f"✅ <b>Destination topic saved!</b>\n\n"
            f"🏷 Group: <b>{chat_title}</b>\n"
            f"🆔 Group ID: <code>{chat_id}</code>\n"
            f"📌 Thread ID: <code>{thread_id}</code>\n\n"
            f"You can now use /range in private chat to start forwarding.",
            parse_mode="HTML",
        )
        logger.info(f"[user={user_id}] Destination forum topic set: chat_id={chat_id}, thread_id={thread_id}")

    else:
        # Normal group
        cfg = await load_config(user_id)
        cfg.user_id = user_id
        cfg.destination_chat_id = chat_id
        cfg.destination_title = chat_title
        cfg.destination_type = "normal_group"
        cfg.destination_thread_id = None
        await save_config(cfg)

        await set_topic_capture_mode(user_id, False)

        await message.reply(
            f"✅ <b>Destination group saved!</b>\n\n"
            f"🏷 Group: <b>{chat_title}</b>\n"
            f"🆔 Group ID: <code>{chat_id}</code>\n"
            f"📋 Type: Normal group\n\n"
            f"You can now use /range in private chat to start forwarding.",
            parse_mode="HTML",
        )
        logger.info(f"[user={user_id}] Destination normal group set: chat_id={chat_id}")
