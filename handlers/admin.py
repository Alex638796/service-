import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ChatType

from utils.auth import owner_required, owner_required_callback, is_owner, is_owner_callback
from utils.helpers import (
    get_all_users,
    get_users_by_status,
    count_users_by_status,
    load_user,
    set_user_status,
    load_state,
)
from keyboards.main_menu import (
    admin_panel_keyboard,
    user_action_keyboard,
    back_to_admin_keyboard,
    back_to_menu_keyboard,
    confirm_broadcast_keyboard,
    cancel_keyboard,
)
from database import col_state
from config import config

logger = logging.getLogger(__name__)
router = Router()


# ─── FSM ──────────────────────────────────────────────────────────────────────

class BroadcastStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_confirm = State()


# ─── Private chat filter ──────────────────────────────────────────────────────

def _is_private(message: Message) -> bool:
    return message.chat.type == ChatType.PRIVATE


# ─── /users ───────────────────────────────────────────────────────────────────

@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not _is_private(message):
        return
    if not await owner_required(message):
        return
    await _send_all_users(message)


async def _send_all_users(message: Message) -> None:
    users = await get_all_users()
    counts = await count_users_by_status()

    if not users:
        await message.answer(
            "👥 <b>No users yet.</b>",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML",
        )
        return

    lines = [
        f"👥 <b>All Users</b>\n",
        f"✅ Allowed: <b>{counts['allowed']}</b>  "
        f"⏳ Pending: <b>{counts['pending']}</b>  "
        f"🚫 Banned: <b>{counts['banned']}</b>\n",
    ]

    for u in users:
        status_icon = {"allowed": "✅", "pending": "⏳", "banned": "🚫"}.get(u.status, "❓")
        username_str = f"@{u.username}" if u.username else "—"
        lines.append(
            f"{status_icon} <code>{u.user_id}</code> | {u.first_name or '—'} | "
            f"{username_str} | used: {u.usage_count}x"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n<i>...list truncated</i>"

    await message.answer(text, reply_markup=back_to_admin_keyboard(), parse_mode="HTML")


# ─── /allow, /ban, /unban ─────────────────────────────────────────────────────

@router.message(Command("allow"))
async def cmd_allow(message: Message) -> None:
    if not _is_private(message):
        return
    if not await owner_required(message):
        return
    await _handle_status_command(message, "allowed")


@router.message(Command("ban"))
async def cmd_ban(message: Message) -> None:
    if not _is_private(message):
        return
    if not await owner_required(message):
        return
    await _handle_status_command(message, "banned")


@router.message(Command("unban"))
async def cmd_unban(message: Message) -> None:
    if not _is_private(message):
        return
    if not await owner_required(message):
        return
    await _handle_status_command(message, "allowed")


async def _handle_status_command(message: Message, new_status: str) -> None:
    """Parse /allow <id>, /ban <id>, /unban <id> and update user status."""
    parts = message.text.strip().split()
    if len(parts) < 2:
        command = parts[0].lstrip("/")
        await message.answer(
            f"⚠️ Usage: <code>/{command} &lt;user_id&gt;</code>\n"
            f"Example: <code>/{command} 123456789</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("⚠️ Invalid user ID. Must be a number.")
        return

    if target_id == config.OWNER_ID:
        await message.answer("⚠️ Cannot change status of owner account.")
        return

    updated = await set_user_status(target_id, new_status)
    status_label = {"allowed": "✅ Allowed", "banned": "🚫 Banned"}.get(new_status, new_status)

    if updated:
        profile = await load_user(target_id)
        name = profile.display_name() if profile else str(target_id)
        await message.answer(
            f"{status_label}\n\n"
            f"User: <b>{name}</b> (<code>{target_id}</code>)\n"
            f"New status: <b>{new_status}</b>",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML",
        )
        logger.info(f"Owner set user {target_id} status → {new_status}")

        if new_status == "allowed":
            try:
                await message.bot.send_message(
                    target_id,
                    "✅ <b>Access granted!</b>\n\n"
                    "You can now use the bot. Send /start to begin.",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"Could not notify user {target_id} of approval: {e}")
    else:
        await message.answer(
            f"⚠️ User <code>{target_id}</code> not found in database.\n\n"
            f"They must send /start to the bot first before you can manage them.",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML",
        )


# ─── /tasks ───────────────────────────────────────────────────────────────────

@router.message(Command("tasks"))
async def cmd_tasks(message: Message) -> None:
    if not _is_private(message):
        return
    if not await owner_required(message):
        return
    await _send_active_tasks(message)


async def _send_active_tasks(message: Message) -> None:
    cursor = col_state().find({"active": True})
    docs = await cursor.to_list(length=100)

    if not docs:
        await message.answer(
            "⚙️ <b>No active forwarding tasks.</b>",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML",
        )
        return

    lines = [f"⚙️ <b>Active Forwarding Tasks</b> ({len(docs)})\n"]
    for doc in docs:
        uid = doc.get("user_id", "?")
        start = doc.get("start_message_id", "?")
        end = doc.get("end_message_id", "?")
        last = doc.get("last_processed_message_id", "?")
        profile = await load_user(uid)
        name = profile.display_name() if profile else str(uid)
        lines.append(
            f"👤 {name} (<code>{uid}</code>)\n"
            f"   Range: <code>{start}</code>→<code>{end}</code> | "
            f"Last: <code>{last}</code>"
        )

    await message.answer(
        "\n".join(lines),
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML",
    )


# ─── /broadcast ───────────────────────────────────────────────────────────────

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    if not _is_private(message):
        return
    if not await owner_required(message):
        return
    await state.set_state(BroadcastStates.waiting_for_message)
    await message.answer(
        "📣 <b>Broadcast</b>\n\n"
        "Send the message you want to broadcast to all allowed users.\n"
        "Supports text, photos, documents, and videos.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext) -> None:
    if not is_owner(message):
        return
    await state.update_data(
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )
    await state.set_state(BroadcastStates.waiting_for_confirm)

    allowed_users = await get_users_by_status("allowed")
    count = len(allowed_users)

    await message.answer(
        f"📋 <b>Confirm Broadcast</b>\n\n"
        f"This message will be sent to <b>{count}</b> allowed user(s).\n\n"
        f"Proceed?",
        reply_markup=confirm_broadcast_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "broadcast_confirm")
async def cb_broadcast_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not await owner_required_callback(callback):
        return

    data = await state.get_data()
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    await state.clear()

    if not source_chat_id or not source_message_id:
        await callback.answer("Session expired. Please run /broadcast again.", show_alert=True)
        return

    allowed_users = await get_users_by_status("allowed")
    sent = 0
    failed = 0

    await callback.message.edit_text(
        f"📣 Sending to {len(allowed_users)} users...",
        parse_mode="HTML",
    )

    for user in allowed_users:
        try:
            await bot.copy_message(
                chat_id=user.user_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Broadcast failed for user {user.user_id}: {e}")
            failed += 1

    await callback.message.edit_text(
        f"✅ <b>Broadcast complete!</b>\n\n"
        f"Sent: <b>{sent}</b> | Failed: <b>{failed}</b>",
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast_cancel")
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ Broadcast cancelled.",
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer("Cancelled.")


# ─── Callback handlers for admin panel ───────────────────────────────────────

@router.callback_query(F.data == "menu_admin")
async def cb_admin_panel(callback: CallbackQuery) -> None:
    if not await owner_required_callback(callback):
        return
    counts = await count_users_by_status()
    await callback.message.edit_text(
        f"🛡 <b>Admin Panel</b>\n\n"
        f"✅ Allowed: <b>{counts['allowed']}</b>\n"
        f"⏳ Pending: <b>{counts['pending']}</b>\n"
        f"🚫 Banned: <b>{counts['banned']}</b>",
        reply_markup=admin_panel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_users_all")
async def cb_admin_users_all(callback: CallbackQuery) -> None:
    if not await owner_required_callback(callback):
        return
    users = await get_all_users()
    counts = await count_users_by_status()
    await _edit_user_list(callback, users, counts, "All Users")


@router.callback_query(F.data == "admin_users_allowed")
async def cb_admin_users_allowed(callback: CallbackQuery) -> None:
    if not await owner_required_callback(callback):
        return
    users = await get_users_by_status("allowed")
    await _edit_user_list(callback, users, {}, "Allowed Users")


@router.callback_query(F.data == "admin_users_pending")
async def cb_admin_users_pending(callback: CallbackQuery) -> None:
    if not await owner_required_callback(callback):
        return
    users = await get_users_by_status("pending")
    await _edit_user_list(callback, users, {}, "Pending Users")


@router.callback_query(F.data == "admin_users_banned")
async def cb_admin_users_banned(callback: CallbackQuery) -> None:
    if not await owner_required_callback(callback):
        return
    users = await get_users_by_status("banned")
    await _edit_user_list(callback, users, {}, "Banned Users")


async def _edit_user_list(
    callback: CallbackQuery,
    users: list,
    counts: dict,
    title: str,
) -> None:
    if not users:
        await callback.message.edit_text(
            f"👥 <b>{title}</b>\n\nNo users in this category.",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    lines = [f"👥 <b>{title}</b> ({len(users)})\n"]
    if counts:
        lines.append(
            f"✅ {counts.get('allowed', 0)}  "
            f"⏳ {counts.get('pending', 0)}  "
            f"🚫 {counts.get('banned', 0)}\n"
        )

    for u in users:
        status_icon = {"allowed": "✅", "pending": "⏳", "banned": "🚫"}.get(u.status, "❓")
        username_str = f"@{u.username}" if u.username else "—"
        lines.append(
            f"{status_icon} <code>{u.user_id}</code> | "
            f"{u.first_name or '—'} | {username_str}"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n<i>...truncated</i>"

    await callback.message.edit_text(
        text,
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery) -> None:
    if not await owner_required_callback(callback):
        return

    counts = await count_users_by_status()
    cursor = col_state().find({"active": True})
    active_tasks = await cursor.to_list(length=100)

    all_users = await get_all_users()
    total_usage = sum(u.usage_count for u in all_users)

    await callback.message.edit_text(
        f"📈 <b>Bot Statistics</b>\n\n"
        f"👥 Total users: <b>{len(all_users)}</b>\n"
        f"✅ Allowed: <b>{counts['allowed']}</b>\n"
        f"⏳ Pending: <b>{counts['pending']}</b>\n"
        f"🚫 Banned: <b>{counts['banned']}</b>\n\n"
        f"⚙️ Active tasks: <b>{len(active_tasks)}</b>\n"
        f"📊 Total commands used: <b>{total_usage}</b>",
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_tasks")
async def cb_admin_tasks(callback: CallbackQuery) -> None:
    if not await owner_required_callback(callback):
        return

    cursor = col_state().find({"active": True})
    docs = await cursor.to_list(length=100)

    if not docs:
        await callback.message.edit_text(
            "⚙️ <b>No active forwarding tasks.</b>",
            reply_markup=back_to_admin_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    lines = [f"⚙️ <b>Active Tasks</b> ({len(docs)})\n"]
    for doc in docs:
        uid = doc.get("user_id", "?")
        start = doc.get("start_message_id", "?")
        end = doc.get("end_message_id", "?")
        last = doc.get("last_processed_message_id", "?")
        profile = await load_user(uid)
        name = profile.display_name() if profile else str(uid)
        lines.append(
            f"👤 {name} (<code>{uid}</code>)\n"
            f"   <code>{start}</code>→<code>{end}</code> | last: <code>{last}</code>"
        )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_allow_"))
async def cb_inline_allow(callback: CallbackQuery, bot: Bot) -> None:
    if not await owner_required_callback(callback):
        return

    target_id = int(callback.data.split("_")[-1])
    await set_user_status(target_id, "allowed")
    profile = await load_user(target_id)
    name = profile.display_name() if profile else str(target_id)

    await callback.message.edit_text(
        f"✅ <b>User allowed</b>\n\n"
        f"{name} (<code>{target_id}</code>) can now use the bot.",
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer(f"Allowed {target_id}")
    logger.info(f"Owner allowed user {target_id} via inline button.")

    try:
        await bot.send_message(
            target_id,
            "✅ <b>Access granted!</b>\n\nYou can now use the bot. Send /start to begin.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


@router.callback_query(F.data.startswith("admin_ban_"))
async def cb_inline_ban(callback: CallbackQuery) -> None:
    if not await owner_required_callback(callback):
        return

    target_id = int(callback.data.split("_")[-1])
    await set_user_status(target_id, "banned")
    profile = await load_user(target_id)
    name = profile.display_name() if profile else str(target_id)

    await callback.message.edit_text(
        f"🚫 <b>User banned</b>\n\n"
        f"{name} (<code>{target_id}</code>) has been banned.",
        reply_markup=back_to_admin_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer(f"Banned {target_id}")
    logger.info(f"Owner banned user {target_id} via inline button.")
