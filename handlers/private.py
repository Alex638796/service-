import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ChatType

from utils.auth import owner_required, allowed_or_owner_required, is_owner
from utils.helpers import (
    load_config,
    save_config,
    load_state,
    save_state,
    load_settings,
    save_settings,
    set_topic_capture_mode,
    extract_forwarded_message_id,
    extract_forwarded_chat_id,
    extract_forwarded_chat_title,
    build_status_text,
    upsert_user_on_activity,
)
from models.config_model import BotConfig, ForwardingState, BotSettings
from keyboards.main_menu import (
    main_menu_keyboard,
    confirm_range_keyboard,
    settings_keyboard,
    back_to_menu_keyboard,
    cancel_keyboard,
)
from services.task_manager import start_forwarding_task, stop_forwarding_task
from config import config

logger = logging.getLogger(__name__)
router = Router()


# ─── FSM States ───────────────────────────────────────────────────────────────

class SetSourceStates(StatesGroup):
    waiting_for_forward = State()


class RangeStates(StatesGroup):
    waiting_for_first = State()
    waiting_for_last = State()
    waiting_for_confirm = State()


class SetNormalGroupStates(StatesGroup):
    waiting_for_forward = State()


class SettingsStates(StatesGroup):
    waiting_for_delay = State()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_private(message: Message) -> bool:
    return message.chat.type == ChatType.PRIVATE


def _owner_flag(message: Message) -> bool:
    """Returns True if sender is owner — used for keyboard rendering."""
    if message.from_user is None:
        return False
    return message.from_user.id == config.OWNER_ID


def _owner_flag_cb(callback: CallbackQuery) -> bool:
    if callback.from_user is None:
        return False
    return callback.from_user.id == config.OWNER_ID


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    if not _is_private(message):
        return
    # /start is the entry point — run auth check which also handles
    # unknown user registration and owner notification
    if not await allowed_or_owner_required(message):
        return
    await state.clear()
    # Track usage for allowed users (not owner — owner isn't in users collection)
    if not is_owner(message):
        await upsert_user_on_activity(message)
    await message.answer(
        "👋 <b>Telegram Forward Bot</b>\n\n"
        "Use the menu below to configure and control forwarding.",
        reply_markup=main_menu_keyboard(is_owner=_owner_flag(message)),
        parse_mode="HTML",
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    if not _is_private(message):
        return
    if not await allowed_or_owner_required(message):
        return
    await state.clear()
    if not is_owner(message):
        await upsert_user_on_activity(message)
    await message.answer(
        "📋 <b>Main Menu</b>",
        reply_markup=main_menu_keyboard(is_owner=_owner_flag(message)),
        parse_mode="HTML",
    )


# ─── /setsource ───────────────────────────────────────────────────────────────

@router.message(Command("setsource"))
async def cmd_setsource(message: Message, state: FSMContext) -> None:
    if not _is_private(message):
        return
    if not await allowed_or_owner_required(message):
        return
    if not is_owner(message):
        await upsert_user_on_activity(message)
    await state.set_state(SetSourceStates.waiting_for_forward)
    await message.answer(
        "📢 <b>Set Source Channel</b>\n\n"
        "Forward any message from your source channel to this chat.\n"
        "The bot will extract the channel ID automatically.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(SetSourceStates.waiting_for_forward)
async def handle_source_forward(message: Message, state: FSMContext) -> None:
    if not await allowed_or_owner_required(message):
        return

    user_id = message.from_user.id
    chat_id = extract_forwarded_chat_id(message)
    chat_title = extract_forwarded_chat_title(message)

    if chat_id is None:
        await message.answer(
            "⚠️ Could not extract channel info from this message.\n\n"
            "Please forward a message <b>directly from the source channel</b> "
            "(not from a user or another bot).",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    cfg = await load_config(user_id)
    cfg.user_id = user_id
    cfg.source_chat_id = chat_id
    cfg.source_title = chat_title or str(chat_id)
    await save_config(cfg)
    await state.clear()

    await message.answer(
        f"✅ <b>Source channel saved!</b>\n\n"
        f"📢 Title: <b>{cfg.source_title}</b>\n"
        f"🆔 ID: <code>{chat_id}</code>",
        reply_markup=main_menu_keyboard(is_owner=_owner_flag(message)),
        parse_mode="HTML",
    )
    logger.info(f"[user={user_id}] Source channel set: chat_id={chat_id}")


# ─── /arm_topic_mode ──────────────────────────────────────────────────────────

@router.message(Command("arm_topic_mode"))
async def cmd_arm_topic_mode(message: Message, state: FSMContext) -> None:
    if not _is_private(message):
        return
    if not await allowed_or_owner_required(message):
        return
    if not is_owner(message):
        await upsert_user_on_activity(message)

    user_id = message.from_user.id
    await state.clear()
    await set_topic_capture_mode(user_id, True)
    await message.answer(
        "🎯 <b>Topic Capture Mode Enabled</b>\n\n"
        "Now go to your destination supergroup, open the desired topic, and send:\n\n"
        "<code>/setdestination</code>\n\n"
        "⏳ This mode expires in <b>10 minutes</b>.",
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )
    logger.info(f"[user={user_id}] Topic capture mode armed.")


# ─── Set Normal Group ─────────────────────────────────────────────────────────

@router.message(Command("setnormalgroup"))
async def cmd_set_normal_group(message: Message, state: FSMContext) -> None:
    if not _is_private(message):
        return
    if not await allowed_or_owner_required(message):
        return
    if not is_owner(message):
        await upsert_user_on_activity(message)

    user_id = message.from_user.id
    # Arm capture mode so /setdestination works in group
    await set_topic_capture_mode(user_id, True)
    await state.set_state(SetNormalGroupStates.waiting_for_forward)
    await message.answer(
        "👥 <b>Set Normal Group Destination</b>\n\n"
        "👉 <b>Recommended:</b> Go to the group and send /setdestination there.\n\n"
        "Capture mode is now armed for <b>10 minutes</b>.\n"
        "Or forward any message from your destination group here.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(SetNormalGroupStates.waiting_for_forward)
async def handle_normal_group_forward(message: Message, state: FSMContext) -> None:
    if not await allowed_or_owner_required(message):
        return

    user_id = message.from_user.id
    chat_id = extract_forwarded_chat_id(message)
    chat_title = extract_forwarded_chat_title(message)

    if chat_id is None:
        await message.answer(
            "⚠️ Could not extract group info.\n\n"
            "Please forward a message from the destination group, "
            "or go to the group and send /setdestination directly.",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    cfg = await load_config(user_id)
    cfg.user_id = user_id
    cfg.destination_chat_id = chat_id
    cfg.destination_title = chat_title or str(chat_id)
    cfg.destination_type = "normal_group"
    cfg.destination_thread_id = None
    await save_config(cfg)
    await set_topic_capture_mode(user_id, False)
    await state.clear()

    await message.answer(
        f"✅ <b>Destination group saved!</b>\n\n"
        f"👥 Title: <b>{cfg.destination_title}</b>\n"
        f"🆔 ID: <code>{chat_id}</code>\n"
        f"📋 Type: Normal group",
        reply_markup=main_menu_keyboard(is_owner=_owner_flag(message)),
        parse_mode="HTML",
    )
    logger.info(f"[user={user_id}] Normal group destination set: chat_id={chat_id}")


# ─── /range ───────────────────────────────────────────────────────────────────

@router.message(Command("range"))
async def cmd_range(message: Message, state: FSMContext) -> None:
    if not _is_private(message):
        return
    if not await allowed_or_owner_required(message):
        return
    if not is_owner(message):
        await upsert_user_on_activity(message)

    user_id = message.from_user.id
    cfg = await load_config(user_id)

    if not cfg.is_fully_configured():
        missing = []
        if not cfg.is_source_configured():
            missing.append("source channel (use /setsource)")
        if not cfg.is_destination_configured():
            missing.append("destination (use /arm_topic_mode + /setdestination)")
        await message.answer(
            f"⚠️ Bot is not fully configured yet.\n\nMissing: {', '.join(missing)}",
            reply_markup=main_menu_keyboard(is_owner=_owner_flag(message)),
            parse_mode="HTML",
        )
        return

    current_state = await load_state(user_id)
    if current_state.active:
        await message.answer(
            "⚠️ A forwarding task is already running.\n"
            "Use /stop to stop it before starting a new range.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    await state.set_state(RangeStates.waiting_for_first)
    await message.answer(
        "📨 <b>Range Forwarding Setup</b>\n\n"
        "<b>Step 1 of 2:</b> Forward the <b>FIRST</b> message of your desired range "
        "from the source channel to this chat.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(RangeStates.waiting_for_first)
async def handle_range_first(message: Message, state: FSMContext) -> None:
    if not await allowed_or_owner_required(message):
        return

    user_id = message.from_user.id
    msg_id = extract_forwarded_message_id(message)

    if msg_id is None:
        await message.answer(
            "⚠️ Could not extract message ID.\n\n"
            "Please forward a message <b>directly from the source channel</b>.",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    src_chat_id = extract_forwarded_chat_id(message)
    cfg = await load_config(user_id)
    if src_chat_id and src_chat_id != cfg.source_chat_id:
        await message.answer(
            f"⚠️ This message is from a different channel (<code>{src_chat_id}</code>).\n\n"
            f"Please forward from the configured source: "
            f"<b>{cfg.source_title}</b> (<code>{cfg.source_chat_id}</code>).",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    await state.update_data(first_id=msg_id)
    await state.set_state(RangeStates.waiting_for_last)
    await message.answer(
        f"✅ First message ID captured: <code>{msg_id}</code>\n\n"
        f"<b>Step 2 of 2:</b> Now forward the <b>LAST</b> message of your desired range.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(RangeStates.waiting_for_last)
async def handle_range_last(message: Message, state: FSMContext) -> None:
    if not await allowed_or_owner_required(message):
        return

    user_id = message.from_user.id
    msg_id = extract_forwarded_message_id(message)

    if msg_id is None:
        await message.answer(
            "⚠️ Could not extract message ID.\n\n"
            "Please forward a message <b>directly from the source channel</b>.",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    first_id = data.get("first_id")

    if msg_id < first_id:
        await message.answer(
            f"⚠️ Last message ID (<code>{msg_id}</code>) is before "
            f"first message ID (<code>{first_id}</code>).\n\n"
            "Please forward a message that comes <b>after</b> the first one.",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    if msg_id == first_id:
        await message.answer(
            "⚠️ First and last message IDs are the same. "
            "Please forward a different message as the last one.",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    cfg = await load_config(user_id)
    settings = await load_settings(user_id)
    total = msg_id - first_id + 1

    await state.update_data(last_id=msg_id)
    await state.set_state(RangeStates.waiting_for_confirm)

    if cfg.destination_type == "forum_topic":
        dest_desc = (
            f"📌 Topic: <b>{cfg.destination_title}</b> "
            f"(thread <code>{cfg.destination_thread_id}</code>)"
        )
    else:
        dest_desc = f"👥 Group: <b>{cfg.destination_title}</b>"

    await message.answer(
        f"📋 <b>Confirm Range Forwarding</b>\n\n"
        f"📢 Source: <b>{cfg.source_title}</b>\n"
        f"{dest_desc}\n\n"
        f"🔢 Range: <code>{first_id}</code> → <code>{msg_id}</code>\n"
        f"📨 Messages to forward: ~<b>{total}</b>\n"
        f"⏱ Delay: <b>{settings.delay_seconds}s</b> per message\n"
        f"🕐 Estimated time: ~<b>{int(total * settings.delay_seconds // 60)}m "
        f"{int(total * settings.delay_seconds % 60)}s</b>\n\n"
        f"Proceed?",
        reply_markup=confirm_range_keyboard(),
        parse_mode="HTML",
    )


# ─── /stop ────────────────────────────────────────────────────────────────────

@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    if not _is_private(message):
        return
    if not await allowed_or_owner_required(message):
        return
    await _do_stop(message)


async def _do_stop(message: Message) -> None:
    user_id = message.from_user.id
    state = await load_state(user_id)
    if not state.active:
        await message.answer(
            "ℹ️ No forwarding task is currently running.",
            reply_markup=main_menu_keyboard(is_owner=_owner_flag(message)),
        )
        return
    await stop_forwarding_task(user_id)
    await message.answer(
        "⏹ <b>Stop signal sent.</b>\n\n"
        "The forwarding task will stop after the current message completes.",
        reply_markup=main_menu_keyboard(is_owner=_owner_flag(message)),
        parse_mode="HTML",
    )


# ─── /status ──────────────────────────────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _is_private(message):
        return
    if not await allowed_or_owner_required(message):
        return
    user_id = message.from_user.id
    text = await build_status_text(user_id)
    await message.answer(
        text,
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )


# ─── /setdelay ────────────────────────────────────────────────────────────────

@router.message(Command("setdelay"))
async def cmd_setdelay(message: Message, state: FSMContext) -> None:
    if not _is_private(message):
        return
    if not await allowed_or_owner_required(message):
        return
    user_id = message.from_user.id
    settings = await load_settings(user_id)
    await state.set_state(SettingsStates.waiting_for_delay)
    await message.answer(
        f"⏱ <b>Set Forwarding Delay</b>\n\n"
        f"Current delay: <b>{settings.delay_seconds}s</b>\n\n"
        f"Enter a new delay in seconds (e.g. <code>2.5</code>).\n"
        f"Minimum: 1.0s — Recommended: 3.0s",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(SettingsStates.waiting_for_delay)
async def handle_delay_input(message: Message, state: FSMContext) -> None:
    if not await allowed_or_owner_required(message):
        return
    try:
        value = float(message.text.strip())
        if value < 1.0:
            raise ValueError("Too low")
    except (ValueError, AttributeError):
        await message.answer(
            "⚠️ Invalid value. Enter a number ≥ 1.0 (e.g. <code>3.0</code>).",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    user_id = message.from_user.id
    settings = await load_settings(user_id)
    settings.user_id = user_id
    settings.delay_seconds = value
    await save_settings(settings)
    await state.clear()
    await message.answer(
        f"✅ Delay updated to <b>{value}s</b>.",
        reply_markup=main_menu_keyboard(is_owner=_owner_flag(message)),
        parse_mode="HTML",
    )


# ─── Callback query handlers ──────────────────────────────────────────────────

@router.callback_query(F.data == "menu_back")
async def cb_menu_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "📋 <b>Main Menu</b>",
        reply_markup=main_menu_keyboard(is_owner=_owner_flag_cb(callback)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_flow")
async def cb_cancel_flow(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ Cancelled.\n\n📋 <b>Main Menu</b>",
        reply_markup=main_menu_keyboard(is_owner=_owner_flag_cb(callback)),
        parse_mode="HTML",
    )
    await callback.answer("Cancelled.")


@router.callback_query(F.data == "menu_setsource")
async def cb_setsource(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    await state.set_state(SetSourceStates.waiting_for_forward)
    await callback.message.edit_text(
        "📢 <b>Set Source Channel</b>\n\n"
        "Forward any message from your source channel to this chat.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "menu_arm_topic")
async def cb_arm_topic(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id
    await state.clear()
    await set_topic_capture_mode(user_id, True)
    await callback.message.edit_text(
        "🎯 <b>Topic Capture Mode Enabled</b>\n\n"
        "Go to your destination supergroup, open the desired topic, and send:\n\n"
        "<code>/setdestination</code>\n\n"
        "⏳ This mode expires in <b>10 minutes</b>.",
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer("Topic capture mode armed.")


@router.callback_query(F.data == "menu_set_normal_group")
async def cb_set_normal_group(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id
    await state.clear()
    await set_topic_capture_mode(user_id, True)
    await callback.message.edit_text(
        "👥 <b>Set Normal Group Destination</b>\n\n"
        "Go to your destination group and send:\n\n"
        "<code>/setdestination</code>\n\n"
        "⏳ This mode expires in <b>10 minutes</b>.",
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer("Capture mode armed.")


@router.callback_query(F.data == "menu_range")
async def cb_range(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id
    cfg = await load_config(user_id)
    if not cfg.is_fully_configured():
        await callback.answer("Bot not fully configured. Set source and destination first.", show_alert=True)
        return
    current_state = await load_state(user_id)
    if current_state.active:
        await callback.answer("A forwarding task is already running. Stop it first.", show_alert=True)
        return
    await state.set_state(RangeStates.waiting_for_first)
    await callback.message.edit_text(
        "📨 <b>Range Forwarding Setup</b>\n\n"
        "<b>Step 1 of 2:</b> Forward the <b>FIRST</b> message of your desired range "
        "from the source channel to this chat.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "menu_stop")
async def cb_stop(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id
    state = await load_state(user_id)
    if not state.active:
        await callback.answer("No forwarding task is running.", show_alert=True)
        return
    await stop_forwarding_task(user_id)
    await callback.message.edit_text(
        "⏹ <b>Stop signal sent.</b>\n\n"
        "The task will stop after the current message.",
        reply_markup=main_menu_keyboard(is_owner=_owner_flag_cb(callback)),
        parse_mode="HTML",
    )
    await callback.answer("Stop signal sent.")


@router.callback_query(F.data == "menu_status")
async def cb_status(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id
    text = await build_status_text(user_id)
    await callback.message.edit_text(
        text,
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "menu_settings")
async def cb_settings(callback: CallbackQuery) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id
    settings = await load_settings(user_id)
    await callback.message.edit_text(
        f"⚙️ <b>Settings</b>\n\n"
        f"⏱ Current delay: <b>{settings.delay_seconds}s</b> per message",
        reply_markup=settings_keyboard(settings.delay_seconds),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "settings_set_delay")
async def cb_set_delay(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id
    settings = await load_settings(user_id)
    await state.set_state(SettingsStates.waiting_for_delay)
    await callback.message.edit_text(
        f"⏱ <b>Set Forwarding Delay</b>\n\n"
        f"Current: <b>{settings.delay_seconds}s</b>\n\n"
        f"Reply with a number ≥ 1.0 (e.g. <code>3.0</code>).",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "range_confirm")
async def cb_range_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user:
        return
    user_id = callback.from_user.id
    data = await state.get_data()
    first_id = data.get("first_id")
    last_id = data.get("last_id")

    if not first_id or not last_id:
        await callback.answer("Session expired. Please start /range again.", show_alert=True)
        await state.clear()
        return

    await state.clear()

    cfg = await load_config(user_id)
    settings = await load_settings(user_id)

    fwd_state = ForwardingState(
        user_id=user_id,
        active=True,
        stop_flag=False,
        start_message_id=first_id,
        end_message_id=last_id,
        last_processed_message_id=first_id - 1,
    )
    await save_state(fwd_state)

    await callback.message.edit_text(
        f"▶️ <b>Forwarding started!</b>\n\n"
        f"Range: <code>{first_id}</code> → <code>{last_id}</code>\n"
        f"I will update you on progress.\n\n"
        f"Use /stop to cancel at any time.",
        parse_mode="HTML",
    )
    await callback.answer("Forwarding started.")

    await start_forwarding_task(
        bot=bot,
        chat_id=callback.message.chat.id,
        user_id=user_id,
        cfg=cfg,
        settings=settings,
        start_id=first_id,
        end_id=last_id,
    )


@router.callback_query(F.data == "range_cancel")
async def cb_range_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ Range forwarding cancelled.",
        reply_markup=main_menu_keyboard(is_owner=_owner_flag_cb(callback)),
        parse_mode="HTML",
    )
    await callback.answer("Cancelled.")
