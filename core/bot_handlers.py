"""Core bot functionality and main handlers."""
from pyrogram import Client, filters, types
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
import asyncio
import os

from config.settings import BOT_TOKEN, API_ID, API_HASH, ADMIN_IDS, PLATFORMS, DEFAULT_SEARCH_PLATFORM
from database.db_manager import (
    init_database, create_user_if_not_exists, is_user_banned, 
    get_user_quota, increment_download_count, add_download_history,
    get_user_search_platform, set_user_search_platform, update_user_activity,
    get_bot_setting, log_admin_action
)
from utils.helpers import (
    extract_url, get_platform_from_url, get_emoji, format_size, 
    format_duration, sanitize_filename, get_temp_dir, clear_user_temp, logger
)
from platforms.soundcloud import SoundCloudDownloader
from platforms.spotify import SpotifyDownloader
from platforms.deezer import DeezerDownloader
from platforms.youtube import YouTubeDownloader
from platforms.instagram import InstagramDownloader
from platforms.tiktok import TikTokDownloader
from platforms.twitter import TwitterDownloader
from platforms.pinterest import PinterestDownloader

# Initialize downloaders
downloaders = {
    'soundcloud': SoundCloudDownloader(),
    'spotify': SpotifyDownloader(),
    'deezer': DeezerDownloader(),
    'youtube': YouTubeDownloader(),
    'instagram': InstagramDownloader(),
    'tiktok': TikTokDownloader(),
    'twitter': TwitterDownloader(),
    'pinterest': PinterestDownloader(),
}

# Active downloads per user (to prevent locking)
active_downloads = {}

async def check_user_access(user_id: int) -> tuple:
    """Check if user has access to the bot. Returns (allowed, reason)."""
    # Check ban
    banned, reason = is_user_banned(user_id)
    if banned:
        return False, f"🚫 شما بن شده‌اید!\nدلیل: {reason}"
    
    # Check quota
    quota = get_user_quota(user_id)
    if quota['daily_downloads'] >= 0 and quota.get('used_today', 0) >= quota['daily_downloads']:
        return False, f"⚠️ سهمیه دانلود روزانه شما تمام شده!\nمحدودیت: {quota['daily_downloads']} دانلود در روز"
    
    return True, None

async def start_command(client: Client, message: Message):
    """Handle /start command."""
    user = message.from_user
    
    # Create user in database
    create_user_if_not_exists(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )
    
    update_user_activity(user.id)
    
    welcome_text = f"""
{get_emoji('music')} **به ربات دانلودر موزیک خوش آمدید!** {get_emoji('music')}

👋 سلام {user.first_name} عزیز!

من می‌تونم از پلتفرم‌های زیر برات دانلود کنم:

{get_emoji('soundcloud')} SoundCloud
{get_emoji('spotify')} Spotify  
{get_emoji('deezer')} Deezer
{get_emoji('youtube')} YouTube
{get_emoji('instagram')} Instagram
{get_emoji('tiktok')} TikTok
{get_emoji('twitter')} Twitter/X
{get_emoji('pinterest')} Pinterest

**روش استفاده:**
1️⃣ لینک مورد نظرت رو بفرست
2️⃣ یا از دستور /search برای جستجو استفاده کن
3️⃣ کیفیت رو انتخاب کن و دانلود کن!

**دستورات:**
/start - شروع مجدد
/search - جستجو در پلتفرم فعلی
/platform - تغییر پلتفرم جستجو
/help - راهنما
/stats - آمار شخصی شما
/admin - پنل ادمین (فقط ادمین‌ها)

{get_emoji('info')} پلتفرم جستجوی فعلی شما: **{get_user_search_platform(user.id)}**
"""
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{get_emoji('search')} جستجو", callback_data="main_search"),
            InlineKeyboardButton(f"{get_emoji('settings')} پلتفرم", callback_data="main_platform"),
        ],
        [
            InlineKeyboardButton(f"{get_emoji('help')} راهنما", callback_data="main_help"),
            InlineKeyboardButton(f"{get_emoji('stats')} آمار من", callback_data="main_stats"),
        ],
    ])
    
    await message.reply(welcome_text, reply_markup=keyboard)

async def help_command(client: Client, message: Message):
    """Handle /help command."""
    help_text = f"""
{get_emoji('help')} **راهنمای کامل ربات** {get_emoji('help')}

**📥 دانلود مستقیم:**
کافیه لینک مورد نظرت رو از هر پلتفرمی (SoundCloud، Spotify، YouTube و...) بفرستی تا ربات شروع به دانلود کنه.

**🔍 جستجو:**
از دستور /search استفاده کن و نام آهنگ یا هنرمند رو بنویس.

**⚙️ تغییر پلتفرم:**
با دستور /platform می‌تونی پلتفرم پیش‌فرض جستجو رو تغییر بدی.

**🎵 کیفیت‌های موجود:**
- MP3: 128, 256, 320 kbps
- FLAC: کیفیت Lossless (بی‌نظیر!)

**📊 محدودیت‌ها:**
کاربران عادی: 5 دانلود در روز، حداکثر 100MB
کاربران فعال: 15 دانلود در روز، حداکثر 500MB
کاربران VIP: 50 دانلود در روز، حداکثر 2GB

**💡 نکات:**
- برای پلی‌لیست‌ها می‌تونی همه آهنگ‌ها رو یکجا دانلود کنی
- نیازی نیست دونه دونه آهنگ‌ها رو انتخاب کنی
- پروگرس بار کلی برای دانلود پلی‌لیست نمایش داده میشه

**❓ سوالی داری؟**
با ادمین تماس بگیر یا دوباره /help رو بزن.
"""
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(get_emoji('home') + " صفحه اصلی", callback_data="main_back")],
    ])
    
    await message.reply(help_text, reply_markup=keyboard)

async def search_command(client: Client, message: Message):
    """Handle /search command."""
    user = message.from_user
    
    # Check access
    allowed, reason = await check_user_access(user.id)
    if not allowed:
        await message.reply(reason)
        return
    
    if len(message.command) < 2:
        await message.reply(
            f"❌ لطفاً عبارت جستجو را وارد کنید:\n"
            f"/search <عبارت>\n\n"
            f"مثال: /search Coldplay Paradise"
        )
        return
    
    query = ' '.join(message.command[1:])
    platform = get_user_search_platform(user.id)
    
    downloader = downloaders.get(platform)
    if not downloader:
        await message.reply(f"❌ پلتفرم {platform} یافت نشد!")
        return
    
    # Send searching message
    search_msg = await message.reply(f"{get_emoji('search')} در حال جستجو در {platform}...")
    
    results = downloader.search(query, limit=10)
    
    if not results:
        await search_msg.edit_text(f"❌ نتیجه‌ای یافت نشد!\n\nپلتفرم: {platform}\nجستجو: {query}")
        return
    
    # Show results
    text = f"""
{get_emoji('search')} **نتایج جستجو در {platform}**

🔍 عبارت: `{query}`
📊 تعداد نتایج: {len(results)}

برای دانلود، شماره آهنگ مورد نظر را بفرستید.
"""
    
    buttons = []
    for i, result in enumerate(results[:10]):
        duration = format_duration(result.get('duration', 0))
        buttons.append([
            InlineKeyboardButton(
                f"{i+1}. {result['title'][:40]} ({duration})",
                callback_data=f"search_result_{platform}_{i}"
            )
        ])
    
    # Store results temporarily
    await search_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def platform_command(client: Client, message: Message):
    """Handle /platform command."""
    user = message.from_user
    
    current_platform = get_user_search_platform(user.id)
    
    text = f"""
{get_emoji('settings')} **انتخاب پلتفرم جستجو**

پلتفرم فعلی شما: **{current_platform}**

پلتفرم مورد نظر را انتخاب کنید:
"""
    
    platform_emojis = {
        'soundcloud': get_emoji('soundcloud'),
        'spotify': get_emoji('spotify'),
        'deezer': get_emoji('deezer'),
        'youtube': get_emoji('youtube'),
    }
    
    buttons = []
    for p in ['soundcloud', 'spotify', 'deezer', 'youtube']:
        emoji = platform_emojis.get(p, '•')
        is_current = "✅" if p == current_platform else ""
        buttons.append([
            InlineKeyboardButton(
                f"{emoji} {p.capitalize()} {is_current}",
                callback_data=f"set_platform_{p}"
            )
        ])
    
    await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons))

async def stats_command(client: Client, message: Message):
    """Handle /stats command."""
    user = message.from_user
    
    quota = get_user_quota(user.id)
    level_names = ["عادی", "فعال", "VIP", "ادمین"]
    
    text = f"""
{get_emoji('stats')} **آمار شخصی شما**

👤 نام: {user.first_name}
🆔 ID: `{user.id}`

**سطح دسترسی:** {level_names.get(quota.get('level', 0), 'عادی')}

**سهمیه امروز:**
📊 دانلود شده: {quota.get('used_today', 0)} از {quota['daily_downloads'] if quota['daily_downloads'] > 0 else '∞'}
💾 حجم مجاز: {quota['max_size_mb'] if quota['max_size_mb'] > 0 else '∞'} MB

**کل دانلودها:**
📥 تعداد: {quota.get('total_downloads', 0)}
💾 حجم: {quota.get('total_bytes', 0) / (1024*1024):.2f} MB
"""
    
    await message.reply(text)

async def handle_url_message(client: Client, message: Message):
    """Handle messages containing URLs."""
    user = message.from_user
    text = message.text or message.caption
    
    if not text:
        return
    
    url = extract_url(text)
    if not url:
        return
    
    # Check access
    allowed, reason = await check_user_access(user.id)
    if not allowed:
        await message.reply(reason)
        return
    
    # Check if user already has active downloads (prevent locking)
    if user.id in active_downloads and active_downloads[user.id] > 2:
        await message.reply(
            f"⚠️ شما در حال حاضر {active_downloads[user.id]} دانلود فعال دارید.\n"
            f"لطفاً صبر کنید تا دانلودهای قبلی تمام شوند."
        )
        return
    
    platform = get_platform_from_url(url)
    if not platform:
        await message.reply("❌ لینک نامعتبر است یا از پلتفرم پشتیبانی‌شده نیست.")
        return
    
    downloader = downloaders.get(platform)
    if not downloader:
        await message.reply(f"❌ پلتفرم {platform} هنوز پشتیبانی نمی‌شود.")
        return
    
    # Increment active downloads
    active_downloads[user.id] = active_downloads.get(user.id, 0) + 1
    
    try:
        # Extract info first
        info = downloader.extract_info(url)
        if not info:
            await message.reply("❌ خطا در استخراج اطلاعات لینک!")
            return
        
        if info['type'] in ['playlist', 'album']:
            await handle_playlist_download(client, message, downloader, info, url, user.id)
        else:
            await handle_single_download(client, message, downloader, info, url, user.id)
    
    finally:
        # Decrement active downloads
        active_downloads[user.id] = max(0, active_downloads.get(user.id, 1) - 1)

async def handle_single_download(client, message, downloader, info, url, user_id):
    """Handle single track/video download."""
    quality = '320'  # Default quality
    
    # Show quality selection
    qualities = downloader.get_quality_options()
    
    text = f"""
{get_emoji('music')} **اطلاعات محتوا**

🎵 عنوان: {info['title']}
👤 هنرمند: {info.get('artist', 'Unknown')}
⏱️ مدت: {format_duration(info.get('duration', 0))}
📊 پلتفرم: {info['platform']}

کیفیت مورد نظر را انتخاب کنید:
"""
    
    buttons = []
    for q in qualities:
        buttons.append([
            InlineKeyboardButton(
                q['label'],
                callback_data=f"download_{downloader.name}_{quality}_{url}"
            )
        ])
    
    await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons))

async def handle_playlist_download(client, message, downloader, info, url, user_id):
    """Handle playlist/album download."""
    tracks = info.get('tracks', [])
    if not tracks:
        await message.reply("❌ پلی‌لیست خالی است!")
        return
    
    text = f"""
{get_emoji('playlist')} **پلی‌لیست/آلبوم شناسایی شد**

📀 عنوان: {info['title']}
🎵 تعداد آهنگ: {len(tracks)}
📊 پلتفرم: {info['platform']}

**انتخاب کنید:**
- دانلود همه آهنگ‌ها
- انتخاب تکی آهنگ‌ها
"""
    
    # First 5 tracks preview
    preview = "\n".join([
        f"{i+1}. {t['title'][:35]} ({format_duration(t.get('duration', 0))})"
        for i, t in enumerate(tracks[:5])
    ])
    if len(tracks) > 5:
        preview += f"\n... و {len(tracks) - 5} آهنگ دیگر"
    
    text += f"\n\n**آهنگ‌ها:**\n{preview}"
    
    buttons = [
        [
            InlineKeyboardButton(
                f"{get_emoji('download')} دانلود همه ({len(tracks)} آهنگ)",
                callback_data=f"download_all_{downloader.name}_{url}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{get_emoji('playlist')} انتخاب تکی",
                callback_data=f"select_tracks_{downloader.name}_{url}"
            ),
        ],
    ]
    
    await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons))

async def process_download(client, message, downloader, url, quality, user_id, track_indices=None):
    """Process actual download."""
    workdir = get_temp_dir(user_id)
    
    progress_msg = await message.reply(f"{get_emoji('download')} در حال آماده‌سازی دانلود...")
    
    try:
        if track_indices:
            # Download specific tracks from playlist
            result = downloader.download_playlist(
                url, quality, workdir, track_indices,
                callback=lambda current, total, title: asyncio.create_task(
                    progress_msg.edit_text(
                        f"{get_emoji('download')} در حال دانلود...\n"
                        f"📊 {current}/{total}\n"
                        f"🎵 {title[:30]}"
                    )
                )
            )
        else:
            # Single download
            result = downloader.download_track(url, quality, workdir)
        
        if not result.get('success'):
            await progress_msg.edit_text(f"❌ خطا در دانلود:\n{result.get('error', 'Unknown error')}")
            return
        
        # Send file(s)
        if 'files' in result:
            # Multiple files from playlist
            await progress_msg.edit_text(
                f"✅ دانلود کامل شد!\n"
                f"📊 موفق: {result['downloaded']}/{result['total_tracks']}\n"
                f"💾 حجم کل: {format_size(result['total_size'])}"
            )
            
            for file_info in result['files']:
                try:
                    await client.send_audio(
                        chat_id=message.chat.id,
                        audio=file_info['path'],
                        caption=f"🎵 {file_info['title']}\n{get_emoji('music')} دانلود شده با Music Bot",
                        thumb=None
                    )
                    # Record in history
                    add_download_history(
                        user_id, downloader.name, url, 
                        file_info['title'], file_info['size'], quality
                    )
                    increment_download_count(user_id, file_info['size'], downloader.name)
                except Exception as e:
                    logger.error(f"Error sending file: {e}")
                
                # Clean up
                try:
                    os.remove(file_info['path'])
                except:
                    pass
        else:
            # Single file
            file_path = result['file_path']
            file_size = result['file_size']
            
            # Check file size limit
            quota = get_user_quota(user_id)
            max_size = quota['max_size_mb'] * 1024 * 1024
            if max_size > 0 and file_size > max_size:
                await progress_msg.edit_text(
                    f"❌ حجم فایل ({format_size(file_size)}) از محدودیت شما ({quota['max_size_mb']}MB) بیشتر است!"
                )
                return
            
            await progress_msg.edit_text(f"{get_emoji('download')} در حال ارسال فایل...")
            
            # Determine file type
            is_audio = file_path.endswith('.mp3')
            
            try:
                if is_audio:
                    await client.send_audio(
                        chat_id=message.chat.id,
                        audio=file_path,
                        caption=f"🎵 {result['title']}\n🎼 کیفیت: {quality}\n{get_emoji('music')} دانلود شده با Music Bot",
                        thumb=None,
                        duration=0
                    )
                else:
                    await client.send_video(
                        chat_id=message.chat.id,
                        video=file_path,
                        caption=f"🎬 {result['title']}\n🎼 کیفیت: {quality}\n{get_emoji('music')} دانلود شده با Music Bot",
                        thumb=None
                    )
                
                # Record in history
                add_download_history(
                    user_id, downloader.name, url,
                    result['title'], file_size, quality
                )
                increment_download_count(user_id, file_size, downloader.name)
                
            except Exception as e:
                logger.error(f"Error sending file: {e}")
                await progress_msg.edit_text(f"❌ خطا در ارسال فایل:\n{str(e)}")
            
            # Clean up
            try:
                os.remove(file_path)
            except:
                pass
    
    except Exception as e:
        logger.error(f"Download error: {e}")
        await progress_msg.edit_text(f"❌ خطا در دانلود:\n{str(e)}")
    
    finally:
        clear_user_temp(user_id)
