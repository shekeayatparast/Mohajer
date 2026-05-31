"""Admin panel handlers for the bot."""
from pyrogram import Client, filters, types
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from config.settings import ADMIN_IDS, PREMIUM_EMOJIS
from database.db_manager import (
    get_all_users, get_user_stats, ban_user, unban_user, set_user_level,
    log_admin_action, get_bot_setting, set_bot_setting, is_user_banned,
    get_user_level, create_user_if_not_exists
)
from utils.helpers import get_emoji, format_number

# Admin filter
def admin_filter(_, __, message: Message):
    return message.from_user.id in ADMIN_IDS

admin_filter = filters.create(admin_filter)

async def admin_panel(client: Client, message: Message):
    """Show admin panel main menu."""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{get_emoji('stats')} آمار کاربران", callback_data="admin_stats"),
            InlineKeyboardButton(f"{get_emoji('user')} مدیریت کاربران", callback_data="admin_users"),
        ],
        [
            InlineKeyboardButton(f"{get_emoji('broadcast')} پیام همگانی", callback_data="admin_broadcast"),
            InlineKeyboardButton(f"{get_emoji('settings')} تنظیمات ربات", callback_data="admin_settings"),
        ],
        [
            InlineKeyboardButton(f"{get_emoji('admin')} لاگ ادمین", callback_data="admin_logs"),
            InlineKeyboardButton(f"{get_emoji('ban')} لیست بن", callback_data="admin_banlist"),
        ],
        [
            InlineKeyboardButton(f"{get_emoji('vip')} سطح‌بندی", callback_data="admin_levels"),
            InlineKeyboardButton(f"{get_emoji('settings')} پروکسی‌ها", callback_data="admin_proxy"),
        ],
    ])
    
    stats_text = f"""
{get_emoji('admin')} **پنل مدیریت ربات** {get_emoji('crown')}

👤 ادمین: {message.from_user.first_name}

{get_emoji('stats')} **وضعیت فعلی:**
- حالت عملیاتی: عادی
- تعداد کل کاربران: در حال بارگذاری...
- دانلودهای امروز: در حال بارگذاری...

برای مشاهده جزئیات بیشتر، یکی از گزینه‌های زیر را انتخاب کنید.
"""
    
    await message.reply(stats_text, reply_markup=keyboard)

async def show_user_stats(client: Client, callback_query):
    """Show detailed user statistics."""
    if callback_query.from_user.id not in ADMIN_IDS:
        await callback_query.answer("❌ دسترسی ندارید!", show_alert=True)
        return
    
    all_users = get_all_users()
    total_users = len(all_users)
    
    # Get today's downloads (simplified)
    today_downloads = sum(1 for u in all_users if True)  # Placeholder
    
    text = f"""
{get_emoji('stats')} **آمار کامل کاربران**

👥 **تعداد کل کاربران:** {format_number(total_users)}
📊 **دانلودهای امروز:** {format_number(today_downloads)}

**توزیع سطح کاربران:**
"""
    
    # Count by level
    level_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for user in all_users:
        level = get_user_level(user['user_id'])
        level_counts[level] = level_counts.get(level, 0) + 1
    
    level_names = {0: "عادی", 1: "فعال", 2: "VIP", 3: "ادمین"}
    for level, count in level_counts.items():
        if count > 0:
            emoji = get_emoji(['free', 'star', 'vip', 'admin'][level])
            text += f"\n{emoji} {level_names[level]}: {count}"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(get_emoji('back') + " بازگشت", callback_data="admin_back")],
    ])
    
    await callback_query.message.edit_text(text, reply_markup=keyboard)

async def search_user(client: Client, message: Message):
    """Search for a user by ID."""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    if len(message.command) < 2:
        await message.reply("❌ لطفاً ID کاربر را وارد کنید:\n/search_user <user_id>")
        return
    
    try:
        user_id = int(message.command[1])
    except ValueError:
        await message.reply("❌ ID باید عدد باشد!")
        return
    
    create_user_if_not_exists(user_id)
    stats = get_user_stats(user_id)
    
    if not stats['user']:
        await message.reply(f"❌ کاربر {user_id} یافت نشد!")
        return
    
    user_info = stats['user']
    level_info = stats['level']
    
    text = f"""
{get_emoji('user')} **اطلاعات کاربر**

🆔 **ID:** `{user_info['user_id']}`
👤 **نام:** {user_info['first_name']} {user_info['last_name'] or ''}
📝 **یوزرنیم:** @{user_info['username'] or 'ندارد'}
🌐 **زبان:** {user_info['language']}
🔍 **پلتفرم جستجو:** {user_info['search_platform']}
📅 **عضویت:** {user_info['created_at']}
⏰ **آخرین فعالیت:** {user_info['last_active']}

**سطح دسترسی:** {level_info['level']}
📊 **دانلود کل:** {level_info['total_downloads']}
💾 **حجم دانلود:** {level_info['total_bytes'] / (1024*1024):.2f} MB
"""
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_emoji('ban') + " بن", callback_data=f"admin_ban_{user_id}"),
            InlineKeyboardButton(get_emoji('unban') + " آنبن", callback_data=f"admin_unban_{user_id}"),
        ],
        [
            InlineKeyboardButton("تنظیم سطح", callback_data=f"admin_setlevel_{user_id}"),
        ],
    ])
    
    await message.reply(text, reply_markup=keyboard)

async def ban_user_handler(client: Client, message: Message):
    """Ban a user."""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    if len(message.command) < 3:
        await message.reply("❌ استفاده صحیح:\n/ban <user_id> <reason> [days]")
        return
    
    try:
        user_id = int(message.command[1])
        reason = message.command[2]
        days = int(message.command[3]) if len(message.command) > 3 else None
    except ValueError:
        await message.reply("❌ ID و روز باید عدد باشند!")
        return
    
    # Prevent banning other admins
    if user_id in ADMIN_IDS:
        await message.reply("❌ نمی‌توانید ادمین‌های دیگر را بن کنید!")
        return
    
    ban_user(user_id, reason, message.from_user.id, days)
    
    days_text = f" به مدت {days} روز" if days else " به صورت دائم"
    await message.reply(f"✅ کاربر {user_id}{days_text} بن شد.\nدلیل: {reason}")
    
    log_admin_action(message.from_user.id, "ban", user_id, f"Reason: {reason}, Days: {days}")

async def unban_user_handler(client: Client, message: Message):
    """Unban a user."""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    if len(message.command) < 2:
        await message.reply("❌ استفاده صحیح:\n/unban <user_id>")
        return
    
    try:
        user_id = int(message.command[1])
    except ValueError:
        await message.reply("❌ ID باید عدد باشد!")
        return
    
    unban_user(user_id, message.from_user.id)
    await message.reply(f"✅ کاربر {user_id} از حالت بن خارج شد.")
    
    log_admin_action(message.from_user.id, "unban", user_id, "User unbanned")

async def set_user_level_handler(client: Client, message: Message):
    """Set user level."""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    if len(message.command) < 3:
        await message.reply("❌ استفاده صحیح:\n/setlevel <user_id> <level> [days]")
        await message.reply("""
سطح‌ها:
0️⃣ عادی (5 دانلود/روز، 100MB)
1️⃣ فعال (15 دانلود/روز، 500MB)
2️⃣ VIP (50 دانلود/روز، 2GB) - نیاز به days دارد
3️⃣ ادمین (نامحدود)
""")
        return
    
    try:
        user_id = int(message.command[1])
        level = int(message.command[2])
        days = int(message.command[3]) if len(message.command) > 3 else None
    except ValueError:
        await message.reply("❌ مقادیر باید عدد باشند!")
        return
    
    if level < 0 or level > 3:
        await message.reply("❌ سطح باید بین 0 تا 3 باشد!")
        return
    
    set_user_level(user_id, level, days)
    
    level_names = ["عادی", "فعال", "VIP", "ادمین"]
    days_text = f" برای {days} روز" if days and level == 2 else ""
    await message.reply(f"✅ سطح کاربر {user_id} به {level_names[level]}{days_text} تغییر یافت.")
    
    log_admin_action(message.from_user.id, "set_level", user_id, f"Level: {level}, Days: {days}")

async def broadcast_message(client: Client, message: Message):
    """Broadcast message to all users."""
    if message.from_user.id not in ADMIN_IDS:
        return
    
    if not message.reply_to_message:
        await message.reply("❌ لطفاً پیامی که می‌خواهید ارسال کنید را ریپلای کنید، سپس دستور /broadcast را بزنید.")
        return
    
    reply_msg = message.reply_to_message
    all_users = get_all_users()
    
    sent_count = 0
    failed_count = 0
    
    progress_msg = await message.reply(f"📢 در حال ارسال پیام به {len(all_users)} کاربر...\n0/{len(all_users)}")
    
    for user in all_users:
        try:
            await reply_msg.copy(user['user_id'])
            sent_count += 1
        except Exception as e:
            failed_count += 1
        
        # Update progress every 10 users
        if sent_count % 10 == 0:
            await progress_msg.edit_text(
                f"📢 در حال ارسال...\n{sent_count}/{len(all_users)}\n❌ خطا: {failed_count}"
            )
    
    await progress_msg.edit_text(
        f"✅ پیام همگانی ارسال شد!\n"
        f"📤 موفق: {sent_count}\n"
        f"❌ ناموفق: {failed_count}"
    )
    
    log_admin_action(message.from_user.id, "broadcast", details=f"Sent to {sent_count} users")
