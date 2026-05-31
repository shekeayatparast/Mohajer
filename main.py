"""Main bot entry point."""
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
import asyncio

from config.settings import BOT_TOKEN, API_ID, API_HASH, ADMIN_IDS
from database.db_manager import init_database, create_user_if_not_exists, is_user_banned, update_user_activity
from core.bot_handlers import (
    start_command, help_command, search_command, platform_command, 
    stats_command, handle_url_message, process_download
)
from admin.admin_handlers import (
    admin_panel, ban_user_handler, unban_user_handler, 
    set_user_level_handler, broadcast_message
)
from utils.helpers import logger, get_emoji

# Initialize Pyrogram client
app = Client(
    "music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    """Handle /start command."""
    await start_command(client, message)

@app.on_message(filters.command("help"))
async def help(client: Client, message: Message):
    """Handle /help command."""
    await help_command(client, message)

@app.on_message(filters.command("search"))
async def search(client: Client, message: Message):
    """Handle /search command."""
    await search_command(client, message)

@app.on_message(filters.command("platform"))
async def platform(client: Client, message: Message):
    """Handle /platform command."""
    await platform_command(client, message)

@app.on_message(filters.command("stats"))
async def stats(client: Client, message: Message):
    """Handle /stats command."""
    await stats_command(client, message)

@app.on_message(filters.command("admin") & filters.user(ADMIN_IDS))
async def admin_cmd(client: Client, message: Message):
    """Handle /admin command."""
    await admin_panel(client, message)

@app.on_message(filters.command("ban") & filters.user(ADMIN_IDS))
async def ban_cmd(client: Client, message: Message):
    """Handle /ban command."""
    await ban_user_handler(client, message)

@app.on_message(filters.command("unban") & filters.user(ADMIN_IDS))
async def unban_cmd(client: Client, message: Message):
    """Handle /unban command."""
    await unban_user_handler(client, message)

@app.on_message(filters.command("setlevel") & filters.user(ADMIN_IDS))
async def setlevel_cmd(client: Client, message: Message):
    """Handle /setlevel command."""
    await set_user_level_handler(client, message)

@app.on_message(filters.command("broadcast") & filters.user(ADMIN_IDS))
async def broadcast_cmd(client: Client, message: Message):
    """Handle /broadcast command."""
    await broadcast_message(client, message)

@app.on_message(filters.text | filters.caption)
async def handle_message(client: Client, message: Message):
    """Handle all text messages (check for URLs)."""
    user = message.from_user
    
    # Create user if not exists
    create_user_if_not_exists(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )
    
    # Check if banned
    banned, reason = is_user_banned(user.id)
    if banned and user.id not in ADMIN_IDS:
        await message.reply(f"🚫 شما بن شده‌اید!\nدلیل: {reason}")
        return
    
    # Update activity
    update_user_activity(user.id)
    
    # Handle URL messages
    await handle_url_message(client, message)

@app.on_callback_query()
async def callback_handler(client: Client, callback_query):
    """Handle callback queries from inline buttons."""
    data = callback_query.data
    user_id = callback_query.from_user.id
    
    # Check if admin callback
    if data.startswith("admin_"):
        if user_id not in ADMIN_IDS:
            await callback_query.answer("❌ دسترسی ندارید!", show_alert=True)
            return
        # Admin callbacks will be handled separately
        await callback_query.answer("در حال توسعه...", show_alert=False)
        return
    
    # Handle platform selection
    if data.startswith("set_platform_"):
        platform = data.replace("set_platform_", "")
        from database.db_manager import set_user_search_platform
        set_user_search_platform(user_id, platform)
        await callback_query.answer(f"✅ پلتفرم به {platform} تغییر یافت!")
        
        # Refresh platform menu
        from core.bot_handlers import platform_command
        await platform_command(client, callback_query.message)
        return
    
    # Handle download quality selection
    if data.startswith("download_"):
        parts = data.split("_", 3)
        if len(parts) >= 4:
            action = parts[1]
            platform = parts[2]
            quality = parts[3]
            
            # Reconstruct URL if it was split
            url = "_".join(parts[3:]) if len(parts) > 4 else parts[3]
            
            from platforms.soundcloud import SoundCloudDownloader
            from platforms.spotify import SpotifyDownloader
            from platforms.deezer import DeezerDownloader
            
            downloaders = {
                'soundcloud': SoundCloudDownloader(),
                'spotify': SpotifyDownloader(),
                'deezer': DeezerDownloader(),
            }
            
            downloader = downloaders.get(platform)
            if downloader:
                await callback_query.message.edit_text(f"{get_emoji('download')} در حال دانلود با کیفیت {quality}...")
                # Note: Full download handling needs URL parsing
                await callback_query.answer("در حال دانلود...", show_alert=False)
        return
    
    # Handle download all tracks
    if data.startswith("download_all_"):
        parts = data.split("_", 3)
        if len(parts) >= 3:
            platform = parts[2]
            url = "_".join(parts[3:]) if len(parts) > 3 else ""
            
            await callback_query.answer("در حال دانلود همه آهنگ‌ها...", show_alert=False)
            # Full implementation in bot_handlers
        return
    
    # Default answer
    await callback_query.answer()

def main():
    """Main entry point."""
    # Initialize database
    logger.info("Initializing database...")
    init_database()
    
    logger.info("Starting bot...")
    app.run()

if __name__ == "__main__":
    main()
