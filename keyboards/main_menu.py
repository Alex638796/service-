from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    """
    Main menu keyboard.
    Change from private bot: accepts is_owner flag.
    Owner sees an extra Admin Panel button at the bottom.
    Regular allowed users see the same menu as before.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📢 Set Source", callback_data="menu_setsource"),
    )
    builder.row(
        InlineKeyboardButton(text="📌 Arm Topic Mode", callback_data="menu_arm_topic"),
        InlineKeyboardButton(text="👥 Set Normal Group", callback_data="menu_set_normal_group"),
    )
    builder.row(
        InlineKeyboardButton(text="▶️ Range Forward", callback_data="menu_range"),
    )
    builder.row(
        InlineKeyboardButton(text="⏹ Stop Forwarding", callback_data="menu_stop"),
        InlineKeyboardButton(text="📊 Status", callback_data="menu_status"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Settings", callback_data="menu_settings"),
    )
    # NEW: owner-only admin panel button
    if is_owner:
        builder.row(
            InlineKeyboardButton(text="🛡 Admin Panel", callback_data="menu_admin"),
        )
    return builder.as_markup()


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    """NEW: Owner-only admin panel keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👥 All Users", callback_data="admin_users_all"),
    )
    builder.row(
        InlineKeyboardButton(text="✅ Allowed", callback_data="admin_users_allowed"),
        InlineKeyboardButton(text="⏳ Pending", callback_data="admin_users_pending"),
        InlineKeyboardButton(text="🚫 Banned", callback_data="admin_users_banned"),
    )
    builder.row(
        InlineKeyboardButton(text="📈 Stats", callback_data="admin_stats"),
        InlineKeyboardButton(text="⚙️ Active Tasks", callback_data="admin_tasks"),
    )
    builder.row(
        InlineKeyboardButton(text="📣 Broadcast", callback_data="admin_broadcast"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Back to Menu", callback_data="menu_back"),
    )
    return builder.as_markup()


def user_action_keyboard(user_id: int, current_status: str) -> InlineKeyboardMarkup:
    """NEW: Per-user action buttons shown when owner views a user."""
    builder = InlineKeyboardBuilder()
    if current_status != "allowed":
        builder.row(
            InlineKeyboardButton(text="✅ Allow", callback_data=f"admin_allow_{user_id}"),
        )
    if current_status != "banned":
        builder.row(
            InlineKeyboardButton(text="🚫 Ban", callback_data=f"admin_ban_{user_id}"),
        )
    builder.row(
        InlineKeyboardButton(text="🔙 Back to Users", callback_data="admin_users_all"),
    )
    return builder.as_markup()


def confirm_range_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Confirm & Start", callback_data="range_confirm"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="range_cancel"),
    )
    return builder.as_markup()


def settings_keyboard(current_delay: float) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⏱ Set Delay", callback_data="settings_set_delay"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Back to Menu", callback_data="menu_back"),
    )
    return builder.as_markup()


def back_to_menu_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔙 Back to Menu", callback_data="menu_back"),
    )
    return builder.as_markup()


def back_to_admin_keyboard() -> InlineKeyboardMarkup:
    """NEW: Back button that returns to admin panel."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔙 Back to Admin", callback_data="menu_admin"),
    )
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_flow"),
    )
    return builder.as_markup()


def confirm_broadcast_keyboard() -> InlineKeyboardMarkup:
    """NEW: Confirm before sending broadcast to all users."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📣 Send to All", callback_data="broadcast_confirm"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="broadcast_cancel"),
    )
    return builder.as_markup()
