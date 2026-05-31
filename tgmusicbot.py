# Telegram Downloader Bot: Enhanced Version - Part 1
# SoundCloud, Pinterest, Instagram, YouTube Shorts, TikTok and Twitter

import os
import re
import shutil
import sqlite3
import tempfile
import requests
import yt_dlp
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot import apihelper
import threading
from flask import Flask
import time
import logging
from datetime import datetime
import random
import json
import queue
from contextlib import contextmanager

# ===== Config =====
BOT_TOKEN = "8382981392:AAEdQptMng0Zu2keWRMrfylq6wepvmULCbI"
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

# Admin Configuration - Add your Telegram ID here
ADMIN_IDS = [123456789]  # Replace with your actual Telegram user ID(s)
CHANNEL_USERNAME = "@TheDarkestNest"
DB_PATH = "sc_bot.db"
TELEGRAM_UPLOAD_LIMIT = 50 * 1024 * 1024
FORCE_MP3 = False
COMPANION_ID = "@Theirodentv"
PORT = int(os.environ.get('PORT', 5000))
COOKIES_PATH = "cookies.txt"

# Download Limits by User Level
USER_LIMITS = {
    0: {"daily_downloads": 5, "daily_bytes": 100 * 1024 * 1024},      # Regular
    1: {"daily_downloads": 15, "daily_bytes": 500 * 1024 * 1024},     # Active
    2: {"daily_downloads": 50, "daily_bytes": 2 * 1024 * 1024 * 1024}, # VIP
    3: {"daily_downloads": -1, "daily_bytes": -1}                      # Admin (unlimited)
}

# Bot Operation Mode
OPERATION_MODE = "normal"  # normal, busy, stress, emergency

# Check if cookies file exists
COOKIES_AVAILABLE = os.path.exists(COOKIES_PATH)
print(f"Cookies file available: {COOKIES_AVAILABLE}")

# Proxy Configuration
MANUAL_PROXIES = [
    "http://20.205.61.143:80",
    "http://20.205.61.142:80",
    "http://20.205.61.141:80",
    "http://104.248.9.22:8080",
    "http://167.71.5.10:8080",
]

ENABLE_PROXY_FOR_SOUNDCLOUD = True
ENABLE_PROXY_ROTATION = True

# Optimized settings for Replit
os.environ['PYTHONUNBUFFERED'] = '1'

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
BOT_USERNAME = bot.get_me().username

apihelper.SESSION_TIMEOUT = 60
apihelper.READ_TIMEOUT = 60
apihelper.CONNECT_TIMEOUT = 60

# ===== Connection Pool Implementation =====
class ConnectionPool:
    """Thread-safe connection pool for SQLite"""
    
    def __init__(self, db_path, max_connections=10):
        self.db_path = db_path
        self.max_connections = max_connections
        self.pool = queue.Queue(maxsize=max_connections)
        self.lock = threading.Lock()
        self.created_connections = 0
        
        # Pre-create some connections
        for _ in range(min(3, max_connections)):
            self._create_connection()
    
    def _create_connection(self):
        """Create a new connection and add it to the pool"""
        if self.created_connections >= self.max_connections:
            return None
            
        try:
            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0,
                isolation_level=None  # Autocommit mode
            )
            # Enable WAL mode for better concurrent access
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=10000")
            conn.execute("PRAGMA temp_store=MEMORY")
            
            self.pool.put(conn)
            self.created_connections += 1
            return conn
        except Exception as e:
            print(f"Error creating connection: {e}")
            return None
    
    @contextmanager
    def get_connection(self):
        """Get a connection from the pool"""
        conn = None
        try:
            # Try to get a connection from the pool
            try:
                conn = self.pool.get(timeout=5.0)
            except queue.Empty:
                # Pool is empty, try to create a new connection
                conn = self._create_connection()
                if conn is None:
                    # Still couldn't get a connection, wait and try again
                    time.sleep(0.1)
                    conn = self.pool.get(timeout=10.0)
            
            yield conn
        except Exception as e:
            print(f"Error getting connection: {e}")
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            raise
        finally:
            # Return the connection to the pool
            if conn:
                try:
                    self.pool.put(conn, timeout=1.0)
                except queue.Full:
                    # Pool is full, close this connection
                    try:
                        conn.close()
                        self.created_connections -= 1
                    except:
                        pass

# Initialize connection pool
db_pool = ConnectionPool(DB_PATH, max_connections=20)

# ===== Enhanced Progress Bar Class =====
class ProgressBar:
    def __init__(self, chat_id, message_id, total_size=0):
        self.chat_id = chat_id
        self.message_id = message_id
        self.total_size = total_size
        self.last_update_time = 0
        self.last_percentage = -1
        self.update_interval = 3.0  # Update every 3 seconds minimum
        self.percentage_threshold = 5  # Update only every 5% minimum
        
    def create_progress_bar(self, percentage, width=20):
        """Create a visual progress bar"""
        filled = int(width * percentage / 100)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}]"
    
    def format_progress(self, done_bytes, total_bytes):
        """Format progress with visual bar, percentage and file sizes"""
        if total_bytes > 0:
            percentage = int(done_bytes * 100 / total_bytes)
        else:
            percentage = 0
            
        progress_bar = self.create_progress_bar(percentage)
        done_str = human_size(done_bytes)
        total_str = human_size(total_bytes)
        
        return f"{progress_bar} {percentage}% ({done_str}/{total_str})"
    
    def should_update(self, current_percentage):
        """Check if we should update the progress message"""
        current_time = time.time()
        
        # Update if it's been long enough OR significant percentage change
        time_passed = current_time - self.last_update_time
        percentage_change = abs(current_percentage - self.last_percentage)
        
        return (time_passed >= self.update_interval or 
                percentage_change >= self.percentage_threshold or
                current_percentage == 100)
    
    def update(self, done_bytes, total_bytes):
        """Update progress with rate limiting"""
        if total_bytes > 0:
            current_percentage = int(done_bytes * 100 / total_bytes)
        else:
            current_percentage = 0
            
        if self.should_update(current_percentage):
            progress_text = self.format_progress(done_bytes, total_bytes)
            
            try:
                safe_edit_message(progress_text, self.chat_id, self.message_id)
                self.last_update_time = time.time()
                self.last_percentage = current_percentage
                return True
            except Exception as e:
                print(f"Error updating progress: {e}")
                return False
        
        return False

# ===== Enhanced i18n =====
LANGS = {"fa", "en"}
T = {
    "fa": {
        "start": "🌐 به ربات دانلودر چند پلتفرمی خوش آمدید!\n\nاین ربات قابلیت دانلود از پلتفرم‌های مختلف را با بهترین کیفیت ممکن فراهم می‌کند. برای استفاده از ربات، لطفاً عضو کانال شوید.",
        "fa_btn": "فارسی 🇮🇷",
        "en_btn": "English 🇬🇧",
        "lang_set": "زبان تنظیم شد: {lang}",
        "send_link": "لینک SoundCloud، Pinterest، Instagram، YouTube، TikTok یا Twitter را بفرست، یا از /search برای جستجو استفاده کن.",
        "quality_prompt": "کیفیت صوتی SoundCloud را انتخاب کن:",
        "quality_high": "کیفیت بالا 🎧",
        "quality_low": "کیفیت سبک 🔉",
        "quality_set": "کیفیت تنظیم شد: {q}",
        "downloading": "در حال دانلود... ⏳",
        "progress": "در حال دانلود... {pct}% ({done}/{total})",
        "invalid_link": "لطفاً لینک معتبر بده یا از /search استفاده کن.",
        "error": "❗️خطا: {err}",
        "stats_title": "آمار دانلود",
        "stats_body": "کاربر: {user_count} مورد، {user_bytes}\nکل ربات: {total_count} مورد، {total_bytes}",
        "search_prompt": "برای جستجو بنویس: /search کلمه‌کلیدی",
        "searching": "در حال جستجو در SoundCloud... 🔎",
        "searching_with_count": "در حال جستجو در SoundCloud... 🔎 ({count} نتیجه یافت شد)",
        "search_results_found": "✅ {count} نتیجه پیدا شد",
        "no_results_found": "نتیجه‌ای پیدا نشد",
        "search_complete": "جستجو کامل شد - {count} نتیجه",
        "processing_results": "در حال پردازش نتایج...",
        "loading_results": "در حال بارگذاری نتایج...",
        "pick_from_results": "از نتایج زیر انتخاب کنید:",
        "previous_page": "⬅️ قبلی",
        "next_page": "بعدی ➡️",
        "page_number": "📄 {page}/{total_pages}",
        "playlist_song_selection": "🎵 انتخاب آهنگ از پلی‌لیست:",
        "downloading_playlist": "در حال دانلود پلی‌لیست...",
        "processing_playlist": "در حال پردازش آهنگ‌های پلی‌لیست...",
        "playlist_detected": "پلی‌لیست شناسایی شد. {count} آهنگ یافت شد",
        "select_song": "انتخاب آهنگ",
        "song_number": "آهنگ {num}",
        "downloading_single": "در حال دانلود تک آهنگ...",
        "preview": "پیش‌نمایش",
        "video_preview": "پیش‌نمایش ویدیو",
        "tiktok_preview": "پیش‌نمایش TikTok",
        "instagram_preview": "پیش‌نمایش اینستاگرام",
        "youtube_preview": "پیش‌نمایش یوتیوب",
        "pinterest_preview": "پیش‌نمایش پینترست",
        "twitter_preview": "پیش‌نمایش توییتر",
        "search_none": "نتیجه‌ای پیدا نشد.",
        "search_pick": "یکی را انتخاب کن:",
        "playlist_note": "پلی‌لیست شناسایی شد. در حال ارسال ترک‌ها... 📂",
        "cover_sent": "اینم از کاور🖼️",
        "must_join": "برای استفاده از ربات، لطفاً عضو کانال {chan} شو.",
        "join_btn": "عضویت در کانال",
        "signature": "دانلود شده با 💝",
        "features_header": "🌟 قابلیت‌های ربات:",
        "features_lines": [
            "🎵 <b>SoundCloud:</b> دانلود ترک تکی و پلی‌لیست، جستجو با /search، انتخاب کیفیت صوتی (بالا/سبک)، ارسال کاور و اطلاعات آهنگ",
            "📷 <b>Pinterest:</b> دانلود عکس و ویدیو با کپشن همراه با بالاترین کیفیت ممکن",
            "📸 <b>Instagram:</b> دانلود عکس، ویدیو و ریلز با بالاترین کیفیت و کپشن کامل",
            "🎬 <b>YouTube:</b> دانلود ویدیوهای عادی و شورتس با انتخاب کیفیت و گزینه صرفاً صدا",
            "🎵 <b>TikTok:</b> دانلود ویدیوهای تیک تاک با واترمارک حذف شده و اطلاعات کامل",
            "🐦 <b>Twitter (X):</b> دانلود توییت‌ها، ویدیوها و تصاویر با بالاترین کیفیت و اطلاعات کامل",
            "⏳ <b>نمایش پیشرفت:</b> نمایش درصد و حجم در حال دانلود به صورت زنده",
            "📊 <b>آمار:</b> آمار تعداد و حجم دانلود کاربر و کل ربات با /stats",
            "🔄 <b>پشتیبانی از پروکسی:</b> استفاده هوشمند از پروکسی برای دور زدن محدودیت‌های جغرافیایی",
            "✨ <b>خوشحال میشم که عضو خونواده ی ما بشی!</b>"
        ],
        "companion_label": "🤝 همراه شما: {id}",
        "close_menu": "❌ بستن منو",
        "content_link": "لینک محتوا",
        "top_users_all_time": "برترین کاربران (همه زمان)",
        "top_platforms_all_time": "پلتفرم‌های برتر (همه زمان)",
        "top_users_daily": "برترین کاربران (امروز)",
        "top_platforms_daily": "پلتفرم‌های برتر (امروز)",
        "top_users_weekly": "برترین کاربران (هفته)",
        "top_platforms_weekly": "پلتفرم‌های برتر (هفته)",
        "view_profile": "مشاهده پروفایل",
        "back_to_stats": "بازگشت به آمار",
        "no_data": "داده‌ای برای نمایش وجود ندارد",
        "rank": "رتبه",
        "user": "کاربر",
        "downloads": "دانلودها",
        "volume": "حجم",
        "platform": "پلتفرم",
        "most_used": "پراستفاده‌ترین",
        "your_stats": "آمار شما",
        "total_processed": "کل پردازش‌ها",
        "uptime": "آپتایم",
        "no_user_data": "شما هنوز فعالیتی نداشته‌اید",
        "top_user_stats": "👑 برترین کاربران (همه زمان)",
        "daily_top_user_stats": "📅 برترین کاربران (امروز)",
        "weekly_top_user_stats": "📆 برترین کاربران (هفته)",
        "top_platform_stats": "🏆 پلتفرم‌های برتر (همه زمان)",
        "daily_top_platform_stats": "📊 پلتفرم‌های برتر (امروز)",
        "weekly_top_platform_stats": "📈 پلتفرم‌های برتر (هفته)",
        "your_daily_stats": "آمار روزانه شما",
        "your_weekly_stats": "آمار هفتگی شما",
        "choose_category": "دسته مورد نظر را انتخاب کنید:",
        "global_stats": "آمار کل ربات",
        "proxy_retry": "🔄 تلاش با پروکسی دیگر...",
        "geo_restriction_error": "⚠️ محدودیت جغرافیایی detected! در حال تلاش با پروکسی...",
        "updating_proxies": "🔄 در حال به‌روزرسانی لیست پروکسی‌ها...",
        "proxy_found": "✅ {count} پروکسی کارآمد یافت شد",
        # New translations for YouTube quality selection
        "youtube_quality_prompt": "🎬 کیفیت ویدیو را انتخاب کنید:",
        "youtube_audio_only": "فقط صدا",
        "youtube_video_quality": "🎬 {quality}",
        "youtube_size_info": "{size} مگابایت",
        "youtube_processing": "در حال پردازش کیفیت‌های ممکن...",
        "youtube_no_qualities": "هیچ کیفیت مناسب زیر ۵۰ مگابایت یافت نشد",
        "youtube_selected_quality": "✅ کیفیت انتخاب شد: {quality}",
        "youtube_downloading": "در حال دانلود با کیفیت {quality}...",
        # YouTube Shorts specific
        "youtube_shorts_detected": "🎬 YouTube Short detected!",
        "youtube_shorts_prompt": "Choose download option:",
        "youtube_shorts_video": "📹 Video",
        "youtube_shorts_audio": "🎵 Audio only",
        "youtube_shorts_downloading": "Downloading YouTube Short...",
    },
    "en": {
        "start": "🌐 Welcome to the Multi-Platform Downloader Bot!\n\nThis bot provides downloading capabilities from various platforms with the best possible quality. Please join the channel to use the bot.",
        "fa_btn": "فارسی 🇮🇷",
        "en_btn": "English 🇬🇧",
        "lang_set": "Language set: {lang}",
        "send_link": "Send a SoundCloud, Pinterest, Instagram, YouTube, TikTok or Twitter link, or use /search.",
        "quality_prompt": "Choose SoundCloud audio quality:",
        "quality_high": "High quality 🎧",
        "quality_low": "Light quality 🔉",
        "quality_set": "Quality set: {q}",
        "downloading": "Downloading... ⏳",
        "progress": "Downloading... {pct}% ({done}/{total})",
        "invalid_link": "Please send a valid link or use /search.",
        "error": "❗️Error: {err}",
        "stats_title": "Download stats",
        "stats_body": "You: {user_count} items, {user_bytes}\nGlobal: {total_count} items, {total_bytes}",
        "search_prompt": "To search, type: /search keyword",
        "searching": "Searching SoundCloud... 🔎",
        "searching_with_count": "Searching SoundCloud... 🔎 ({count} results found)",
        "search_results_found": "✅ {count} results found",
        "no_results_found": "No results found",
        "search_complete": "Search complete - {count} results",
        "processing_results": "Processing results...",
        "loading_results": "Loading results...",
        "pick_from_results": "Pick from the results below:",
        "previous_page": "⬅️ Previous",
        "next_page": "Next ➡️",
        "page_number": "📄 {page}/{total_pages}",
        "playlist_song_selection": "🎵 Select song from playlist:",
        "downloading_playlist": "Downloading playlist...",
        "processing_playlist": "Processing playlist songs...",
        "playlist_detected": "Playlist detected. {count} songs found",
        "select_song": "Select song",
        "song_number": "Song {num}",
        "downloading_single": "Downloading single track...",
        "preview": "Preview",
        "video_preview": "Video Preview",
        "tiktok_preview": "TikTok Preview",
        "instagram_preview": "Instagram Preview",
        "youtube_preview": "YouTube Preview",
        "pinterest_preview": "Pinterest Preview",
        "twitter_preview": "Twitter Preview",
        "search_none": "No results found.",
        "search_pick": "Pick one:",
        "playlist_note": "Playlist detected. Sending tracks... 📂",
        "cover_sent": "Cover art sent 🖼️",
        "must_join": "To use the bot, please join {chan}.",
        "join_btn": "Join channel",
        "signature": "Downloaded With 💝",
        "features_header": "🌟 Bot Features:",
        "features_lines": [
            "🎵 <b>SoundCloud:</b> Download single tracks and playlists, search via /search, choose audio quality (high/light), send cover and metadata",
            "📷 <b>Pinterest:</b> Download images and videos with captions and in highest quality",
            "📸 <b>Instagram:</b> Download photos, videos and reels with highest quality and full captions",
            "🎬 <b>YouTube:</b> Download regular videos and Shorts with quality selection and audio-only option",
            "🎵 <b>TikTok:</b> Download TikTok videos without watermark and complete information",
            "🐦 <b>Twitter (X):</b> Download tweets, videos and images with highest quality and complete information",
            "⏳ <b>Progress Display:</b> Live download percentage and size display",
            "📊 <b>Statistics:</b> User and global counts and sizes via /stats",
            "🔄 <b>Proxy Support:</b> Smart proxy usage to bypass geo-restrictions",
            "✨ <b>I'll Be Happy To Have You In Our Family!</b>"
        ],
        "companion_label": "🤝 Your companion: {id}",
        "close_menu": "❌ Close Menu",
        "content_link": "Content Link",
        "top_users_all_time": "Top Users (All Time)",
        "top_platforms_all_time": "Top Platforms (All Time)",
        "top_users_daily": "Top Users (Daily)",
        "top_platforms_daily": "Top Platforms (Daily)",
        "top_users_weekly": "Top Users (Weekly)",
        "top_platforms_weekly": "Top Platforms (Weekly)",
        "view_profile": "View Profile",
        "back_to_stats": "Back to Stats",
        "no_data": "No data available",
        "rank": "Rank",
        "user": "User",
        "downloads": "Downloads",
        "volume": "Volume",
        "platform": "Platform",
        "most_used": "Most Used",
        "your_stats": "Your Stats",
        "total_processed": "Total Processed",
        "uptime": "Uptime",
        "no_user_data": "You have no activity yet",
        "top_user_stats": "👑 Top Users (All Time)",
        "daily_top_user_stats": "📅 Top Users (Daily)",
        "weekly_top_user_stats": "📆 Top Users (Weekly)",
        "top_platform_stats": "🏆 Top Platforms (All Time)",
        "daily_top_platform_stats": "📊 Top Platforms (Daily)",
        "weekly_top_platform_stats": "📈 Top Platforms (Weekly)",
        "your_daily_stats": "Your Daily Stats",
        "your_weekly_stats": "Your Weekly Stats",
        "choose_category": "Choose a category:",
        "global_stats": "Global Bot Stats",
        "proxy_retry": "🔄 Retrying with another proxy...",
        "geo_restriction_error": "⚠️ Geo-restriction detected! Trying with proxy...",
        "updating_proxies": "🔄 Updating proxy list...",
        "proxy_found": "✅ {count} working proxies found",
        # New translations for YouTube quality selection
        "youtube_quality_prompt": "🎬 Choose video quality:",
        "youtube_audio_only": "Audio Only",
        "youtube_video_quality": "🎬 {quality}",
        "youtube_size_info": "{size} MB",
        "youtube_processing": "Processing available qualities...",
        "youtube_no_qualities": "No suitable qualities under 50MB found",
        "youtube_selected_quality": "✅ Quality selected: {quality}",
        "youtube_downloading": "Downloading with {quality} quality...",
        # YouTube Shorts specific
        "youtube_shorts_detected": "🎬 YouTube Short detected!",
        "youtube_shorts_prompt": "Choose download option:",
        "youtube_shorts_video": "📹 Video",
        "youtube_shorts_audio": "🎵 Audio only",
        "youtube_shorts_downloading": "Downloading YouTube Short...",
    },
}

def tr(chat_id, key, **kwargs):
    lang = get_user_lang(chat_id) or "en"
    text = T.get(lang, T["en"]).get(key, key)
    return text.format(**kwargs) if kwargs else text

# ===== Enhanced Proxy Management =====
class ProxyManager:
    def __init__(self):
        self.working_proxies = []
        self.failed_proxies = []
        self.last_update = 0
        self.manual_proxies = MANUAL_PROXIES.copy()
        
    def fetch_free_proxies(self) -> list:
        """Fetch free proxies from multiple sources"""
        proxies = []
        
        try:
            # Source 1: ProxyScrape US proxies
            url = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&country=us&proxy_format=protocolipport&format=text&timeout=619"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                proxy_text = response.text.strip()
                proxy_lines = proxy_text.split('\n')
                
                for line in proxy_lines:
                    line = line.strip()
                    if line and ':' in line:
                        if not line.startswith(('http://', 'https://', 'socks5://')):
                            proxies.append(f"http://{line}")
                        else:
                            proxies.append(line)
                
                print(f"Fetched {len(proxies)} US proxies from ProxyScrape")
            else:
                print(f"Failed to fetch US proxies: HTTP {response.status_code}")
                
        except Exception as e:
            print(f"Error fetching US proxies: {e}")
        
        try:
            # Source 2: ProxyScrape all countries (backup)
            url = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&timeout=619"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                proxy_text = response.text.strip()
                proxy_lines = proxy_text.split('\n')
                
                for line in proxy_lines[:1000]:  # Limit to first 1000
                    line = line.strip()
                    if line and ':' in line:
                        if not line.startswith(('http://', 'https://', 'socks5://')):
                            proxies.append(f"http://{line}")
                        else:
                            proxies.append(line)
                
                print(f"Fetched additional {len(proxy_lines[:1000])} backup proxies")
        except Exception as e:
            print(f"Error fetching backup proxies: {e}")
        
        # Remove duplicates and shuffle
        proxies = list(set(proxies))
        random.shuffle(proxies)
        
        print(f"Total unique proxies: {len(proxies)}")
        return proxies
    
    def test_proxy(self, proxy_url: str, timeout: int = 5) -> bool:
        """Test if a proxy is working for SoundCloud"""
        try:
            proxies = {'http': proxy_url, 'https': proxy_url}
            
            # First test basic connectivity
            response = requests.get('http://httpbin.org/ip', proxies=proxies, timeout=timeout)
            if response.status_code != 200:
                return False
            
            # Test if proxy can access SoundCloud (critical for geo-restriction)
            try:
                soundcloud_test = requests.get('https://soundcloud.com/', proxies=proxies, timeout=timeout)
                if soundcloud_test.status_code == 200:
                    return True
            except:
                # If SoundCloud test fails but basic test passed, still consider it working
                return True
                
        except:
            return False
    
    def get_working_proxy(self, max_retries: int = 10) -> str:
        """Get a working proxy, testing multiple if needed"""
        
        # Update proxy list if it's old or empty
        if time.time() - self.last_update > 1800 or not self.working_proxies:  # Update every 30 minutes
            self.update_proxy_list()
        
        # Try manual/proven proxies first
        for _ in range(max_retries):
            if self.manual_proxies:
                proxy = random.choice(self.manual_proxies)
                if self.test_proxy(proxy, timeout=3):
                    return proxy
        
        # Try working proxies
        for _ in range(max_retries):
            if self.working_proxies:
                proxy = random.choice(self.working_proxies)
                if self.test_proxy(proxy, timeout=3):
                    return proxy
                else:
                    self.working_proxies.remove(proxy)
                    self.failed_proxies.append(proxy)
        
        # Try failed proxies (they might work now)
        random.shuffle(self.failed_proxies)
        for proxy in self.failed_proxies[:max_retries]:
            if self.test_proxy(proxy, timeout=3):
                self.failed_proxies.remove(proxy)
                self.working_proxies.append(proxy)
                return proxy
        
        # Fetch and test new proxies if all failed
        print("All proxies failed, fetching fresh ones...")
        new_proxies = self.fetch_free_proxies()
        for proxy in new_proxies[:max_retries * 2]:  # Test more proxies
            if self.test_proxy(proxy, timeout=3):
                self.working_proxies.append(proxy)
                return proxy
        
        return None
    
    def get_alternative_proxy_format(self, proxy_url: str) -> str:
        """Try to convert HTTP proxy to SOCKS5 format for better compatibility"""
        if proxy_url.startswith('http://'):
            # Extract IP and port
            parts = proxy_url.replace('http://', '').split(':')
            if len(parts) == 2:
                ip, port = parts
                # Try SOCKS5 format (some services support this)
                return f"socks5://{ip}:{port}"
        return proxy_url
    
    def update_proxy_list(self):
        """Update proxy list with fresh proxies"""
        print("Updating proxy list...")
        
        # Clear old working proxies
        self.working_proxies = []
        
        # Start with manual proxies
        all_proxies = self.manual_proxies.copy()
        
        # Add free proxies
        free_proxies = self.fetch_free_proxies()
        all_proxies.extend(free_proxies)
        
        print(f"Fetched {len(free_proxies)} proxies from ProxyScrape")
        
        # Test more proxies to find working ones
        tested_count = 0
        working_count = 0
        for proxy in all_proxies:
            if tested_count >= 100:  # Limit testing to first 100 proxies
                break
            if working_count >= 25:  # Keep only 25 working proxies
                break
                
            tested_count += 1
            if self.test_proxy(proxy, timeout=3):
                self.working_proxies.append(proxy)
                working_count += 1
                print(f"Working proxy #{working_count}: {proxy}")
        
        self.last_update = time.time()
        print(f"Proxy list updated: {len(self.working_proxies)} working proxies (tested {tested_count})")
        
        return len(self.working_proxies)
    
    def get_proxy_stats(self) -> dict:
        """Get statistics about proxy performance"""
        return {
            "working_proxies": len(self.working_proxies),
            "failed_proxies": len(self.failed_proxies),
            "manual_proxies": len(self.manual_proxies),
            "last_update": self.last_update
        }

# Initialize proxy manager
proxy_manager = ProxyManager()# Telegram Downloader Bot: Enhanced Version - Part 2
# Database Functions and Helper Classes with Connection Pooling

# ===== DB Functions with Connection Pooling =====
def db_init():
    """Initialize database with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        # Existing tables
        c.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, lang TEXT, quality TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS stats (chat_id INTEGER, count INTEGER, bytes INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS totals (id INTEGER PRIMARY KEY, count INTEGER, bytes INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS search_cache (chat_id INTEGER, idx INTEGER, url TEXT, title TEXT, artist TEXT, duration INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS playlist_cache (chat_id INTEGER, idx INTEGER, url TEXT, title TEXT, artist TEXT, duration INTEGER)")

        # New tables for advanced statistics
        c.execute("""
            CREATE TABLE IF NOT EXISTS detailed_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                platform TEXT,
                file_type TEXT,
                file_size INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES users (chat_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS uptime_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                total_downloads INTEGER DEFAULT 0,
                total_processed INTEGER DEFAULT 0
            )
        """)

        # New table for YouTube quality cache
        c.execute("""
            CREATE TABLE IF NOT EXISTS youtube_quality_cache (
                chat_id INTEGER PRIMARY KEY,
                url TEXT,
                qualities TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # New table for YouTube Shorts cache
        c.execute("""
            CREATE TABLE IF NOT EXISTS youtube_shorts_cache (
                chat_id INTEGER PRIMARY KEY,
                url TEXT,
                is_short BOOLEAN,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Admin panel tables
        c.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                chat_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                banned_by INTEGER
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS user_levels (
                chat_id INTEGER PRIMARY KEY,
                level INTEGER DEFAULT 0,
                vip_until DATETIME,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_text TEXT,
                sent_count INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending'
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS proxy_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proxy_url TEXT,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                last_tested DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                target_user INTEGER,
                details TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS daily_user_stats (
                chat_id INTEGER,
                date DATE DEFAULT CURRENT_DATE,
                download_count INTEGER DEFAULT 0,
                bytes_used INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, date)
            )
        """)

        # Create indexes
        c.execute("CREATE INDEX IF NOT EXISTS idx_detailed_stats_chat_id ON detailed_stats(chat_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_detailed_stats_timestamp ON detailed_stats(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_detailed_stats_platform ON detailed_stats(platform)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_banned_users_chat_id ON banned_users(chat_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_user_levels_chat_id ON user_levels(chat_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_daily_user_stats_date ON daily_user_stats(date)")

        conn.commit()

def get_user_lang(chat_id):
    """Get user language with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT lang FROM users WHERE chat_id=?", (chat_id,))
        row = c.fetchone()
        return row[0] if row and row[0] in LANGS else None

def set_user_lang(chat_id, lang):
    """Set user language with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO users (chat_id, lang, quality) VALUES (?, ?, COALESCE((SELECT quality FROM users WHERE chat_id=?),'high'))", (chat_id, lang, chat_id))
        conn.commit()

def get_user_quality(chat_id):
    """Get user quality with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT quality FROM users WHERE chat_id=?", (chat_id,))
        row = c.fetchone()
        return row[0] if row and row[0] in ("high", "low") else "high"

def set_user_quality(chat_id, q):
    """Set user quality with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO users (chat_id, lang, quality) VALUES (?, COALESCE((SELECT lang FROM users WHERE chat_id=?),'en'), ?)", (chat_id, chat_id, q))
        conn.commit()

# ===== Admin Panel Functions =====
def is_admin(chat_id):
    """Check if user is admin"""
    return chat_id in ADMIN_IDS

def is_banned(chat_id):
    """Check if user is banned"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM banned_users WHERE chat_id=?", (chat_id,))
        return c.fetchone() is not None

def ban_user(chat_id, reason, admin_id):
    """Ban a user"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO banned_users (chat_id, reason, banned_by) VALUES (?, ?, ?)", 
                  (chat_id, reason, admin_id))
        c.execute("INSERT INTO admin_logs (admin_id, action, target_user, details) VALUES (?, ?, ?, ?)",
                  (admin_id, "ban", chat_id, reason))
        conn.commit()

def unban_user(chat_id, admin_id):
    """Unban a user"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM banned_users WHERE chat_id=?", (chat_id,))
        c.execute("INSERT INTO admin_logs (admin_id, action, target_user) VALUES (?, ?, ?)",
                  (admin_id, "unban", chat_id))
        conn.commit()

def get_user_level(chat_id):
    """Get user level (0-3)"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        # Check if admin first
        if is_admin(chat_id):
            return 3
        
        c.execute("SELECT level, vip_until FROM user_levels WHERE chat_id=?", (chat_id,))
        row = c.fetchone()
        
        if row:
            level, vip_until = row
            # Check if VIP expired
            if level == 2 and vip_until:
                try:
                    vip_date = datetime.strptime(vip_until, "%Y-%m-%d %H:%M:%S")
                    if datetime.now() < vip_date:
                        return 2
                    else:
                        # VIP expired, reset to level 0
                        c.execute("UPDATE user_levels SET level=0, vip_until=NULL WHERE chat_id=?", (chat_id,))
                        conn.commit()
                        return 0
                except:
                    return level
            return level
        return 0

def set_user_level(chat_id, level, admin_id, vip_days=None):
    """Set user level"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        vip_until = None
        if level == 2 and vip_days:  # VIP level
            from datetime import timedelta
            vip_until = (datetime.now() + timedelta(days=vip_days)).strftime("%Y-%m-%d %H:%M:%S")
        
        c.execute("INSERT OR REPLACE INTO user_levels (chat_id, level, vip_until, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                  (chat_id, level, vip_until))
        c.execute("INSERT INTO admin_logs (admin_id, action, target_user, details) VALUES (?, ?, ?, ?)",
                  (admin_id, "set_level", chat_id, f"level={level}, vip_days={vip_days}"))
        conn.commit()

def get_daily_usage(chat_id):
    """Get user's daily download count and bytes used"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT download_count, bytes_used FROM daily_user_stats WHERE chat_id=? AND date=CURRENT_DATE", (chat_id,))
        row = c.fetchone()
        return row if row else (0, 0)

def update_daily_usage(chat_id, file_size):
    """Update user's daily usage"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO daily_user_stats (chat_id, download_count, bytes_used) 
            VALUES (?, 1, ?)
            ON CONFLICT(chat_id, date) DO UPDATE SET 
                download_count = download_count + 1,
                bytes_used = bytes_used + ?
        """, (chat_id, file_size, file_size))
        conn.commit()

def check_download_limit(chat_id):
    """Check if user has reached their daily limit"""
    level = get_user_level(chat_id)
    limits = USER_LIMITS.get(level, USER_LIMITS[0])
    
    daily_downloads, daily_bytes = get_daily_usage(chat_id)
    
    # -1 means unlimited
    if limits["daily_downloads"] == -1 and limits["daily_bytes"] == -1:
        return True, "Unlimited"
    
    if daily_downloads >= limits["daily_downloads"]:
        return False, f"Daily download limit reached ({daily_downloads}/{limits['daily_downloads']})"
    
    if daily_bytes >= limits["daily_bytes"]:
        return False, f"Daily volume limit reached ({human_size(daily_bytes)}/{human_size(limits['daily_bytes'])})"
    
    return True, "OK"

def log_admin_action(admin_id, action, target_user=None, details=None):
    """Log admin action"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO admin_logs (admin_id, action, target_user, details) VALUES (?, ?, ?, ?)",
                  (admin_id, action, target_user, details))
        conn.commit()

def get_all_users_count():
    """Get total number of users"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        return c.fetchone()[0]

def get_banned_users_count():
    """Get number of banned users"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM banned_users")
        return c.fetchone()[0]

def get_vip_users_count():
    """Get number of VIP users"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM user_levels WHERE level=2 AND (vip_until IS NULL OR vip_until > datetime('now'))")
        return c.fetchone()[0]

def broadcast_message(message_text, admin_id):
    """Send broadcast message to all users"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO broadcast_messages (message_text, status) VALUES (?, 'pending')", (message_text,))
        broadcast_id = c.lastrowid
        conn.commit()
    
    # Get all users
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT chat_id FROM users")
        users = [row[0] for row in c.fetchall()]
    
    sent_count = 0
    failed_count = 0
    
    for user_id in users:
        try:
            bot.send_message(user_id, message_text, parse_mode="HTML")
            sent_count += 1
            time.sleep(0.1)  # Rate limiting
        except Exception as e:
            failed_count += 1
            print(f"Broadcast failed for user {user_id}: {e}")
    
    # Update broadcast status
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE broadcast_messages SET sent_count=?, status='completed' WHERE id=?", (sent_count, broadcast_id))
        log_admin_action(admin_id, "broadcast", details=f"sent={sent_count}, failed={failed_count}")
        conn.commit()
    
    return sent_count, failed_count

def get_admin_stats():
    """Get comprehensive admin statistics"""
    stats = {
        "total_users": get_all_users_count(),
        "banned_users": get_banned_users_count(),
        "vip_users": get_vip_users_count(),
        "operation_mode": OPERATION_MODE,
        "active_proxies": len(proxy_manager.working_proxies),
        "proxy_failed": len(proxy_manager.failed_proxies),
    }
    return stats

# ===== End Admin Panel Functions =====

def add_detailed_stats(chat_id, platform, file_type, file_size):
    """Add detailed statistics to database with connection pooling"""
    # Check download limit first
    allowed, msg = check_download_limit(chat_id)
    if not allowed:
        print(f"Download limit reached for user {chat_id}: {msg}")
        return False
    
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        # Add to detailed stats table
        c.execute("INSERT INTO detailed_stats (chat_id, platform, file_type, file_size) VALUES (?, ?, ?, ?)", (chat_id, platform, file_type, file_size))

        # Update general stats
        c.execute("SELECT count, bytes FROM stats WHERE chat_id=?", (chat_id,))
        row = c.fetchone()
        if row:
            c.execute("UPDATE stats SET count=?, bytes=? WHERE chat_id=?", (row[0] + 1, row[1] + file_size, chat_id))
        else:
            c.execute("INSERT INTO stats (chat_id, count, bytes) VALUES (?, ?, ?)", (chat_id, 1, file_size))

        # Update global stats
        c.execute("SELECT count, bytes FROM totals WHERE id=1")
        t = c.fetchone()
        c.execute("UPDATE totals SET count=?, bytes=? WHERE id=1", (t[0] + 1, t[1] + file_size))

        # Update uptime stats
        c.execute("UPDATE uptime_stats SET total_downloads = total_downloads + 1 WHERE id=1")
        
        # Update daily usage
        update_daily_usage(chat_id, file_size)

        conn.commit()
    
    return True

def add_stats_with_platform(chat_id, platform, file_type, file_size):
    """Register stats with platform and file type"""
    add_detailed_stats(chat_id, platform, file_type, file_size)

# Cache functions for YouTube qualities with connection pooling
def save_youtube_qualities(chat_id, url, qualities):
    """Save YouTube qualities for a URL with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO youtube_quality_cache (chat_id, url, qualities) VALUES (?, ?, ?)", 
                  (chat_id, url, json.dumps(qualities)))
        conn.commit()

def get_youtube_qualities(chat_id, url):
    """Get cached YouTube qualities with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT qualities FROM youtube_quality_cache WHERE chat_id=? AND url=?", (chat_id, url))
        row = c.fetchone()
        
        if row:
            try:
                return json.loads(row[0])
            except:
                return None
        return None

def clear_youtube_quality_cache(chat_id):
    """Clear YouTube quality cache for a user with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM youtube_quality_cache WHERE chat_id=?", (chat_id,))
        conn.commit()

# Cache functions for YouTube Shorts detection with connection pooling
def save_youtube_shorts_info(chat_id, url, is_short):
    """Save YouTube Shorts detection info with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO youtube_shorts_cache (chat_id, url, is_short) VALUES (?, ?, ?)", 
                  (chat_id, url, is_short))
        conn.commit()

def get_youtube_shorts_info(chat_id, url):
    """Get cached YouTube Shorts info with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT is_short FROM youtube_shorts_cache WHERE chat_id=? AND url=?", (chat_id, url))
        row = c.fetchone()
        
        if row:
            return row[0]
        return None

def clear_youtube_shorts_cache(chat_id):
    """Clear YouTube Shorts cache for a user with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM youtube_shorts_cache WHERE chat_id=?", (chat_id,))
        conn.commit()

# ===== Helper Functions =====
def get_stats(chat_id):
    """Get user statistics from detailed_stats with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        # User stats from detailed_stats
        c.execute("SELECT COUNT(*) as count, SUM(file_size) as bytes FROM detailed_stats WHERE chat_id = ?", (chat_id,))
        user_row = c.fetchone()

        # Global stats from detailed_stats
        c.execute("SELECT COUNT(*) as count, SUM(file_size) as bytes FROM detailed_stats")
        global_row = c.fetchone()

        return {
            "user_count": user_row[0] or 0,
            "user_bytes": user_row[1] or 0,
            "total_count": global_row[0] or 0,
            "total_bytes": global_row[1] or 0
        }

def get_uptime_stats():
    """Get bot uptime statistics with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        c.execute("SELECT start_time, total_downloads, total_processed FROM uptime_stats WHERE id=1")
        row = c.fetchone()

        if row:
            start_time, total_downloads, total_processed = row
            # Calculate uptime
            if start_time:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                now = datetime.now()
                uptime_seconds = (now - start_dt).total_seconds()

                days = int(uptime_seconds // 86400)
                hours = int((uptime_seconds % 86400) // 3600)
                minutes = int((uptime_seconds % 3600) // 60)

                uptime_str = f"{days}d {hours}h {minutes}m"
            else:
                uptime_str = "Unknown"

            return {
                "uptime": uptime_str,
                "total_downloads": total_downloads,
                "total_processed": total_processed
            }

        return {"uptime": "Unknown", "total_downloads": 0, "total_processed": 0}

def get_user_display_name(chat_id):
    """Get user display name (nickname or full name)"""
    try:
        user_info = bot.get_chat(chat_id)
        # Priority to nickname
        if user_info.username:
            return f"@{user_info.username}"
        elif user_info.first_name:
            if user_info.last_name:
                return f"{user_info.first_name} {user_info.last_name}"
            else:
                return user_info.first_name
        else:
            return f"ID:{chat_id}"
    except:
        return f"ID:{chat_id}"

def get_user_username(chat_id):
    """Get pure username (for profile link)"""
    try:
        user_info = bot.get_chat(chat_id)
        return user_info.username
    except:
        return None

def get_top_users_all_time(limit=3):
    """Get top users of all time with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        c.execute("""
            SELECT ds.chat_id, COUNT(*) as download_count, SUM(ds.file_size) as total_size
            FROM detailed_stats ds
            GROUP BY ds.chat_id
            ORDER BY download_count DESC
            LIMIT ?
        """, (limit,))

        results = []
        for row in c.fetchall():
            chat_id, count, size = row

            # Get user display name
            display_name = get_user_display_name(chat_id)

            # Get most used platform for this user correctly
            c.execute("""
                SELECT platform, COUNT(*) as platform_count
                FROM detailed_stats 
                WHERE chat_id = ?
                GROUP BY platform
                ORDER BY platform_count DESC
                LIMIT 1
            """, (chat_id,))
            platform_row = c.fetchone()
            
            if platform_row:
                most_used_platform = platform_row[0]
            else:
                most_used_platform = "Unknown"

            results.append({
                "chat_id": chat_id,
                "display_name": display_name,
                "download_count": count,
                "total_size": size,
                "most_used_platform": most_used_platform
            })

        return results

def get_top_users_daily(limit=3):
    """Get top daily users with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        c.execute("""
            SELECT ds.chat_id, COUNT(*) as download_count, SUM(ds.file_size) as total_size
            FROM detailed_stats ds
            WHERE DATE(ds.timestamp) = DATE('now')
            GROUP BY ds.chat_id
            ORDER BY download_count DESC
            LIMIT ?
        """, (limit,))

        results = []
        for row in c.fetchall():
            chat_id, count, size = row

            display_name = get_user_display_name(chat_id)

            # Get most used platform for this user correctly
            c.execute("""
                SELECT platform, COUNT(*) as platform_count
                FROM detailed_stats 
                WHERE chat_id = ? AND DATE(timestamp) = DATE('now')
                GROUP BY platform
                ORDER BY platform_count DESC
                LIMIT 1
            """, (chat_id,))
            platform_row = c.fetchone()
            
            if platform_row:
                most_used_platform = platform_row[0]
            else:
                most_used_platform = "Unknown"

            results.append({
                "chat_id": chat_id,
                "display_name": display_name,
                "download_count": count,
                "total_size": size,
                "most_used_platform": most_used_platform
            })

        return results

def get_top_users_weekly(limit=3):
    """Get top weekly users with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        c.execute("""
            SELECT ds.chat_id, COUNT(*) as download_count, SUM(ds.file_size) as total_size
            FROM detailed_stats ds
            WHERE ds.timestamp >= datetime('now', '-7 days')
            GROUP BY ds.chat_id
            ORDER BY download_count DESC
            LIMIT ?
        """, (limit,))

        results = []
        for row in c.fetchall():
            chat_id, count, size = row

            display_name = get_user_display_name(chat_id)

            # Get most used platform for this user correctly
            c.execute("""
                SELECT platform, COUNT(*) as platform_count
                FROM detailed_stats 
                WHERE chat_id = ? AND timestamp >= datetime('now', '-7 days')
                GROUP BY platform
                ORDER BY platform_count DESC
                LIMIT 1
            """, (chat_id,))
            platform_row = c.fetchone()
            
            if platform_row:
                most_used_platform = platform_row[0]
            else:
                most_used_platform = "Unknown"

            results.append({
                "chat_id": chat_id,
                "display_name": display_name,
                "download_count": count,
                "total_size": size,
                "most_used_platform": most_used_platform
            })

        return results

def get_platform_ranking_all_time():
    """Platform ranking all time with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        c.execute("SELECT platform, COUNT(*) as download_count, SUM(file_size) as total_size FROM detailed_stats GROUP BY platform ORDER BY download_count DESC")

        results = []
        for row in c.fetchall():
            platform, count, size = row
            results.append({
                "platform": platform,
                "download_count": count,
                "total_size": size
            })

        return results

def get_platform_ranking_daily():
    """Daily platform ranking with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        c.execute("SELECT platform, COUNT(*) as download_count, SUM(file_size) as total_size FROM detailed_stats WHERE DATE(timestamp) = DATE('now') GROUP BY platform ORDER BY download_count DESC")

        results = []
        for row in c.fetchall():
            platform, count, size = row
            results.append({
                "platform": platform,
                "download_count": count,
                "total_size": size
            })

        return results

def get_platform_ranking_weekly():
    """Weekly platform ranking with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        c.execute("SELECT platform, COUNT(*) as download_count, SUM(file_size) as total_size FROM detailed_stats WHERE timestamp >= datetime('now', '-7 days') GROUP BY platform ORDER BY download_count DESC")

        results = []
        for row in c.fetchall():
            platform, count, size = row
            results.append({
                "platform": platform,
                "download_count": count,
                "total_size": size
            })

        return results

def get_user_platform_stats(chat_id, period='all'):
    """User platform statistics with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        if period == 'daily':
            c.execute("SELECT platform, COUNT(*) as download_count, SUM(file_size) as total_size FROM detailed_stats WHERE chat_id = ? AND DATE(timestamp) = DATE('now') GROUP BY platform ORDER BY download_count DESC", (chat_id,))
        elif period == 'weekly':
            c.execute("SELECT platform, COUNT(*) as download_count, SUM(file_size) as total_size FROM detailed_stats WHERE chat_id = ? AND timestamp >= datetime('now', '-7 days') GROUP BY platform ORDER BY download_count DESC", (chat_id,))
        else:  # all time
            c.execute("SELECT platform, COUNT(*) as download_count, SUM(file_size) as total_size FROM detailed_stats WHERE chat_id = ? GROUP BY platform ORDER BY download_count DESC", (chat_id,))

        results = []
        for row in c.fetchall():
            platform, count, size = row
            results.append({
                "platform": platform,
                "download_count": count,
                "total_size": size
            })

        return results

def get_user_stats(chat_id, period='all'):
    """User statistics for specific period with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()

        if period == 'daily':
            c.execute("SELECT COUNT(*) as count, SUM(file_size) as bytes FROM detailed_stats WHERE chat_id = ? AND DATE(timestamp) = DATE('now')", (chat_id,))
        elif period == 'weekly':
            c.execute("SELECT COUNT(*) as count, SUM(file_size) as bytes FROM detailed_stats WHERE chat_id = ? AND timestamp >= datetime('now', '-7 days')", (chat_id,))
        else:  # all time
            c.execute("SELECT COUNT(*) as count, SUM(file_size) as bytes FROM detailed_stats WHERE chat_id = ?", (chat_id,))

        row = c.fetchone()
        if row:
            count, bytes = row
            return {"count": count or 0, "bytes": bytes or 0}

        return {"count": 0, "bytes": 0}

def save_search_choices(chat_id, choices):
    """Save search choices with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM search_cache WHERE chat_id=?", (chat_id,))
        for idx, ch in enumerate(choices):
            c.execute("INSERT INTO search_cache (chat_id, idx, url, title, artist, duration) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, idx, ch["url"], ch["title"], ch["artist"], ch.get("duration", 0)))
        conn.commit()

def save_playlist_choices(chat_id, choices):
    """Save playlist songs for selection with connection pooling"""
    if not choices:
        return

    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM playlist_cache WHERE chat_id=?", (chat_id,))
        for idx, ch in enumerate(choices):
            title = ch.get("title", "Unknown Title")
            artist = ch.get("artist", "Unknown Artist")
            url = ch.get("url", "")
            duration = ch.get("duration", 0)

            c.execute("INSERT INTO playlist_cache (chat_id, idx, url, title, artist, duration) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, idx, url, title, artist, duration))
        conn.commit()

def get_search_choice(chat_id, idx):
    """Get search choice with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT url, title, artist, duration FROM search_cache WHERE chat_id=? AND idx=?", (chat_id, idx))
        row = c.fetchone()
        if not row:
            return None
        return {"url": row[0], "title": row[1], "artist": row[2], "duration": row[3]}

def get_playlist_choice(chat_id, idx):
    """Get selected song from playlist with connection pooling"""
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT url, title, artist, duration FROM playlist_cache WHERE chat_id=? AND idx=?", (chat_id, idx))
        row = c.fetchone()
        if not row:
            return None
        return {"url": row[0], "title": row[1], "artist": row[2], "duration": row[3]}

# ===== Enhanced FileProcessor Class =====
class FileProcessor:
    """Unified file processing for all platforms"""
    
    def __init__(self):
        self.supported_audio_exts = ['.mp3', '.m4a', '.wav', '.ogg', '.opus']
        self.supported_video_exts = ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm']
        self.supported_image_exts = ['.jpg', '.jpeg', '.png', '.webp']
    
    def sanitize_name(self, name: str) -> str:
        """Sanitize filename for all platforms"""
        return re.sub(r'[\\/:*?"<>|\n\r]+', ' ', name).strip()
    
    def extract_artist(self, info: dict) -> str:
        """Extract artist info from metadata"""
        candidates = [
            info.get("uploader"), info.get("creator"), info.get("artist"),
            info.get("uploader_id"), info.get("user"), info.get("username"),
            info.get("channel"), info.get("channel_name"), info.get("author"),
            info.get("post_author"),
        ]

        for c in candidates:
            if c and isinstance(c, str) and c.strip() and c.lower() != "unknown":
                cleaned = c.strip()
                if cleaned.endswith(" - topic"):
                    cleaned = cleaned[:-7].strip()
                if cleaned and len(cleaned) > 1:
                    return cleaned

        title = info.get("title") or ""
        if title and " - " in title:
            parts = title.split(" - ")
            if len(parts) >= 2:
                potential_artist = parts[0].strip()
                if len(potential_artist) > 1 and len(potential_artist) < 50:
                    return potential_artist

        url = info.get("webpage_url") or info.get("url") or ""
        if url:
            patterns = [r"soundcloud\.com/([^/]+)/", r"/user/([^/]+)/", r"/@([^/]+)/", r"/artist/([^/]+)/"]
            for pattern in patterns:
                m = re.search(pattern, url, re.IGNORECASE)
                if m:
                    artist_name = m.group(1).strip()
                    if artist_name and len(artist_name) > 1:
                        return artist_name

        filename = info.get("_filename") or ""
        if filename and " - " in filename:
            parts = filename.split(" - ")
            if len(parts) >= 2:
                potential_artist = parts[0].strip()
                potential_artist = re.sub(r'[\\/:*?"<>|]', '', potential_artist)
                if potential_artist and len(potential_artist) > 1:
                    return potential_artist

        return "unknown"
    
    def download_thumb(self, thumb_url: str, workdir: str) -> str:
        """Download thumbnail for all platforms"""
        try:
            if not thumb_url:
                return ""
            r = requests.get(thumb_url, timeout=10)
            if r.status_code == 200:
                path = os.path.join(workdir, "thumb.jpg")
                with open(path, "wb") as f:
                    f.write(r.content)
                return path
        except Exception:
            pass
        return ""
    
    def force_audio_extension(self, filepath: str) -> str:
        """Ensure audio files have .mp3 extension"""
        base, ext = os.path.splitext(filepath)
        if ext.lower() in [".ogg", ".opus"]:
            new_fp = base + ".mp3"
            try:
                os.rename(filepath, new_fp)
                return new_fp
            except Exception:
                return filepath
        return filepath
    
    def force_video_extension(self, filepath: str) -> str:
        """Ensure all video files have .mp4 extension"""
        base, ext = os.path.splitext(filepath)
        if ext.lower() not in [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"]:
            new_fp = base + ".mp4"
            try:
                os.rename(filepath, new_fp)
                return new_fp
            except Exception:
                return filepath
        elif ext.lower() != ".mp4":
            new_fp = base + ".mp4"
            try:
                os.rename(filepath, new_fp)
                return new_fp
            except Exception:
                return filepath
        return filepath
    
    def find_downloaded_file(self, workdir: str, preferred_exts=None) -> str:
        """Find the downloaded file in workdir"""
        if not os.path.exists(workdir):
            return None
        
        existing_files = [f for f in os.listdir(workdir) if os.path.isfile(os.path.join(workdir, f))]
        
        if preferred_exts:
            # Try to find file with preferred extension first
            for ext in preferred_exts:
                for file in existing_files:
                    if file.lower().endswith(ext.lower()):
                        return os.path.join(workdir, file)
        
        # Return any file found
        if existing_files:
            return os.path.join(workdir, existing_files[0])
        
        return None
    
    def process_soundcloud_file(self, info, workdir: str):
        """Process SoundCloud file with metadata tagging"""
        fp = info.get("_filename")
        
        # If no filename or file doesn't exist, try to find it in workdir
        if not fp or not os.path.exists(fp):
            print(f"Original file not found: {fp}")
            fp = self.find_downloaded_file(workdir, self.supported_audio_exts)
            print(f"Found file in workdir: {fp}")
        
        if not fp or not os.path.exists(fp):
            return None, "file not found"

        title = self.sanitize_name(info.get("title", "soundcloud_audio"))
        artist = self.sanitize_name(self.extract_artist(info) or "unknown")
        ext = os.path.splitext(fp)[1].lstrip(".")
        new_fp = os.path.join(workdir, f"{artist} - {title}.{ext}")
        
        try:
            os.rename(fp, new_fp)
            print(f"Renamed file: {fp} -> {new_fp}")
        except Exception as e:
            print(f"Rename failed: {e}")
            new_fp = fp
        
        # Apply SoundCloud specific tagging
        self._tag_audio_file(new_fp, artist, title, info.get("thumbnail"))
        
        thumb_file = self.download_thumb(info.get("thumbnail"), workdir)
        size = os.path.getsize(new_fp)
        duration = info.get("duration", 0)
        
        print(f"Processed SoundCloud file: {new_fp}, size: {size}, duration: {duration}")
        
        return {
            "filepath": new_fp, "title": title, "artist": artist, "size": size,
            "duration": duration, "thumb_file": thumb_file, "ext": ext.lower(),
        }, None
    
    def process_generic_file(self, info, workdir: str, platform: str = "generic"):
        """Process generic file for other platforms"""
        if not info:
            print("process_generic_file: info is None")
            return None

        print(f"process_generic_file: info keys = {list(info.keys())}")

        fp = info.get("_filename")

        if not fp:
            title = info.get("title") or info.get("fulltitle") or "media"
            title = self.sanitize_name(title)

            # Determine file extension based on platform
            if platform in ["YouTube", "TikTok", "Instagram", "Twitter"]:
                ext = ".mp4"  # Default to video for these platforms
            elif platform == "Pinterest":
                # Pinterest can be image or video
                if info.get("ext"):
                    ext = "." + info["ext"]
                elif info.get("video_ext"):
                    ext = "." + info["video_ext"]
                else:
                    ext = ".mp4"  # Default to video
            else:
                ext = ".mp4"  # Default for unknown platforms

            fp = os.path.join(workdir, f"{title}{ext}")
            print(f"process_generic_file: Generated filename: {fp}")

            if not os.path.exists(fp):
                print(f"process_generic_file: File does not exist at {fp}")
                
                # Try to find any file in workdir
                preferred_exts = self.supported_video_exts if platform != "Pinterest" else self.supported_video_exts + self.supported_image_exts
                fp = self.find_downloaded_file(workdir, preferred_exts)
                
                if fp:
                    print(f"process_generic_file: Found fallback file: {fp}")
                else:
                    print("process_generic_file: No files found in workdir")
                    return None

        # Double-check if file exists before proceeding
        if not os.path.exists(fp):
            print(f"process_generic_file: Final file does not exist at {fp}")
            # Try to find any file in workdir
            preferred_exts = self.supported_video_exts if platform != "Pinterest" else self.supported_video_exts + self.supported_image_exts
            fp = self.find_downloaded_file(workdir, preferred_exts)
            if not fp:
                print("process_generic_file: No files found in workdir")
                return None
            print(f"process_generic_file: Found fallback file: {fp}")

        title = info.get("title") or info.get("fulltitle") or "media"
        if not title:
            title = info.get("alt") or info.get("description") or "media"
        title = self.sanitize_name(title)

        print(f"process_generic_file: title = {title}")

        ext = os.path.splitext(fp)[1].lower()
        new_fp = os.path.join(workdir, f"{title}{ext}")

        print(f"process_generic_file: old_fp = {fp}")
        print(f"process_generic_file: new_fp = {new_fp}")

        try:
            if fp != new_fp:
                os.rename(fp, new_fp)
                print("process_generic_file: file renamed successfully")
        except Exception as e:
            print(f"process_generic_file: rename failed: {e}")
            new_fp = fp

        # Ensure video files have .mp4 extension
        final_fp = new_fp
        if ext in ['.webm', '.mkv', '.avi', '.mov', '.flv']:
            final_fp = self.force_video_extension(new_fp)
            ext = '.mp4'

        # Double-check final file exists
        if not os.path.exists(final_fp):
            print(f"process_generic_file: Final file after extension change does not exist at {final_fp}")
            return None

        size = os.path.getsize(final_fp)
        duration = int(info.get("duration") or 0)
        thumb_url = info.get("thumbnail")
        thumb_file = self.download_thumb(thumb_url, workdir)

        result = {
            "filepath": final_fp, "title": title, "size": size,
            "duration": duration, "thumb_file": thumb_file, "ext": ext.lstrip("."),
        }

        print(f"process_generic_file: result = {result}")
        return result
    
    def _tag_audio_file(self, filepath: str, artist: str, title: str, cover_url: str = None):
        """Tag audio file with metadata"""
        try:
            from mutagen.id3 import ID3, TIT2, TPE1, APIC
            from mutagen.mp4 import MP4, MP4Cover
            from mutagen.oggvorbis import OggVorbis
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".mp3":
                try:
                    id3 = ID3(filepath)
                except Exception:
                    id3 = ID3()
                id3.add(TIT2(encoding=3, text=title))
                id3.add(TPE1(encoding=3, text=artist))
                if cover_url:
                    try:
                        img = requests.get(cover_url, timeout=10).content
                        id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img))
                    except Exception:
                        pass
                id3.save(filepath)
            elif ext in [".m4a", ".mp4", ".aac"]:
                audio = MP4(filepath)
                audio["\xa9nam"] = title
                audio["\xa9ART"] = artist
                if cover_url:
                    try:
                        img = requests.get(cover_url, timeout=10).content
                        audio["covr"] = [MP4Cover(img, imageformat=MP4Cover.FORMAT_JPEG)]
                    except Exception:
                        pass
                audio.save()
            elif ext in [".ogg", ".oga", ".opus"]:
                audio = OggVorbis(filepath)
                audio["title"] = [title]
                audio["artist"] = [artist]
                audio.save()
        except Exception:
            pass

# Initialize global file processor
file_processor = FileProcessor()

# ===== Enhanced CaptionBuilder Class =====
class CaptionBuilder:
    """Unified caption building for all platforms"""
    
    def __init__(self):
        self.platform_emojis = {
            "SoundCloud": "🎵",
            "YouTube": "🎬",
            "Pinterest": "📷",
            "Instagram": "📸",
            "TikTok": "🎵",
            "Twitter": "🐦"
        }
        
        self.platform_names = {
            "SoundCloud": "SoundCloud",
            "YouTube": "YouTube",
            "Pinterest": "Pinterest",
            "Instagram": "Instagram",
            "TikTok": "TikTok",
            "Twitter": "Twitter"
        }
    
    def format_duration_for_lang(self, seconds: int, lang: str) -> str:
        """Format duration based on language"""
        seconds = int(seconds or 0)
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if lang == "fa":
            return f"{h} ساعت {m} دقیقه {s} ثانیه" if h > 0 else f"{m} دقیقه {s} ثانیه"
        return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
    
    def human_size(self, n: int, chat_id=None) -> str:
        """Convert bytes to human readable format with Persian/English units"""
        if chat_id and get_user_lang(chat_id) == "fa":
            # Persian units
            for unit in ["بایت", "کیلوبایت", "مگابایت", "گیگابایت"]:
                if n < 1024.0:
                    return f"{n:.1f} {unit}"
                n /= 1024.0
            return f"{n:.1f} ترابایت"
        else:
            # English units
            for unit in ["B", "KB", "MB", "GB"]:
                if n < 1024.0:
                    return f"{n:.1f} {unit}"
                n /= 1024.0
            return f"{n:.1f} TB"
    
    def build_caption(self, chat_id, platform, item, original_url=None, **kwargs):
        """Build unified caption for any platform"""
        lang = get_user_lang(chat_id) or "en"
        signature = T[lang]["signature"]
        link_text = tr(chat_id, "content_link")
        emoji = self.platform_emojis.get(platform, "📁")
        platform_name = self.platform_names.get(platform, platform)
        
        lines = [f"{emoji} {platform_name}"]
        
        # Platform-specific formatting
        if platform == "SoundCloud":
            lines.extend([
                f"🎵 {item['artist']} - {item['title']}",
                f"⏱️ {self.format_duration_for_lang(item['duration'], lang)}",
                f"💾 {self.human_size(item['size'], chat_id)}"
            ])
        elif platform == "YouTube":
            audio_only = kwargs.get('audio_only', False)
            if audio_only:
                lines.extend([
                    f"🎵 {item['title']}",
                    f"⏱️ {self.format_duration_for_lang(item['duration'], lang)}",
                    f"💾 {self.human_size(item['size'], chat_id)}"
                ])
            else:
                lines.extend([
                    f"🎬 {item['title']}",
                    f"⏱️ {self.format_duration_for_lang(item['duration'], lang)}",
                    f"💾 {self.human_size(item['size'], chat_id)}"
                ])
        else:
            # Generic formatting for other platforms
            if item.get("title"):
                lines.append(f"📝 {item['title']}")
            if item.get("duration"):
                lines.append(f"⏱️ {self.format_duration_for_lang(item['duration'], lang)}")
            lines.append(f"💾 {self.human_size(item['size'], chat_id)}")
        
        # Add original link if provided
        if original_url:
            lines.append(f'🔗 <a href="{original_url}">{link_text}</a>')
        
        # Add signature
        lines.append(f"@{BOT_USERNAME} | {signature}")
        
        return "\n".join(lines)

# Initialize global caption builder
caption_builder = CaptionBuilder()

# ===== Other Helper Functions =====
def human_size(n: int, chat_id=None) -> str:
    """Wrapper for backward compatibility"""
    return caption_builder.human_size(n, chat_id)

def sanitize_name(name: str) -> str:
    """Wrapper for backward compatibility"""
    return file_processor.sanitize_name(name)

def extract_artist(info: dict) -> str:
    """Wrapper for backward compatibility"""
    return file_processor.extract_artist(info)

def format_duration_for_lang(seconds: int, lang: str) -> str:
    """Wrapper for backward compatibility"""
    return caption_builder.format_duration_for_lang(seconds, lang)

def download_thumb(thumb_url: str, workdir: str) -> str:
    """Wrapper for backward compatibility"""
    return file_processor.download_thumb(thumb_url, workdir)

def force_audio_extension(filepath: str) -> str:
    """Wrapper for backward compatibility"""
    return file_processor.force_audio_extension(filepath)

def force_video_extension(filepath: str) -> str:
    """Wrapper for backward compatibility"""
    return file_processor.force_video_extension(filepath)

def process_sc_info_to_file(info, workdir: str):
    """Wrapper for backward compatibility"""
    return file_processor.process_soundcloud_file(info, workdir)

def finalize_generic_item(info, workdir: str):
    """Wrapper for backward compatibility"""
    return file_processor.process_generic_file(info, workdir)

def detect_platform_from_url(url):
    """Detect platform from URL"""
    url = url.lower()
    if "soundcloud.com" in url:
        return "SoundCloud"
    elif "pinterest.com" in url or "pin.it" in url:
        return "Pinterest"
    elif "instagram.com" in url or "instagr.am" in url:
        return "Instagram"
    elif "youtube.com" in url or "youtu.be" in url:
        return "YouTube"
    elif "tiktok.com" in url:
        return "TikTok"
    elif "twitter.com" in url or "x.com" in url or "t.co" in url:
        return "Twitter"
    else:
        return "Unknown"

# ===== Safe message editing =====
_message_cache = {}

def safe_edit_message(text, chat_id, message_id, reply_markup=None):
    """Safely edit a message, avoiding duplicate edits"""
    cache_key = (chat_id, message_id)
    last_text = _message_cache.get(cache_key)

    if last_text == text:
        return

    try:
        if reply_markup:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
        else:
            bot.edit_message_text(text, chat_id, message_id)
        _message_cache[cache_key] = text
    except Exception as e:
        error_msg = str(e).lower()
        if "message is not modified" not in error_msg:
            raise
        _message_cache[cache_key] = text# Telegram Downloader Bot: Enhanced Version - Part 3
# Enhanced YouTube Download Logic with Merging, Shorts Detection, and New Button Formatting

# ===== YouTube Detection and Shorts =====
def is_youtube_short(url: str) -> bool:
    """Detect if URL is a YouTube Short"""
    url = url.lower()
    
    # Check for shorts indicators in URL
    if "youtube.com/shorts/" in url:
        return True
    elif "youtu.be/" in url:
        # youtu.be links can be shorts, need to check video info
        return None  # Unknown, need further checking
    elif "youtube.com/watch" in url:
        # Check for shorts parameters
        if "shorts" in url:
            return True
    
    return False

def get_youtube_video_info(url: str) -> dict:
    """Get YouTube video information to detect shorts and get details"""
    tmpdir = tempfile.mkdtemp(prefix="yt_info_")
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "simulate": True,
            "skip_download": True,
            "cookies": COOKIES_PATH if COOKIES_AVAILABLE else None,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        print(f"Error getting YouTube video info: {e}")
        return {}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def confirm_youtube_short(url: str) -> bool:
    """Confirm if a YouTube video is actually a Short by checking video info"""
    info = get_youtube_video_info(url)
    if not info:
        return False
    
    # Check duration (shorts are typically < 60 seconds)
    duration = info.get("duration", 0)
    if duration and duration <= 60:
        return True
    
    # Check for shorts-specific metadata
    title = info.get("title", "").lower()
    description = info.get("description", "").lower()
    
    # Sometimes shorts have specific indicators
    if "#shorts" in title or "#shorts" in description:
        return True
    
    return False

# ===== Enhanced YouTube Quality Selection with Merging and New Button Format =====
def get_youtube_qualities_with_merging(url: str, chat_id):
    """Get available YouTube qualities with video+audio merging and new button format"""
    print(f"Getting YouTube qualities for: {url}")
    tmpdir = tempfile.mkdtemp(prefix="yt_qualities_")
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "simulate": True,
            "skip_download": True,
            "listformats": True,
            "cookies": COOKIES_PATH if COOKIES_AVAILABLE else None,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return []
            
            formats = info.get("formats", [])
            qualities = []
            
            # Get audio format for merging
            audio_formats = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]
            best_audio = None
            if audio_formats:
                best_audio = max(audio_formats, key=lambda x: x.get("abr", 0) or 0)
            
            # Get video-only formats and merge with audio
            video_formats = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") == "none"]
            video_formats.sort(key=lambda x: x.get("height", 0), reverse=True)
            
            # Define quality priorities for regular YouTube videos
            quality_priorities = [1080, 720, 480, 360, 240, 144]
            
            for height in quality_priorities:
                # Find video format with this height
                video_fmt = None
                for fmt in video_formats:
                    if fmt.get("height") == height:
                        video_fmt = fmt
                        break
                
                if video_fmt and best_audio:
                    # Create merged format ID
                    merged_format_id = f"{video_fmt['format_id']}+{best_audio['format_id']}"
                    
                    # Calculate actual file size by combining video and audio
                    video_size = estimate_file_size(video_fmt, info.get("duration", 0))
                    audio_size = estimate_file_size(best_audio, info.get("duration", 0))
                    total_size = video_size + audio_size
                    
                    if total_size <= TELEGRAM_UPLOAD_LIMIT:
                        # Quality label based on height
                        if height >= 1080:
                            quality_label = "1080p"
                        elif height >= 720:
                            quality_label = "720p"
                        elif height >= 480:
                            quality_label = "480p"
                        elif height >= 360:
                            quality_label = "360p"
                        else:
                            quality_label = f"{height}p"
                        
                        qualities.append({
                            "format_id": merged_format_id,
                            "quality": quality_label,
                            "size": total_size,
                            "ext": "mp4",
                            "type": "video",
                            "height": height,
                            "video_format": video_fmt['format_id'],
                            "audio_format": best_audio['format_id']
                        })
            
            # Add audio-only option
            if best_audio:
                audio_size = estimate_file_size(best_audio, info.get("duration", 0))
                if audio_size <= TELEGRAM_UPLOAD_LIMIT:
                    qualities.append({
                        "format_id": best_audio["format_id"],
                        "quality": "Audio Only",
                        "size": audio_size,
                        "ext": best_audio.get("ext", "mp3"),
                        "type": "audio"
                    })
            
            # Remove duplicates and sort
            unique_qualities = []
            seen_qualities = set()
            for q in qualities:
                key = (q["quality"], q["type"])
                if key not in seen_qualities:
                    unique_qualities.append(q)
                    seen_qualities.add(key)
            
            # Sort: audio first, then video by quality (highest to lowest)
            unique_qualities.sort(key=lambda x: (x["type"] != "audio", -x.get("height", 0)))
            
            print(f"Found {len(unique_qualities)} suitable qualities")
            for q in unique_qualities:
                print(f"  - {q['quality']} ({q['type']}): {human_size(q['size'])}")
            
            return unique_qualities
            
    except Exception as e:
        print(f"Error getting YouTube qualities: {e}")
        return []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def get_best_youtube_short_quality(url: str, chat_id):
    """Get best quality for YouTube Shorts (max 1080p, video+audio) under 50MB"""
    print(f"Getting best YouTube Short quality for: {url}")
    tmpdir = tempfile.mkdtemp(prefix="yt_shorts_")
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "simulate": True,
            "skip_download": True,
            "listformats": True,
            "cookies": COOKIES_PATH if COOKIES_AVAILABLE else None,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            
            formats = info.get("formats", [])
            
            # Get best audio
            audio_formats = [f for f in formats if f.get("acodec") != "none"]
            best_audio = None
            if audio_formats:
                best_audio = max(audio_formats, key=lambda x: x.get("abr", 0) or 0)
            
            # For Shorts, limit to 1080p maximum
            max_height = 1080
            quality_priorities = [1080, 720, 480, 360, 240, 144]
            
            # Get best video with audio (prefer pre-merged)
            video_with_audio = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") != "none"]
            video_with_audio.sort(key=lambda x: x.get("height", 0), reverse=True)
            
            # Try pre-merged formats first (limited to 1080p)
            for fmt in video_with_audio:
                height = fmt.get("height", 0)
                if height <= max_height:  # Limit to 1080p for Shorts
                    size = estimate_file_size(fmt, info.get("duration", 0))
                    if size <= TELEGRAM_UPLOAD_LIMIT:
                        return {
                            "format_id": fmt["format_id"],
                            "quality": f"{height}p",
                            "size": size,
                            "ext": fmt.get("ext", "mp4"),
                            "type": "video"
                        }
            
            # If no suitable pre-merged format, try to merge video-only + audio (limited to 1080p)
            if best_audio:
                video_only = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") == "none"]
                video_only.sort(key=lambda x: x.get("height", 0), reverse=True)
                
                for video_fmt in video_only:
                    height = video_fmt.get("height", 0)
                    if height <= max_height:  # Limit to 1080p for Shorts
                        merged_format_id = f"{video_fmt['format_id']}+{best_audio['format_id']}"
                        video_size = estimate_file_size(video_fmt, info.get("duration", 0))
                        audio_size = estimate_file_size(best_audio, info.get("duration", 0))
                        total_size = video_size + audio_size
                        
                        if total_size <= TELEGRAM_UPLOAD_LIMIT:
                            return {
                                "format_id": merged_format_id,
                                "quality": f"{height}p",
                                "size": total_size,
                                "ext": "mp4",
                                "type": "video",
                                "video_format": video_fmt['format_id'],
                                "audio_format": best_audio['format_id']
                            }
            
            # Fallback to audio only
            if best_audio:
                audio_size = estimate_file_size(best_audio, info.get("duration", 0))
                if audio_size <= TELEGRAM_UPLOAD_LIMIT:
                    return {
                        "format_id": best_audio["format_id"],
                        "quality": "Audio Only",
                        "size": audio_size,
                        "ext": best_audio.get("ext", "mp3"),
                        "type": "audio"
                    }
            
            return None
            
    except Exception as e:
        print(f"Error getting YouTube Short quality: {e}")
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ===== Enhanced Button Formatting =====
def create_youtube_quality_keyboard(qualities, chat_id):
    """Create keyboard for YouTube quality selection with new glass-style button format"""
    lang = get_user_lang(chat_id) or "en"
    kb = InlineKeyboardMarkup()
    
    if not qualities:
        return kb
    
    for i, quality in enumerate(qualities):
        # Safe access to type field with fallback
        quality_type = quality.get("type", "video")
        
        if quality_type == "audio":
            # Audio only button with new format
            if lang == "fa":
                label = "فقط صدا"
            else:
                label = "Audio Only"
            
            size_mb = quality["size"] / (1024 * 1024)
            size_text = f"{size_mb:.1f} مگابایت" if lang == "fa" else f"{size_mb:.1f} MB"
            
            full_label = f"🎵 {label} • {size_text}"
        else:
            # Video button with new format
            quality_label = quality.get("quality", "Unknown")
            size_mb = quality["size"] / (1024 * 1024)
            size_text = f"{size_mb:.1f} مگابایت" if lang == "fa" else f"{size_mb:.1f} MB"
            
            full_label = f"🎬 {quality_label} • {size_text}"
        
        format_id = quality.get("format_id", "unknown")
        callback_data = f"yt_quality:{format_id}:{quality_type}"
        
        # Create button with proper formatting
        if i % 2 == 0:
            # First button in row
            current_row = [InlineKeyboardButton(text=full_label, callback_data=callback_data)]
        else:
            # Second button in row - add the row
            current_row.append(InlineKeyboardButton(text=full_label, callback_data=callback_data))
            kb.row(*current_row)
            current_row = []
    
    # Add the last row if it has only one button
    if 'current_row' in locals() and len(current_row) == 1:
        kb.row(*current_row)
    
    return kb

def create_youtube_shorts_keyboard(chat_id):
    """Create keyboard for YouTube Shorts selection with new glass-style format"""
    lang = get_user_lang(chat_id) or "en"
    kb = InlineKeyboardMarkup()
    
    # For Shorts, we'll use the same format but with simpler options
    if lang == "fa":
        video_btn = "🎬 ویدیو"
        audio_btn = "🎵 فقط صدا"
    else:
        video_btn = "🎬 Video"
        audio_btn = "🎵 Audio Only"
    
    kb.row(
        InlineKeyboardButton(text=video_btn, callback_data="yt_shorts:video"),
        InlineKeyboardButton(text=audio_btn, callback_data="yt_shorts:audio")
    )
    
    return kb

# ===== YouTube Quality Selection Handler =====
def handle_youtube_quality_selection(call):
    """Handle YouTube quality selection callback"""
    chat_id = call.message.chat.id
    data = call.data
    
    if not data.startswith("yt_quality:"):
        return
    
    parts = data.split(":")
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "Invalid format")
        return
    
    format_id = parts[1]
    media_type = parts[2]
    
    bot.answer_callback_query(call.id, "Starting download...")
    
    # Edit the message to show downloading status
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="⏳ در حال دانلود... / Downloading..."
        )
    except:
        pass
    
    # Start download in a thread
    url = call.message.caption.split("\n")[-1] if call.message.caption else ""
    if not url:
        # Try to get URL from user data or cache
        bot.answer_callback_query(call.id, "URL not found", show_alert=True)
        return
    
    download_thread = threading.Thread(
        target=download_youtube_with_quality,
        args=(chat_id, url, format_id, media_type, call.message.message_id),
        daemon=True
    )
    download_thread.start()

# ===== Admin Panel Callback Handler =====
def handle_admin_callback(call):
    """Handle admin panel callbacks"""
    chat_id = call.message.chat.id
    data = call.data
    
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, "❌ Access denied!", show_alert=True)
        return
    
    action = data.replace("admin:", "")
    
    if action == "stats":
        admin_stats = get_admin_stats()
        stats_text = f"""
👤 <b>پنل ادمین - آمار</b>

📊 <b>آمار کلی:</b>
├─ 👥 کاربران کل: {admin_stats['total_users']}
├─ 🚫 کاربران مسدود: {admin_stats['banned_users']}
├─ 💎 کاربران VIP: {admin_stats['vip_users']}
├─ 🌐 پروکسی‌های فعال: {admin_stats['active_proxies']}
├─ ❌ پروکسی‌های ناموفق: {admin_stats['proxy_failed']}
└─ 🔧 حالت عملیاتی: {admin_stats['operation_mode']}
"""
        kb = InlineKeyboardMarkup(row_width=2)
        kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:main"))
        bot.edit_message_text(stats_text, chat_id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    
    elif action == "users":
        users_text = f"""
👥 <b>مدیریت کاربران</b>

کاربران کل: {get_all_users_count()}
کاربران مسدود: {get_banned_users_count()}
کاربران VIP: {get_vip_users_count()}

از دستورات زیر استفاده کنید:
• /ban <user_id> <reason> - مسدود کردن کاربر
• /unban <user_id> - رفع مسدودیت
• /setlevel <user_id> <level> [days] - تنظیم سطح کاربر
"""
        kb = InlineKeyboardMarkup(row_width=2)
        kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:main"))
        bot.edit_message_text(users_text, chat_id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    
    elif action == "broadcast":
        broadcast_text = """
📢 <b>پیام همگانی</b>

برای ارسال پیام به همه کاربران:
1. پیام مورد نظر را فوروارد کنید
2. یا از دستور /broadcast استفاده کنید

تعداد کاربران: {}
""".format(get_all_users_count())
        kb = InlineKeyboardMarkup(row_width=2)
        kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:main"))
        bot.edit_message_text(broadcast_text, chat_id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    
    elif action == "settings":
        settings_text = """
⚙️ <b>تنظیمات ربات</b>

حالت فعلی: {mode}
کانال عضویت: {channel}

برای تغییر تنظیمات، فایل کانفیگ را ویرایش کنید.
""".format(mode=OPERATION_MODE, channel=CHANNEL_USERNAME)
        kb = InlineKeyboardMarkup(row_width=2)
        kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:main"))
        bot.edit_message_text(settings_text, chat_id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    
    elif action == "security":
        security_text = """
🛡️ <b>امنیت</b>

کاربران مسدود: {}
سطح دسترسی ادمین: فعال
محدودیت دانلود: بر اساس سطح کاربر

برای مشاهده لاگ‌ها، به دیتابیس مراجعه کنید.
""".format(get_banned_users_count())
        kb = InlineKeyboardMarkup(row_width=2)
        kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:main"))
        bot.edit_message_text(security_text, chat_id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    
    elif action == "maintenance":
        maintenance_text = """
🔧 <b>نگهداری</b>

فضای دیسک: در حال بررسی...
وضعیت دیتابیس: سالم
آخرین بکاپ: خودکار

عملیات موجود:
• پاکسازی کش (دستی)
• بکاپ دیتابیس (خودکار)
"""
        kb = InlineKeyboardMarkup(row_width=2)
        kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:main"))
        bot.edit_message_text(maintenance_text, chat_id, call.message.message_id, reply_markup=kb, parse_mode="HTML")
    
    elif action == "main":
        # Return to main admin menu
        cmd_admin(type('obj', (object,), {'chat': type('obj', (object,), {'id': chat_id})})())
    
    bot.answer_callback_query(call.id)

# ===== End Admin Panel Callback Handler =====

def handle_youtube_shorts_selection(call):
    """Handle YouTube quality selection callback with merging support"""
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    try:
        # Parse callback data
        parts = call.data.split(":")
        if len(parts) < 3:
            bot.answer_callback_query(call.id, "Invalid selection")
            return
        
        format_id = parts[1]
        media_type = parts[2]  # "audio" or "video"
        
        # Get the URL and qualities from cache
        with db_pool.get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT url FROM youtube_quality_cache WHERE chat_id=?", (chat_id,))
            row = c.fetchone()
        
        if not row:
            bot.answer_callback_query(call.id, "URL not found")
            return
        
        url = row[0]
        
        # Get the quality text for display
        quality_text = "Audio Only" if media_type == "audio" else "Video"
        
        # For video, try to get the actual quality from the format_id
        if media_type == "video":
            # Extract quality from format_id if it's a merged format
            if "+" in format_id:
                # This is a merged format, we need to get the quality from our cached data
                qualities = get_youtube_qualities(chat_id, url)
                if qualities:
                    for q in qualities:
                        if q.get("format_id") == format_id:
                            quality_text = f"Video ({q.get('quality', 'Unknown')})"
                            break
                else:
                    # Fallback: try to extract from format_id (less reliable)
                    quality_text = f"Video ({format_id})"
            else:
                quality_text = f"Video ({format_id})"
        else:
            # For Persian users, show "فقط صدا"
            if get_user_lang(chat_id) == "fa":
                quality_text = "فقط صدا"
            else:
                quality_text = "Audio Only"
        
        # Answer callback immediately to avoid timeout
        bot.answer_callback_query(call.id, tr(chat_id, "youtube_selected_quality", quality=quality_text))
        
        # Delete the quality selection message
        try:
            bot.delete_message(chat_id, message_id)
        except Exception as e:
            print(f"Error deleting quality selection message: {e}")
        
        # Show downloading message with correct quality text
        msg = bot.send_message(chat_id, tr(chat_id, "youtube_downloading", quality=quality_text))
        
        # Download with selected quality
        download_youtube_with_quality(chat_id, url, format_id, media_type, msg.message_id)
        
    except Exception as e:
        print(f"Error in YouTube quality selection: {e}")
        # Only answer callback if not already answered
        try:
            bot.answer_callback_query(call.id, f"Error: {str(e)}", show_alert=True)
        except:
            pass

def handle_youtube_shorts_selection(call):
    """Handle YouTube Shorts selection callback with new logic"""
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    try:
        # Parse callback data
        parts = call.data.split(":")
        if len(parts) < 2:
            bot.answer_callback_query(call.id, "Invalid selection")
            return
        
        choice = parts[1]  # "video" or "audio"
        
        # Get the URL from cache
        with db_pool.get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT url FROM youtube_shorts_cache WHERE chat_id=?", (chat_id,))
            row = c.fetchone()
        
        if not row:
            bot.answer_callback_query(call.id, "URL not found")
            return
        
        url = row[0]
        
        # Delete the selection message
        try:
            bot.delete_message(chat_id, message_id)
        except Exception as e:
            print(f"Error deleting selection message: {e}")
        
        # Get best quality for the short to show actual quality
        quality_info = get_best_youtube_short_quality(url, chat_id)
        
        # Get the quality text for display
        if choice == "audio":
            # For Persian users, show "فقط صدا"
            if get_user_lang(chat_id) == "fa":
                choice_text = "فقط صدا"
            else:
                choice_text = "Audio Only"
        else:
            # For video, show the actual quality
            if quality_info:
                choice_text = f"Video ({quality_info.get('quality', 'Unknown')})"
            else:
                choice_text = "Video"
        
        # Answer callback immediately to avoid timeout
        bot.answer_callback_query(call.id, f"Downloading {choice_text}")
        
        # Delete the selection message
        try:
            bot.delete_message(chat_id, message_id)
        except Exception as e:
            print(f"Error deleting selection message: {e}")
        
        msg = bot.send_message(chat_id, tr(chat_id, "youtube_shorts_downloading"))
        
        # Download the short with best quality
        download_youtube_short_with_choice(chat_id, url, choice, msg.message_id)
        
    except Exception as e:
        print(f"Error in YouTube Shorts selection: {e}")
        # Only answer callback if not already answered
        try:
            bot.answer_callback_query(call.id, f"Error: {str(e)}", show_alert=True)
        except:
            pass

def download_youtube_with_quality(chat_id, url, format_id, media_type, message_id):
    """Download YouTube with specific quality with merging support"""
    tmpdir = tempfile.mkdtemp(prefix="youtube_dl_")
    
    try:
        # Create progress bar instance
        progress_bar = ProgressBar(chat_id, message_id)
        
        def progress_hook(d):
            try:
                if d.get("status") == "downloading":
                    done = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    progress_bar.update(done, total)
            except Exception:
                pass
        
        # Download with selected format
        audio_only = (media_type == "audio")
        ydl_opts = make_youtube_opts(tmpdir, format_id, progress_hook=progress_hook, audio_only=audio_only)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            info["_filename"] = ydl.prepare_filename(info)
            item = finalize_generic_item(info, tmpdir)
            
            if item:
                # Ensure correct file extension
                if not audio_only:
                    item["filepath"] = force_video_extension(item["filepath"])
                
                # Send the file
                send_youtube_item(chat_id, item, url, audio_only)
            else:
                safe_edit_message(tr(chat_id, "error", err="Failed to process downloaded file"), chat_id, message_id)
                
    except Exception as e:
        safe_edit_message(tr(chat_id, "error", err=str(e)), chat_id, message_id)
    finally:
        # Clean up AFTER sending
        if tmpdir and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)
        
        # Clear cache
        clear_youtube_quality_cache(chat_id)

def download_youtube_short_with_choice(chat_id, url, choice, message_id):
    """Download YouTube Short with user choice and no thumbnail"""
    tmpdir = tempfile.mkdtemp(prefix="youtube_short_dl_")
    
    try:
        # Create progress bar instance
        progress_bar = ProgressBar(chat_id, message_id)
        
        def progress_hook(d):
            try:
                if d.get("status") == "downloading":
                    done = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    progress_bar.update(done, total)
            except Exception:
                pass
        
        # Get best quality for the short (max 1080p)
        quality_info = get_best_youtube_short_quality(url, chat_id)
        
        if not quality_info:
            safe_edit_message(tr(chat_id, "error", err="No suitable quality found"), chat_id, message_id)
            return
        
        # Determine format based on user choice
        if choice == "audio":
            # Force audio-only
            format_id = None  # Will use best audio
            audio_only = True
            quality_text = "فقط صدا" if get_user_lang(chat_id) == "fa" else "Audio Only"
        else:
            # Use best video quality found (max 1080p)
            format_id = quality_info["format_id"]
            audio_only = False
            quality_text = quality_info.get('quality', 'Video')
        
        # Update downloading message with actual quality
        safe_edit_message(tr(chat_id, "youtube_downloading", quality=quality_text), chat_id, message_id)
        
        # Download
        ydl_opts = make_youtube_opts(tmpdir, format_id, progress_hook=progress_hook, audio_only=audio_only)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            info["_filename"] = ydl.prepare_filename(info)
            item = finalize_generic_item(info, tmpdir)
            
            if item:
                # Ensure correct file extension
                if not audio_only:
                    item["filepath"] = force_video_extension(item["filepath"])
                
                # Send the file WITHOUT thumbnail for Shorts
                send_youtube_short_item(chat_id, item, url, audio_only)
            else:
                safe_edit_message(tr(chat_id, "error", err="Failed to process downloaded file"), chat_id, message_id)
                
    except Exception as e:
        safe_edit_message(tr(chat_id, "error", err=str(e)), chat_id, message_id)
    finally:
        # Clean up AFTER sending
        if tmpdir and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)
        
        # Clear cache
        clear_youtube_shorts_cache(chat_id)

# ===== Enhanced yt-dlp options builders =====
def make_sc_opts(workdir: str, quality: str, progress_hook=None, force_mp3=False, proxy_url=None):
    format_sel = "bestaudio/best" if quality == "high" else "bestaudio[abr<=128]/bestaudio/best"
    opts = {
        "format": format_sel,
        "noplaylist": False,
        "outtmpl": os.path.join(workdir, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "default_search": "auto",
        "socket_timeout": 60,
        "extractor_retries": 5,
        "fragment_retries": 5,
        "retry_sleep": 2,
        "file_access_retries": 3,
        "retries": 5,
    }
    
    # Add proxy if provided with better configuration
    if proxy_url:
        opts["proxy"] = proxy_url
        # Additional options for better proxy compatibility
        if proxy_url.startswith('http://'):
            opts["http_proxy"] = proxy_url
            opts["https_proxy"] = proxy_url
        elif proxy_url.startswith('socks5://'):
            opts["socks_proxy"] = proxy_url
        
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    if force_mp3:
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    return opts

def make_generic_opts(workdir: str, progress_hook=None, proxy_url=None):
    opts = {
        "format": "bestvideo+bestaudio/bestvideo/bestaudio/best",
        "outtmpl": os.path.join(workdir, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "nocheckcertificate": True,
        "no_check_certificate": True,
        "extractor_retries": 5,
        "socket_timeout": 30,
        "prefer_ffmpeg": True,
        "ignoreerrors": True,
    }
    
    if COOKIES_AVAILABLE:
        opts["extractor_args"] = {
            "instagram": {
                "cookies": [COOKIES_PATH]
            }
        }
        opts["cookiefile"] = COOKIES_PATH

    if proxy_url:
        opts["proxy"] = proxy_url

    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts

def make_youtube_opts(workdir: str, format_id: str, progress_hook=None, proxy_url=None, audio_only=False):
    """YouTube-specific options with quality selection and cookie support"""
    if audio_only:
        opts = {
            "format": "bestaudio[ext=m4a]/",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio", 
                    "preferredcodec": "m4a",  # از m4a استفاده کنید (سریع‌تر از mp3)
                    "preferredquality": "192"  # یا حذف کنید برای کیفیت اصلی
                }
            ],
        }
    else:
        opts = {
            "format": format_id,
            "outtmpl": os.path.join(workdir, "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "nocheckcertificate": True,
            "socket_timeout": 30,
            "extractor_retries": 3,
            "fragment_retries": 3,
            "retry_sleep": 2,
            "prefer_ffmpeg": True,
        }
    
    # Add cookies if available
    if COOKIES_AVAILABLE:
        opts["cookies"] = COOKIES_PATH
        print(f"Using cookies file: {COOKIES_PATH}")
    else:
        print("No cookies file available, proceeding without cookies")
    
    if proxy_url:
        opts["proxy"] = proxy_url
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts

# ===== SoundCloud core =====
def detect_content_type(url):
    """Smart content type detection from link"""
    url = resolve_url(url)

    if 'soundcloud.com' in url:
        if any(indicator in url.lower() for indicator in ['/sets/', '/albums/', '/playlist/']):
            return "playlist"
        if any(pattern in url for pattern in ['/you/', '/stations/']):
            return "playlist"

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; SoundCloudBot/1.0)'}
        response = requests.head(url, headers=headers, allow_redirects=True, timeout=10)
        content_type = response.headers.get('content-type', '')
        final_url = response.url

        if any(indicator in final_url.lower() for indicator in ['/sets/', '/albums/', '/playlist/']):
            return "playlist"
        if any(pattern in final_url for pattern in ['/you/', '/stations/']):
            return "playlist"

    except Exception as e:
        print(f"Error in URL detection: {e}")

    try:
        ydl_opts = {
            "quiet": True, "no_warnings": True, "extract_flat": False,
            "simulate": True, "skip_download": True,
            "cookies": COOKIES_PATH if COOKIES_AVAILABLE else None,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if "entries" in info and info["entries"]:
                if len(info["entries"]) > 1:
                    return "playlist"
                elif len(info["entries"]) == 1:
                    return "single"

            if info.get("_type") == "playlist":
                return "playlist"
            elif info.get("ie_key") == "soundcloud:set":
                return "playlist"
            elif info.get("ie_key") == "soundcloud:track":
                return "single"

    except Exception as e:
        print(f"Error in yt-dlp detection: {e}")

    return "single"

def download_soundcloud_with_retry(url_or_query: str, workdir: str, quality: str, is_search=False, search_limit=15, progress_hook=None, max_retries=15):
    """Download SoundCloud content with proxy retry logic"""
    
    for attempt in range(max_retries):
        proxy_url = None
        
        # Use proxy for SoundCloud if enabled
        if ENABLE_PROXY_FOR_SOUNDCLOUD and not is_search:
            if attempt == 0:
                # First attempt without proxy
                proxy_url = None
            else:
                # Subsequent attempts with proxy
                if ENABLE_PROXY_ROTATION:
                    proxy_url = proxy_manager.get_working_proxy()
                
                if not proxy_url:
                    print("No working proxy available, trying without proxy")
                    proxy_url = None
                else:
                    print(f"Attempt {attempt + 1}: Using proxy {proxy_url}")
                    
                    # Try alternative proxy format if HTTP fails multiple times
                    if attempt > 5 and proxy_url.startswith('http://'):
                        alt_proxy = proxy_manager.get_alternative_proxy_format(proxy_url)
                        if alt_proxy != proxy_url:
                            print(f"Trying alternative format: {alt_proxy}")
                            proxy_url = alt_proxy
        
        try:
            ydl_opts = make_sc_opts(workdir, quality, progress_hook=progress_hook, force_mp3=FORCE_MP3, proxy_url=proxy_url)
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if is_search:
                    info = ydl.extract_info(f"scsearch{search_limit}:{url_or_query}", download=False)
                    entries = info.get("entries") or []
                    choices = []
                    for e in entries:
                        choices.append({
                            "title": e.get("title"), "artist": extract_artist(e),
                            "url": e.get("webpage_url"), "duration": e.get("duration", 0),
                            "thumb": e.get("thumbnail"),
                        })
                    return {"choices": choices, "ok": True}
                else:
                    content_type = detect_content_type(url_or_query)
                    print(f"Detected content type: {content_type} for URL: {url_or_query}")

                    if content_type == "playlist":
                        ydl_opts_playlist = {
                            "quiet": True, "no_warnings": True, "extract_flat": False,
                            "simulate": True, "skip_download": True,
                        }
                        
                        # Add proxy to playlist detection if available
                        if proxy_url:
                            ydl_opts_playlist["proxy"] = proxy_url

                        with yt_dlp.YoutubeDL(ydl_opts_playlist) as ydl_playlist:
                            info = ydl_playlist.extract_info(url_or_query, download=False)

                            if "entries" in info and info["entries"]:
                                playlist_items = []
                                for e in info["entries"]:
                                    if e:
                                        playlist_items.append({
                                            "title": e.get("title", "Unknown Title"),
                                            "artist": extract_artist(e) or "Unknown Artist",
                                            "url": e.get("webpage_url", ""),
                                            "duration": e.get("duration", 0),
                                            "thumb": e.get("thumbnail"),
                                        })

                                return {"playlist": playlist_items, "ok": True, "content_type": "playlist"}
                            else:
                                return {"error": "No playlist items found", "ok": False}
                    else:
                        info = ydl.extract_info(url_or_query, download=True)
                        info["_filename"] = ydl.prepare_filename(info)
                        item, err = process_sc_info_to_file(info, workdir)
                        if not item:
                            return {"error": err or "failed", "ok": False}
                        return {"item": item, "ok": True, "content_type": "single"}
                        
        except Exception as e:
            error_str = str(e).lower()
            print(f"Attempt {attempt + 1} failed: {error_str}")
            
            # Check if it's a geo-restriction error
            if "geo restriction" in error_str or "not available from your location" in error_str:
                if attempt < max_retries - 1:
                    print("Geo-restriction detected, will retry with proxy")
                    continue
            
            # If it's last attempt, return error
            if attempt == max_retries - 1:
                return {"error": str(e), "ok": False}
            
            # Continue to next attempt
            continue
    
    return {"error": "All attempts failed", "ok": False}

def download_soundcloud(url_or_query: str, workdir: str, quality: str, is_search=False, search_limit=15, progress_hook=None):
    """Wrapper for backward compatibility"""
    return download_soundcloud_with_retry(url_or_query, workdir, quality, is_search, search_limit, progress_hook)

# ===== Enhanced Pinterest Downloader =====
def download_pinterest_enhanced(url: str, workdir: str, progress_hook=None):
    """Enhanced Pinterest downloader with better error handling and multiple strategies"""
    print(f"Starting enhanced Pinterest download for: {url}")
    
    # Multiple download strategies
    strategies = [
        # Strategy 1: Direct yt-dlp with custom headers
        {
            "format": "best",
            "outtmpl": os.path.join(workdir, "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
            "nocheckcertificate": True,
            "ignoreerrors": True,
            "extractor_retries": 3,
            "socket_timeout": 20,
        },
        # Strategy 2: Mobile user agent
        {
            "format": "best",
            "outtmpl": os.path.join(workdir, "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
            },
            "nocheckcertificate": True,
            "ignoreerrors": True,
            "extractor_retries": 3,
        },
        # Strategy 3: Generic fallback
        {
            "format": "bestvideo+bestaudio/bestvideo/bestaudio/best",
            "outtmpl": os.path.join(workdir, "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "nocheckcertificate": True,
            "ignoreerrors": True,
        }
    ]
    
    for i, opts in enumerate(strategies, 1):
        try:
            print(f"Trying Pinterest strategy {i}/{len(strategies)}")
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                if not info:
                    print(f"Strategy {i}: No info extracted")
                    continue
                
                info["_filename"] = ydl.prepare_filename(info)
                item = finalize_generic_item(info, workdir)
                
                if item:
                    print(f"Strategy {i}: Successfully downloaded Pinterest content")
                    return {"item": item, "ok": True}
                else:
                    print(f"Strategy {i}: Failed to finalize item")
                    continue
                    
        except Exception as e:
            print(f"Strategy {i} failed: {str(e)}")
            if i == len(strategies):
                return {"error": f"All Pinterest strategies failed. Last error: {str(e)}", "ok": False}
            continue
    
    return {"error": "All Pinterest download strategies failed", "ok": False}

def resolve_url(url: str) -> str:
    try:
        r = requests.head(url, allow_redirects=True, timeout=10)
        return r.url or url
    except Exception:
        return url

def estimate_file_size(format_info, duration_seconds):
    """Estimate file size based on format info and duration"""
    try:
        # Get bitrate information
        if format_info.get("vcodec") != "none" and format_info.get("acodec") != "none":
            # Video + audio
            vbr = format_info.get("vbr", 0) or format_info.get("tbr", 0) or 1000  # Default to 1000 kbps
            abr = format_info.get("abr", 0) or 128  # Default to 128 kbps for audio
            total_bitrate = vbr + abr
        elif format_info.get("vcodec") == "none" and format_info.get("acodec") != "none":
            # Audio only
            total_bitrate = format_info.get("abr", 0) or format_info.get("tbr", 0) or 128
        else:
            # Video only or unknown
            total_bitrate = format_info.get("tbr", 0) or 1000
        
        # Calculate size (in bytes)
        size_bits = total_bitrate * 1000 * duration_seconds  # Convert kbps to bits
        size_bytes = size_bits / 8  # Convert bits to bytes
        
        # Add some buffer (10%)
        size_bytes *= 1.1
        
        return int(size_bytes)
        
    except Exception as e:
        print(f"Error estimating file size: {e}")
        # Return a conservative estimate
        return 10 * 1024 * 1024  # 10MB default# Telegram Downloader Bot: Enhanced Version - Part 4
# YouTube Handlers, Statistics, and Core Functionality

# ===== YouTube Handler with Shorts Detection =====
def handle_download_youtube(chat_id, url):
    """Handle YouTube download with quality selection and Shorts detection"""
    lang = get_user_lang(chat_id) or "en"
    
    # Check if it's a YouTube Short
    is_short = is_youtube_short(url)
    
    # If URL detection is inconclusive, check video info
    if is_short is None:
        is_short = confirm_youtube_short(url)
    
    if is_short:
        print(f"YouTube Short detected: {url}")
        # Handle as YouTube Short
        msg = bot.send_message(chat_id, tr(chat_id, "youtube_shorts_detected"))
        msg_id = msg.message_id
        
        try:
            # Save URL for later use
            save_youtube_shorts_info(chat_id, url, True)
            
            # Create selection keyboard with new format
            kb = create_youtube_shorts_keyboard(chat_id)
            
            # Update message with selection options
            bot.edit_message_text(tr(chat_id, "youtube_shorts_prompt"), chat_id, msg_id, reply_markup=kb)
            
        except Exception as e:
            bot.edit_message_text(tr(chat_id, "error", err=str(e)), chat_id, msg_id)
    else:
        print(f"Regular YouTube video detected: {url}")
        # Handle as regular YouTube video
        msg = bot.send_message(chat_id, tr(chat_id, "youtube_processing"))
        msg_id = msg.message_id
        
        try:
            # Get available qualities with merging
            qualities = get_youtube_qualities_with_merging(url, chat_id)
            
            if not qualities:
                bot.edit_message_text(tr(chat_id, "youtube_no_qualities"), chat_id, msg_id)
                return
            
            # Save URL for later use
            save_youtube_qualities(chat_id, url, qualities)
            
            # Create quality selection keyboard with new format
            kb = create_youtube_quality_keyboard(qualities, chat_id)
            
            # Update message with quality selection
            bot.edit_message_text(tr(chat_id, "youtube_quality_prompt"), chat_id, msg_id, reply_markup=kb)
            
        except Exception as e:
            bot.edit_message_text(tr(chat_id, "error", err=str(e)), chat_id, msg_id)

# ===== Generic handlers for other platforms =====
def handle_download_pinterest(chat_id, url):
    handle_generic_download(chat_id, url, "Pinterest")

def handle_download_instagram(chat_id, url):
    handle_generic_download(chat_id, url, "Instagram")

def handle_download_tiktok(chat_id, url):
    handle_generic_download(chat_id, url, "TikTok")

def handle_download_twitter(chat_id, url):
    handle_generic_download(chat_id, url, "Twitter")

def handle_generic_download(chat_id, url, platform):
    """Generic download with optimized progress bar"""
    msg = bot.send_message(chat_id, tr(chat_id, "downloading"))
    msg_id = msg.message_id

    # Create progress bar instance
    progress_bar = ProgressBar(chat_id, msg_id)

    def hook(d):
        try:
            if d.get("status") == "downloading":
                done = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                progress_bar.update(done, total)
        except Exception as e:
            pass

    tmpdir = tempfile.mkdtemp(prefix="gendl_")
    try:
        res = download_generic(url, tmpdir, progress_hook=hook)
        if not res.get("ok"):
            safe_edit_message(tr(chat_id, "error", err=res.get("error", "failed")), chat_id, msg_id)
            return

        if "playlist" in res:
            for item in res["playlist"]:
                send_media_item(chat_id, item, platform, url)
        else:
            item = res["item"]
            send_media_item(chat_id, item, platform, url)
    except Exception as e:
        safe_edit_message(tr(chat_id, "error", err=str(e)), chat_id, msg_id)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ===== Generic download functions =====
def download_generic(url: str, workdir: str, progress_hook=None):
    """Generic download with smart platform detection"""
    if "pinterest.com" in url or "pin.it" in url:
        print("Detected Pinterest URL, using enhanced downloader")
        return download_pinterest_enhanced(url, workdir, progress_hook)

    opts = make_generic_opts(workdir, progress_hook=progress_hook)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

            if not info:
                return {"error": "No info extracted from URL", "ok": False}

            entries = info.get("entries")
            if entries and isinstance(entries, list):
                items = []
                for e in entries:
                    if e:
                        e["_filename"] = ydl.prepare_filename(e)
                        it = finalize_generic_item(e, workdir)
                        if it:
                            items.append(it)
                return {"playlist": items, "ok": True} if items else {"error": "No valid items found", "ok": False}
            else:
                info["_filename"] = ydl.prepare_filename(info)
                it = finalize_generic_item(info, workdir)
                if it:
                    return {"item": it, "ok": True}
                return {"error": "Failed to finalize item", "ok": False}
    except Exception as e:
        return {"error": str(e), "ok": False}

# ===== SoundCloud flow =====
def handle_download_soundcloud(chat_id, url):
    content_type = detect_content_type(url)
    lang = get_user_lang(chat_id) or "en"

    if content_type == "playlist":
        msg = bot.send_message(chat_id, tr(chat_id, "downloading_playlist"))
        msg_id = msg.message_id

        tmpdir = tempfile.mkdtemp(prefix="scdl_")
        try:
            ydl_opts_flat = {
                "quiet": True, "no_warnings": True, "extract_flat": True,
                "simulate": True, "skip_download": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts_flat) as ydl:
                info = ydl.extract_info(url, download=False)

                if "entries" in info and info["entries"]:
                    entries = [e for e in info["entries"] if e]

                    bot.edit_message_text(tr(chat_id, "playlist_detected", count=len(entries)), chat_id, msg_id)

                    playlist_items = []

                    for i, e in enumerate(entries):
                        if not e.get("title") or e.get("title") == "Unknown Title":
                            try:
                                single_opts = {
                                    "quiet": True, "no_warnings": True, "extract_flat": False,
                                    "simulate": True, "skip_download": True,
                                }

                                with yt_dlp.YoutubeDL(single_opts) as ydl_single:
                                    track_url = e.get("url") or e.get("webpage_url", "")
                                    if track_url:
                                        track_info = ydl_single.extract_info(track_url, download=False)
                                        e = track_info
                            except Exception as ex:
                                print(f"Error getting track info: {ex}")

                        title = e.get("title")
                        if not title or title == "Unknown Title":
                            url_text = e.get("webpage_url", e.get("url", ""))
                            if url_text:
                                import re
                                url_match = re.search(r'/([^/]+)(?:\?|$)', url_text)
                                if url_match:
                                    title = url_match.group(1).replace('-', ' ').replace('_', ' ').title()

                        artist = extract_artist(e)
                        if not artist or artist == "unknown":
                            if title and " - " in title:
                                artist = title.split(" - ")[0].strip()
                                title = title.split(" - ", 1)[1].strip()

                        final_title = title if title else f"Track {i+1}"
                        final_artist = artist if artist else "Unknown Artist"

                        playlist_items.append({
                            "title": final_title, "artist": final_artist,
                            "url": e.get("webpage_url", e.get("url", "")),
                            "duration": e.get("duration", 0), "thumb": e.get("thumbnail"),
                        })

                        if (i + 1) % 5 == 0:
                            bot.edit_message_text(tr(chat_id, "processing_playlist") + f" ({i+1}/{len(entries)})", chat_id, msg_id)

                    # Save playlist choices and send keyboard
                    save_playlist_choices(chat_id, playlist_items)

                    kb = create_paginated_keyboard(playlist_items, chat_id, 0, 10, "playlist")
                    bot.send_message(chat_id, tr(chat_id, "playlist_song_selection"), reply_markup=kb)
                else:
                    bot.edit_message_text(tr(chat_id, "no_results_found"), chat_id, msg_id)
                    
        except Exception as e:
            bot.edit_message_text(tr(chat_id, "error", err=str(e)), chat_id, msg_id)
        finally:
            # Clean up AFTER processing (very important!)
            if tmpdir and os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
    else:
        handle_single_soundcloud(chat_id, url)

def handle_single_soundcloud(chat_id, url):
    """Download single SoundCloud track with proxy support and retry logic"""
    msg = bot.send_message(chat_id, tr(chat_id, "downloading_single"))
    msg_id = msg.message_id

    # Create progress bar instance
    progress_bar = ProgressBar(chat_id, msg_id)
    proxy_retry_notified = False

    def hook(d):
        nonlocal proxy_retry_notified
        try:
            if d.get("status") == "downloading":
                done = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                progress_bar.update(done, total)
        except Exception as e:
            pass

    def download_with_retry():
        nonlocal proxy_retry_notified
        
        for attempt in range(3):  # Maximum 3 attempts
            proxy_url = None
            
            # First attempt without proxy, subsequent attempts with proxy
            if attempt > 0 and ENABLE_PROXY_FOR_SOUNDCLOUD:
                if not proxy_retry_notified:
                    bot.edit_message_text(tr(chat_id, "geo_restriction_error"), chat_id, msg_id)
                    proxy_retry_notified = True
                else:
                    bot.edit_message_text(tr(chat_id, "proxy_retry"), chat_id, msg_id)
                
                # Get a working proxy from proxy manager
                proxy_url = proxy_manager.get_working_proxy()
                
                if proxy_url:
                    print(f"Attempt {attempt + 1}: Using proxy {proxy_url}")
                else:
                    print(f"Attempt {attempt + 1}: No proxy available, trying without")
                    proxy_url = None
            
            tmpdir = tempfile.mkdtemp(prefix="scdl_")
            try:
                res = download_soundcloud_with_retry(url, tmpdir, get_user_quality(chat_id), is_search=False, progress_hook=hook, max_retries=1)
                
                if res.get("ok"):
                    return res, tmpdir
                else:
                    error_msg = res.get("error", "failed")
                    print(f"Attempt {attempt + 1} failed: {error_msg}")
                    
                    # Check if it's a geo-restriction error
                    if "geo restriction" in error_msg.lower() or "not available from your location" in error_msg.lower():
                        if attempt < 2:  # Don't give up yet
                            # Clean up and continue to next attempt
                            if tmpdir and os.path.exists(tmpdir):
                                shutil.rmtree(tmpdir, ignore_errors=True)
                            continue
                    
                    # If it's last attempt, return error with tmpdir for cleanup
                    if attempt == 2:
                        return res, tmpdir
                        
            except Exception as e:
                print(f"Attempt {attempt + 1} exception: {str(e)}")
                if attempt == 2:
                    return {"error": str(e), "ok": False}, tmpdir
            
            # Clean up on failed attempts (except last one)
            if tmpdir and os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        
        return {"error": "All attempts failed", "ok": False}, None

    try:
        res, tmpdir = download_with_retry()
        
        if not res.get("ok"):
            safe_edit_message(tr(chat_id, "error", err=res.get("error", "failed")), chat_id, msg_id)
            # Clean up on error
            if tmpdir and os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
            return

        item = res["item"]
        
        try:
            send_sc_item(chat_id, item, url)
        except Exception as e:
            print(f"Error sending item: {e}")
            safe_edit_message(tr(chat_id, "error", err=f"Failed to send file: {str(e)}"), chat_id, msg_id)
        finally:
            # Clean up AFTER sending (very important!)
            if tmpdir and os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        
    except Exception as e:
        safe_edit_message(tr(chat_id, "error", err=str(e)), chat_id, msg_id)
        # Clean up on any error
        try:
            if 'tmpdir' in locals() and tmpdir and os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        except:
            pass

# ===== Forced join =====
def is_member(chat_id):
    try:
        m = bot.get_chat_member(CHANNEL_USERNAME, chat_id)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False

def join_keyboard(chat_id):
    lang = get_user_lang(chat_id) or "en"
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton(text=T[lang]["join_btn"], url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"))
    return kb

# ===== Keyboards =====
def lang_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton(text="فارسی 🇮🇷", callback_data="start_lang:fa"),
        InlineKeyboardButton(text="English 🇬🇧", callback_data="start_lang:en"),
    )
    return kb

def sc_quality_keyboard(chat_id):
    lang = get_user_lang(chat_id) or "en"
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton(text=T[lang]["quality_high"], callback_data="quality:high"),
        InlineKeyboardButton(text=T[lang]["quality_low"], callback_data="quality:low"),
    )
    return kb

def create_paginated_keyboard(choices, chat_id, page=0, per_page=15, prefix="search"):
    """Create paginated keyboard"""
    lang = get_user_lang(chat_id) or "en"
    kb = InlineKeyboardMarkup()

    if not choices:
        return kb

    start_idx = page * per_page
    end_idx = min(start_idx + per_page, len(choices))

    for i in range(start_idx, end_idx):
        ch = choices[i]
        if prefix == "search":
            artist = ch.get("artist", "Unknown Artist")
            title = ch.get("title", "Unknown Title")
            label = f"{i+1}. {artist} - {title}"
            callback_data = f"pick:{i}"
        elif prefix == "playlist":
            artist = ch.get("artist", "Unknown Artist")
            title = ch.get("title", "Unknown Title")
            label = f"🎵 {artist} - {title}"
            callback_data = f"playlist_pick:{i}"
        else:
            title = ch.get("title", "Unknown Title")
            label = f"{i+1}. {title}"
            callback_data = f"pick:{i}"

        kb.row(InlineKeyboardButton(text=label[:64], callback_data=callback_data))

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text=tr(chat_id, "previous_page"), callback_data=f"{prefix}_page:{page-1}"))

    total_pages = (len(choices) + per_page - 1) // per_page
    nav_row.append(InlineKeyboardButton(text=tr(chat_id, "page_number", page=page+1, total_pages=total_pages), callback_data="noop"))

    if end_idx < len(choices):
        nav_row.append(InlineKeyboardButton(text=tr(chat_id, "next_page"), callback_data=f"{prefix}_page:{page+1}"))

    if nav_row:
        kb.row(*nav_row)

    return kb

# ===== Features message =====
def send_features_message(chat_id):
    lang = get_user_lang(chat_id)
    header = T[lang]["features_header"]
    lines = T[lang]["features_lines"]
    companion = T[lang]["companion_label"].format(id=COMPANION_ID)
    text = header + "\n" + "\n".join(lines) + "\n" + companion
    bot.send_message(chat_id, text)

# ===== Statistics Functions =====
def get_stats_text(chat_id):
    """Get statistics text for message editing"""
    user_stats = get_stats(chat_id)
    uptime_stats = get_uptime_stats()

    text = f"📊 {tr(chat_id, 'stats_title')}\n\n"
    text += f"👤 {tr(chat_id, 'your_stats')}:\n"
    text += f"📁 {tr(chat_id, 'downloads')}: {user_stats['user_count']}\n"
    text += f"💾 {tr(chat_id, 'volume')}: {human_size(user_stats['user_bytes'])}\n\n"

    text += f"🌍 {tr(chat_id, 'global_stats')}:\n"
    text += f"📁 {tr(chat_id, 'downloads')}: {user_stats['total_count']}\n"
    text += f"💾 {tr(chat_id, 'volume')}: {human_size(user_stats['total_bytes'])}\n"
    text += f"⏱️ {tr(chat_id, 'uptime')}: {uptime_stats['uptime']}\n"
    text += f"\n"
    text += f"🔍 {tr(chat_id, 'choose_category')}:"

    return text

def send_stats_main(chat_id):
    """Send main statistics page"""
    text = get_stats_text(chat_id)
    bot.send_message(chat_id, text, reply_markup=create_stats_keyboard(chat_id))

def edit_stats_main(chat_id, message_id):
    """Edit main statistics page (instead of sending new message)"""
    text = get_stats_text(chat_id)
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=create_stats_keyboard(chat_id))
    except Exception as e:
        print(f"Error editing stats message: {e}")
        # If editing fails, send new message
        bot.send_message(chat_id, text, reply_markup=create_stats_keyboard(chat_id))

def send_top_users_stats(chat_id, message_id, period='all'):
    """Send top users statistics - with message editing"""
    if period == 'daily':
        top_users = get_top_users_daily(3)
        title = tr(chat_id, 'daily_top_user_stats')
        user_period = 'daily'
    elif period == 'weekly':
        top_users = get_top_users_weekly(3)
        title = tr(chat_id, 'weekly_top_user_stats')
        user_period = 'weekly'
    else:
        top_users = get_top_users_all_time(3)
        title = tr(chat_id, 'top_user_stats')
        user_period = 'all'

    if not top_users:
        try:
            bot.edit_message_text(tr(chat_id, 'no_data'), chat_id, message_id, reply_markup=create_back_keyboard(chat_id))
        except Exception as e:
            print(f"Error editing message: {e}")
            bot.send_message(chat_id, tr(chat_id, 'no_data'), reply_markup=create_back_keyboard(chat_id))
        return

    text = f"{title}\n\n"

    for i, user in enumerate(top_users, 1):
        text += f"🏅 {tr(chat_id, 'rank')} {i}\n"
        text += f"👤 {tr(chat_id, 'user')}: {user['display_name']}\n"
        text += f"📁 {tr(chat_id, 'downloads')}: {user['download_count']}\n"
        text += f"💾 {tr(chat_id, 'volume')}: {human_size(user['total_size'])}\n"
        text += f"🎯 {tr(chat_id, 'most_used')}: {user['most_used_platform']}\n\n"

    # Add referring user's statistics
    user_stats = get_user_stats(chat_id, user_period)
    if user_stats['count'] > 0:
        if user_period == 'daily':
            text += f"📊 {tr(chat_id, 'your_daily_stats')}:\n"
        elif user_period == 'weekly':
            text += f"📊 {tr(chat_id, 'your_weekly_stats')}:\n"
        else:
            text += f"📊 {tr(chat_id, 'your_stats')}:\n"

        text += f"📁 {tr(chat_id, 'downloads')}: {user_stats['count']}\n"
        text += f"💾 {tr(chat_id, 'volume')}: {human_size(user_stats['bytes'])}\n"
    else:
        if user_period == 'daily':
            text += f"📊 {tr(chat_id, 'your_daily_stats')}: {tr(chat_id, 'no_user_data')}\n"
        elif user_period == 'weekly':
            text += f"📊 {tr(chat_id, 'your_weekly_stats')}: {tr(chat_id, 'no_user_data')}\n"
        else:
            text += f"📊 {tr(chat_id, 'your_stats')}: {tr(chat_id, 'no_user_data')}\n"

    # Edit main message instead of sending new one
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=create_back_keyboard(chat_id))
    except Exception as e:
        print(f"Error editing message: {e}")
        # If editing fails, send new message
        bot.send_message(chat_id, text, reply_markup=create_back_keyboard(chat_id))

def send_top_platforms_stats(chat_id, message_id, period='all'):
    """Send top platforms statistics"""
    if period == 'daily':
        platforms = get_platform_ranking_daily()
        title = tr(chat_id, 'daily_top_platform_stats')
        user_period = 'daily'
    elif period == 'weekly':
        platforms = get_platform_ranking_weekly()
        title = tr(chat_id, 'weekly_top_platform_stats')
        user_period = 'weekly'
    else:
        platforms = get_platform_ranking_all_time()
        title = tr(chat_id, 'top_platform_stats')
        user_period = 'all'

    if not platforms:
        try:
            bot.edit_message_text(tr(chat_id, 'no_data'), chat_id, message_id, reply_markup=create_back_keyboard(chat_id))
        except Exception as e:
            print(f"Error editing message: {e}")
            bot.send_message(chat_id, tr(chat_id, 'no_data'), reply_markup=create_back_keyboard(chat_id))
        return

    text = f"{title}\n\n"

    for i, platform in enumerate(platforms, 1):
        text += f"🏅 {tr(chat_id, 'rank')} {i}\n"
        text += f"🎯 {tr(chat_id, 'platform')}: {platform['platform']}\n"
        text += f"📁 {tr(chat_id, 'downloads')}: {platform['download_count']}\n"
        text += f"💾 {tr(chat_id, 'volume')}: {human_size(platform['total_size'])}\n\n"

    # Add referring user's platform statistics
    user_platforms = get_user_platform_stats(chat_id, user_period)
    if user_platforms:
        if user_period == 'daily':
            text += f"📊 {tr(chat_id, 'your_daily_stats')}:\n"
        elif user_period == 'weekly':
            text += f"📊 {tr(chat_id, 'your_weekly_stats')}:\n"
        else:
            text += f"📊 {tr(chat_id, 'your_stats')}:\n"

        for platform in user_platforms:
            text += f"🎯 {platform['platform']}: {platform['download_count']} ({human_size(platform['total_size'])})\n"
    else:
        if user_period == 'daily':
            text += f"📊 {tr(chat_id, 'your_daily_stats')}: {tr(chat_id, 'no_user_data')}\n"
        elif user_period == 'weekly':
            text += f"📊 {tr(chat_id, 'your_weekly_stats')}: {tr(chat_id, 'no_user_data')}\n"
        else:
            text += f"📊 {tr(chat_id, 'your_stats')}: {tr(chat_id, 'no_user_data')}\n"

    # Edit main message instead of sending new one
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=create_back_keyboard(chat_id))
    except Exception as e:
        print(f"Error editing message: {e}")
        # If editing fails, send new message
        bot.send_message(chat_id, text, reply_markup=create_back_keyboard(chat_id))

def create_stats_keyboard(chat_id):
    """Create main statistics keyboard with close button"""
    lang = get_user_lang(chat_id) or "en"
    kb = InlineKeyboardMarkup()

    kb.row(
        InlineKeyboardButton(text=f"👑 {tr(chat_id, 'top_users_all_time')}", callback_data="stats:top_users_all"),
        InlineKeyboardButton(text=f"🏆 {tr(chat_id, 'top_platforms_all_time')}", callback_data="stats:top_platforms_all")
    )

    kb.row(
        InlineKeyboardButton(text=f"📅 {tr(chat_id, 'top_users_daily')}", callback_data="stats:top_users_daily"),
        InlineKeyboardButton(text=f"📊 {tr(chat_id, 'top_platforms_daily')}", callback_data="stats:top_platforms_daily")
    )

    kb.row(
        InlineKeyboardButton(text=f"📆 {tr(chat_id, 'top_users_weekly')}", callback_data="stats:top_users_weekly"),
        InlineKeyboardButton(text=f"📈 {tr(chat_id, 'top_platforms_weekly')}", callback_data="stats:top_platforms_weekly")
    )

    # Add close button in last row
    kb.row(InlineKeyboardButton(text=tr(chat_id, "close_menu"), callback_data="stats:close"))

    return kb

def create_back_keyboard(chat_id):
    """Create back keyboard"""
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton(text=f"🔙 {tr(chat_id, 'back_to_stats')}", callback_data="stats:main"))
    return kb# Telegram Downloader Bot: Enhanced Version - Part 5
# Main Commands, Handlers, File Senders, and Flask Server

# ===== Commands =====
@bot.message_handler(commands=["start"])
def cmd_start(message):
    chat_id = message.chat.id
    
    # Check if user is banned
    if is_banned(chat_id):
        bot.send_message(chat_id, "❌ شما مسدود شده‌اید.\n❌ You have been banned.")
        return

    # Initialize user in database if new
    with db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (chat_id, lang, quality) VALUES (?, NULL, 'high')", (chat_id,))
        conn.commit()

    # Step 1: Language selection with enhanced welcome message
    lang_keyboard = InlineKeyboardMarkup()
    lang_keyboard.row(
        InlineKeyboardButton(text="فارسی 🇮🇷", callback_data="start_lang:fa"),
        InlineKeyboardButton(text="English 🇬🇧", callback_data="start_lang:en"),
    )

    welcome_text = """
🌐 خوش آمدید! / Welcome!

لطفاً زبان خود را انتخاب کنید:
Please select your language:

🇮🇷 فارسی | 🇬🇧 English
"""

    bot.send_message(chat_id, welcome_text, reply_markup=lang_keyboard)

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    """Admin panel command"""
    chat_id = message.chat.id
    
    if not is_admin(chat_id):
        return  # Silently ignore non-admin users
    
    # Create admin panel keyboard
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton(text="📊 آمار ربات", callback_data="admin:stats"),
        InlineKeyboardButton(text="👥 مدیریت کاربران", callback_data="admin:users"),
    )
    kb.row(
        InlineKeyboardButton(text="📢 پیام همگانی", callback_data="admin:broadcast"),
        InlineKeyboardButton(text="⚙️ تنظیمات", callback_data="admin:settings"),
    )
    kb.row(
        InlineKeyboardButton(text="🛡️ امنیت", callback_data="admin:security"),
        InlineKeyboardButton(text="🔧 نگهداری", callback_data="admin:maintenance"),
    )
    
    admin_stats = get_admin_stats()
    stats_text = f"""
👤 <b>پنل ادمین</b>

📊 <b>آمار کلی:</b>
├─ 👥 کاربران کل: {admin_stats['total_users']}
├─ 🚫 کاربران مسدود: {admin_stats['banned_users']}
├─ 💎 کاربران VIP: {admin_stats['vip_users']}
├─ 🌐 پروکسی‌های فعال: {admin_stats['active_proxies']}
├─ ❌ پروکسی‌های ناموفق: {admin_stats['proxy_failed']}
└─ 🔧 حالت عملیاتی: {admin_stats['operation_mode']}

دستور مورد نظر را انتخاب کنید:
"""
    
    bot.send_message(chat_id, stats_text, reply_markup=kb, parse_mode="HTML")

@bot.message_handler(commands=["ban"])
def cmd_ban(message):
    """Ban a user: /ban user_id reason"""
    chat_id = message.chat.id
    
    if not is_admin(chat_id):
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        bot.send_message(chat_id, "❌ Usage: /ban <user_id> <reason>")
        return
    
    try:
        target_id = int(args[1])
        reason = args[2]
        
        if is_admin(target_id):
            bot.send_message(chat_id, "❌ Cannot ban another admin!")
            return
        
        ban_user(target_id, reason, chat_id)
        bot.send_message(chat_id, f"✅ User {target_id} banned. Reason: {reason}")
        
        # Notify the banned user
        try:
            bot.send_message(target_id, f"❌ You have been banned.\nReason: {reason}")
        except:
            pass
    except ValueError:
        bot.send_message(chat_id, "❌ Invalid user ID")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")

@bot.message_handler(commands=["unban"])
def cmd_unban(message):
    """Unban a user: /unban user_id"""
    chat_id = message.chat.id
    
    if not is_admin(chat_id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        bot.send_message(chat_id, "❌ Usage: /unban <user_id>")
        return
    
    try:
        target_id = int(args[1])
        unban_user(target_id, chat_id)
        bot.send_message(chat_id, f"✅ User {target_id} unbanned.")
        
        # Notify the user
        try:
            bot.send_message(target_id, "✅ You have been unbanned.")
        except:
            pass
    except ValueError:
        bot.send_message(chat_id, "❌ Invalid user ID")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")

@bot.message_handler(commands=["setlevel"])
def cmd_setlevel(message):
    """Set user level: /setlevel user_id level [vip_days]"""
    chat_id = message.chat.id
    
    if not is_admin(chat_id):
        return
    
    args = message.text.split()
    if len(args) < 3:
        bot.send_message(chat_id, "❌ Usage: /setlevel <user_id> <level> [vip_days]\nLevels: 0=Regular, 1=Active, 2=VIP, 3=Admin")
        return
    
    try:
        target_id = int(args[1])
        level = int(args[2])
        vip_days = int(args[3]) if len(args) > 3 else None
        
        if level not in [0, 1, 2, 3]:
            bot.send_message(chat_id, "❌ Level must be 0-3")
            return
        
        set_user_level(target_id, level, chat_id, vip_days)
        
        level_names = {0: "Regular", 1: "Active", 2: "VIP", 3: "Admin"}
        bot.send_message(chat_id, f"✅ User {target_id} level set to {level_names[level]}")
    except ValueError:
        bot.send_message(chat_id, "❌ Invalid parameters")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")

@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(message):
    """Broadcast message to all users"""
    chat_id = message.chat.id
    
    if not is_admin(chat_id):
        return
    
    # Get the message to broadcast (reply to a message or use text after command)
    if message.reply_to_message:
        broadcast_text = message.reply_to_message.text or message.reply_to_message.caption
    else:
        broadcast_text = message.text.replace("/broadcast", "").strip()
    
    if not broadcast_text:
        bot.send_message(chat_id, "❌ Usage: Reply to a message with /broadcast or use /broadcast <text>")
        return
    
    bot.send_message(chat_id, "⏳ Sending broadcast message...")
    
    sent, failed = broadcast_message(broadcast_text, chat_id)
    
    bot.send_message(chat_id, f"✅ Broadcast completed!\nSent: {sent}\nFailed: {failed}")

@bot.message_handler(commands=["lang"])
def cmd_lang(message):
    chat_id = message.chat.id
    if not is_member(chat_id):
        bot.send_message(chat_id, tr(chat_id, "must_join", chan=CHANNEL_USERNAME), reply_markup=join_keyboard(chat_id))
        return
    bot.send_message(chat_id, tr(chat_id, "start"), reply_markup=lang_keyboard())

@bot.message_handler(commands=["quality"])
def cmd_quality(message):
    chat_id = message.chat.id
    if not is_member(chat_id):
        bot.send_message(chat_id, tr(chat_id, "must_join", chan=CHANNEL_USERNAME), reply_markup=join_keyboard(chat_id))
        return
    bot.send_message(chat_id, tr(chat_id, "quality_prompt"), reply_markup=sc_quality_keyboard(chat_id))

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    chat_id = message.chat.id
    if not is_member(chat_id):
        bot.send_message(chat_id, tr(chat_id, "must_join", chan=CHANNEL_USERNAME), reply_markup=join_keyboard(chat_id))
        return

    send_stats_main(chat_id)

@bot.message_handler(commands=["search"])
def cmd_search(message):
    chat_id = message.chat.id
    if not is_member(chat_id):
        bot.send_message(chat_id, tr(chat_id, "must_join", chan=CHANNEL_USERNAME), reply_markup=join_keyboard(chat_id))
        return
    query = message.text.replace("/search", "").strip()
    if not query:
        bot.send_message(chat_id, tr(chat_id, "search_prompt"))
        return
    do_search(chat_id, query)

def do_search(chat_id, query):
    lang = get_user_lang(chat_id) or "en"

    initial_msg = bot.send_message(chat_id, tr(chat_id, "searching"))
    msg_id = initial_msg.message_id

    tmpdir = tempfile.mkdtemp(prefix="scsrch_")
    try:
        ydl_opts = {
            "quiet": True, "no_warnings": True, "extract_flat": True,
            "simulate": True, "skip_download": True,
            "socket_timeout": 15, "extractor_retries": 2,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_query = f"scsearch15:{query}"
            info = ydl.extract_info(search_query, download=False)

            entries = info.get("entries") or []
            choices = []

            if entries:
                bot.edit_message_text(tr(chat_id, "searching_with_count", count=len(entries)), chat_id, msg_id)

                for i, e in enumerate(entries):
                    if e:
                        title = e.get("title")
                        if not title or title == "Unknown Title":
                            url_text = e.get("webpage_url", e.get("url", ""))
                            if url_text:
                                import re
                                url_match = re.search(r'/([^/]+)(?:\?|$)', url_text)
                                if url_match:
                                    title = url_match.group(1).replace('-', ' ').replace('_', ' ').title()

                        artist = extract_artist(e)
                        if not artist or artist == "unknown":
                            if title and " - " in title:
                                artist = title.split(" - ")[0].strip()
                                title = title.split(" - ", 1)[1].strip()

                        final_title = title if title else f"Track {i+1}"
                        final_artist = artist if artist else "Unknown Artist"

                        choices.append({
                            "title": final_title, "artist": final_artist,
                            "url": e.get("webpage_url", ""), "duration": e.get("duration", 0),
                            "thumb": e.get("thumbnail"),
                        })

                        if (i + 1) % 5 == 0:
                            bot.edit_message_text(tr(chat_id, "processing_results") + f" ({i+1}/{len(entries)})", chat_id, msg_id)

        if not choices:
            bot.edit_message_text(tr(chat_id, "no_results_found"), chat_id, msg_id)
            return

        save_search_choices(chat_id, choices)

        bot.edit_message_text(tr(chat_id, "search_results_found", count=len(choices)), chat_id, msg_id)

        kb = create_paginated_keyboard(choices, chat_id, 0, 10, "search")
        bot.send_message(chat_id, tr(chat_id, "pick_from_results"), reply_markup=kb)

    except Exception as e:
        bot.edit_message_text(tr(chat_id, "error", err=str(e)), chat_id, msg_id)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# ===== Callbacks =====
@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    chat_id = call.message.chat.id
    data = call.data or ""
    lang = get_user_lang(chat_id) or "en"

    # Handle admin panel callbacks
    if data.startswith("admin:"):
        handle_admin_callback(call)
        return

    # Handle initial language selection
    if data.startswith("start_lang:"):
        _, lang = data.split(":", 1)
        if lang in LANGS:
            set_user_lang(chat_id, lang)
            bot.answer_callback_query(call.id, f"Language set to {lang}")

            if not is_member(chat_id):
                join_keyboard = InlineKeyboardMarkup()
                join_keyboard.row(InlineKeyboardButton(text="بشم، اومدم 👋", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"))

                if lang == "fa":
                    msg_text = f"برای استفاده از ربات، لطفاً عضو کانال {CHANNEL_USERNAME} شوید.\n\nبعد از عضویت روی /start بزنید:"
                else:
                    msg_text = f"To use the bot, please join {CHANNEL_USERNAME}.\n\nAfter joining, press /start:"

                bot.send_message(chat_id, msg_text, reply_markup=join_keyboard)
            else:
                send_main_messages(chat_id)
        return

    # Handle YouTube quality selection
    if data.startswith("yt_quality:"):
        handle_youtube_quality_selection(call)
        return

    # Handle YouTube Shorts selection
    if data.startswith("yt_shorts:"):
        handle_youtube_shorts_selection(call)
        return

    # Handle statistics callbacks - with message editing
    if data.startswith("stats:"):
        _, action = data.split(":", 1)

        if action == "main":
            edit_stats_main(chat_id, call.message.message_id)
        elif action == "close":
            # Delete stats message
            try:
                bot.delete_message(chat_id, call.message.message_id)
            except Exception as e:
                print(f"Error deleting stats message: {e}")
        elif action == "top_users_all":
            send_top_users_stats(chat_id, call.message.message_id, 'all')
        elif action == "top_users_daily":
            send_top_users_stats(chat_id, call.message.message_id, 'daily')
        elif action == "top_users_weekly":
            send_top_users_stats(chat_id, call.message.message_id, 'weekly')
        elif action == "top_platforms_all":
            send_top_platforms_stats(chat_id, call.message.message_id, 'all')
        elif action == "top_platforms_daily":
            send_top_platforms_stats(chat_id, call.message.message_id, 'daily')
        elif action == "top_platforms_weekly":
            send_top_platforms_stats(chat_id, call.message.message_id, 'weekly')

        bot.answer_callback_query(call.id)
        return

    # Handle regular callbacks
    if data.startswith("lang:"):
        _, lang = data.split(":", 1)
        if lang in LANGS:
            set_user_lang(chat_id, lang)
            bot.answer_callback_query(call.id, tr(chat_id, "lang_set", lang=lang))
            send_features_message(chat_id)
    elif data.startswith("quality:"):
        _, q = data.split(":", 1)
        if q in ("high", "low"):
            set_user_quality(chat_id, q)
            bot.answer_callback_query(call.id, tr(chat_id, "quality_set", q=q))
    elif data.startswith("pick:"):
        idx_str = data.split(":", 1)[1]
        try:
            idx = int(idx_str)
        except Exception:
            bot.answer_callback_query(call.id, "Invalid choice")
            return
        choice = get_search_choice(chat_id, idx)
        bot.answer_callback_query(call.id, "OK")
        if choice:
            handle_download_soundcloud(chat_id, choice["url"])
        else:
            bot.send_message(chat_id, tr(chat_id, "error", err="choice expired"))
    elif data.startswith("playlist_pick:"):
        idx_str = data.split(":", 1)[1]
        try:
            idx = int(idx_str)
        except Exception:
            bot.answer_callback_query(call.id, "Invalid choice")
            return
        choice = get_playlist_choice(chat_id, idx)
        bot.answer_callback_query(call.id, "OK")
        if choice:
            handle_download_soundcloud(chat_id, choice["url"])
        else:
            bot.send_message(chat_id, tr(chat_id, "error", err="choice expired"))
    elif data.startswith("search_page:"):
        page_str = data.split(":", 1)[1]
        try:
            page = int(page_str)
        except Exception:
            bot.answer_callback_query(call.id, "Invalid page")
            return

        with db_pool.get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT url, title, artist, duration FROM search_cache WHERE chat_id=? ORDER BY idx", (chat_id,))
            rows = c.fetchall()

        if rows:
            choices = []
            for row in rows:
                choices.append({"url": row[0], "title": row[1], "artist": row[2], "duration": row[3]})

            kb = create_paginated_keyboard(choices, chat_id, page, 10, "search")
            bot.edit_message_text(tr(chat_id, "pick_from_results"), call.message.chat.id, call.message.message_id, reply_markup=kb)

        bot.answer_callback_query(call.id)
    elif data.startswith("playlist_page:"):
        page_str = data.split(":", 1)[1]
        try:
            page = int(page_str)
        except Exception:
            bot.answer_callback_query(call.id, "Invalid page")
            return

        with db_pool.get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT url, title, artist, duration FROM playlist_cache WHERE chat_id=? ORDER BY idx", (chat_id,))
            rows = c.fetchall()

        if rows:
            choices = []
            for row in rows:
                choices.append({"url": row[0], "title": row[1], "artist": row[2], "duration": row[3]})

            kb = create_paginated_keyboard(choices, chat_id, page, 10, "playlist")
            bot.edit_message_text(tr(chat_id, "playlist_song_selection"), call.message.chat.id, call.message.message_id, reply_markup=kb)

        bot.answer_callback_query(call.id)
    elif data == "noop":
        bot.answer_callback_query(call.id)

def send_main_messages(chat_id):
    """Send main bot messages after joining with enhanced features"""
    bot.send_message(chat_id, tr(chat_id, "send_link"))
    bot.send_message(chat_id, tr(chat_id, "quality_prompt"), reply_markup=sc_quality_keyboard(chat_id))
    send_features_message(chat_id)

# ===== Main message handler =====
@bot.message_handler(func=lambda m: True)
def handle_message(message):
    chat_id = message.chat.id

    # Check if user is banned
    if is_banned(chat_id):
        bot.send_message(chat_id, "❌ شما مسدود شده‌اید.\n❌ You have been banned.")
        return

    # If user hasn't selected language yet
    if not get_user_lang(chat_id) or get_user_lang(chat_id) not in LANGS:
        lang_keyboard = InlineKeyboardMarkup()
        lang_keyboard.row(
            InlineKeyboardButton(text="فارسی 🇮🇷", callback_data="start_lang:fa"),
            InlineKeyboardButton(text="English 🇬🇧", callback_data="start_lang:en"),
        )

        welcome_text = """
🌐 خوش آمدید! / Welcome!

لطفاً زبان خود را انتخاب کنید:
Please select your language:

🇮🇷 فارسی | 🇬🇧 English
"""

        bot.send_message(chat_id, welcome_text, reply_markup=lang_keyboard)
        return

    # Check membership for users who have selected language
    if not is_member(chat_id):
        join_keyboard = InlineKeyboardMarkup()
        join_keyboard.row(InlineKeyboardButton(text="بشم، اومدم 👋", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"))

        lang = get_user_lang(chat_id)
        if lang == "fa":
            msg_text = f"برای استفاده از ربات، لطفاً عضو کانال {CHANNEL_USERNAME} شوید.\n\nبعد از عضویت روی /start بزنید:"
        else:
            msg_text = f"To use the bot, please join {CHANNEL_USERNAME}.\n\nAfter joining, press /start:"

        bot.send_message(chat_id, msg_text, reply_markup=join_keyboard)
        return

    text = (message.text or "").strip()
    if not text:
        bot.reply_to(message, tr(chat_id, "invalid_link"))
        return

    if text.startswith("http"):
        final_url = resolve_url(text)
        try:
            # Delete user message after starting download
            bot.delete_message(chat_id, message.message_id)
        except Exception as e:
            print(f"Error deleting user message: {e}")

        if "soundcloud.com" in final_url:
            handle_download_soundcloud(chat_id, final_url)
        elif "pinterest.com" in final_url or "pin.it" in final_url:
            handle_download_pinterest(chat_id, final_url)
        elif "instagram.com" in final_url or "instagr.am" in final_url:
            handle_download_instagram(chat_id, final_url)
        elif "youtube.com" in final_url or "youtu.be" in final_url:
            handle_download_youtube(chat_id, final_url)
        elif "tiktok.com" in final_url:
            handle_download_tiktok(chat_id, final_url)
        elif "twitter.com" in final_url or "x.com" in final_url or "t.co" in final_url:
            handle_download_twitter(chat_id, final_url)
        else:
            bot.send_message(chat_id, tr(chat_id, "error", err="Unsupported link"))
    else:
        do_search(chat_id, text)

# ===== File Senders =====
def build_sc_caption(chat_id, item, original_url=None):
    """Wrapper for backward compatibility"""
    return caption_builder.build_caption(chat_id, "SoundCloud", item, original_url)

def build_youtube_caption(chat_id, item, original_url=None, audio_only=False):
    """Wrapper for backward compatibility"""
    return caption_builder.build_caption(chat_id, "YouTube", item, original_url, audio_only=audio_only)

def build_media_caption(chat_id, item, platform, original_url=None):
    """Wrapper for backward compatibility"""
    return caption_builder.build_caption(chat_id, platform, item, original_url)

def send_sc_item(chat_id, item, original_url=None):
    caption = build_sc_caption(chat_id, item, original_url)

    # Send thumbnail first for SoundCloud (allowed platform)
    if item.get("thumb_file"):
        try:
            with open(item["thumb_file"], "rb") as tf:
                bot.send_photo(chat_id, tf, caption=tr(chat_id, "cover_sent"))
        except Exception:
            pass

    safe_fp = force_audio_extension(item["filepath"])

    if item["size"] <= TELEGRAM_UPLOAD_LIMIT:
        with open(safe_fp, "rb") as f:
            kwargs = {
                "caption": caption, "performer": item["artist"], "title": item["title"],
                "duration": item["duration"] or None,
            }
            if item.get("thumb_file"):
                try:
                    with open(item["thumb_file"], "rb") as tf:
                        kwargs["thumb"] = tf
                        bot.send_audio(chat_id, f, **kwargs)
                except Exception:
                    bot.send_audio(chat_id, f, **kwargs)
            else:
                bot.send_audio(chat_id, f, **kwargs)
        add_stats_with_platform(chat_id, "SoundCloud", "audio", item["size"])
    else:
        bot.send_message(chat_id, tr(chat_id, "error", err=f"File too large: {human_size(item['size'])}"))

def send_youtube_item(chat_id, item, original_url=None, audio_only=False):
    caption = build_youtube_caption(chat_id, item, original_url, audio_only)

    # Send thumbnail first for YouTube regular videos (allowed platform)
    if item.get("thumb_file") and not audio_only:
        try:
            with open(item["thumb_file"], "rb") as tf:
                bot.send_photo(chat_id, tf, caption=tr(chat_id, "youtube_preview"))
        except Exception:
            pass

    if item["size"] <= TELEGRAM_UPLOAD_LIMIT:
        if audio_only:
            # Send as audio file
            with open(item["filepath"], "rb") as f:
                kwargs = {
                    "caption": caption,
                    "title": item["title"],
                    "duration": item["duration"] or None,
                }
                if item.get("thumb_file"):
                    try:
                        with open(item["thumb_file"], "rb") as tf:
                            kwargs["thumb"] = tf
                            bot.send_audio(chat_id, f, **kwargs)
                    except Exception:
                        bot.send_audio(chat_id, f, **kwargs)
                else:
                    bot.send_audio(chat_id, f, **kwargs)
            add_stats_with_platform(chat_id, "YouTube", "audio", item["size"])
        else:
            # Send as video file
            with open(item["filepath"], "rb") as f:
                kwargs = {
                    "caption": caption,
                    "duration": item.get("duration") or None,
                    "supports_streaming": True,
                }
                if item.get("thumb_file"):
                    try:
                        with open(item["thumb_file"], "rb") as tf:
                            kwargs["thumb"] = tf
                            bot.send_video(chat_id, f, **kwargs)
                    except Exception:
                        bot.send_video(chat_id, f, **kwargs)
                else:
                    bot.send_video(chat_id, f, **kwargs)
            add_stats_with_platform(chat_id, "YouTube", "video", item["size"])
    else:
        bot.send_message(chat_id, tr(chat_id, "error", err=f"File too large: {human_size(item['size'])}"))

def send_youtube_short_item(chat_id, item, original_url=None, audio_only=False):
    """Send YouTube Short WITHOUT thumbnail"""
    caption = build_youtube_caption(chat_id, item, original_url, audio_only)

    # NO thumbnail for YouTube Shorts
    if item["size"] <= TELEGRAM_UPLOAD_LIMIT:
        if audio_only:
            # Send as audio file
            with open(item["filepath"], "rb") as f:
                kwargs = {
                    "caption": caption,
                    "title": item["title"],
                    "duration": item["duration"] or None,
                }
                bot.send_audio(chat_id, f, **kwargs)
            add_stats_with_platform(chat_id, "YouTube", "audio", item["size"])
        else:
            # Send as video file WITHOUT thumbnail
            with open(item["filepath"], "rb") as f:
                kwargs = {
                    "caption": caption,
                    "duration": item.get("duration") or None,
                    "supports_streaming": True,
                }
                bot.send_video(chat_id, f, **kwargs)
            add_stats_with_platform(chat_id, "YouTube", "video", item["size"])
    else:
        bot.send_message(chat_id, tr(chat_id, "error", err=f"File too large: {human_size(item['size'])}"))

def send_media_item(chat_id, item, platform, original_url=None):
    caption = build_media_caption(chat_id, item, platform, original_url)
    ext = (item.get("ext") or "").lower()
    size = item.get("size", 0)

    if size > TELEGRAM_UPLOAD_LIMIT:
        bot.send_message(chat_id, tr(chat_id, "error", err=f"File too large: {human_size(size)}"))
        return

    # NO thumbnail sending for non-allowed platforms (Pinterest, Instagram, TikTok, Twitter)
    # Only YouTube and SoundCloud are allowed to send thumbnails

    if ext in ["jpg", "jpeg", "png", "webp"]:
        try:
            with open(item["filepath"], "rb") as f:
                bot.send_photo(chat_id, f, caption=caption)
        except Exception as e:
            bot.send_message(chat_id, tr(chat_id, "error", err=str(e)))
        add_stats_with_platform(chat_id, platform, "image", size)
    else:
        # Ensure video has .mp4 extension
        video_path = force_video_extension(item["filepath"])
        
        # Video
        try:
            with open(video_path, "rb") as f:
                kwargs = {
                    "caption": caption, 
                    "duration": item.get("duration") or None, 
                    "supports_streaming": True,
                }
                bot.send_video(chat_id, f, **kwargs)
        except Exception as e:
            bot.send_message(chat_id, tr(chat_id, "error", err=str(e)))
        add_stats_with_platform(chat_id, platform, "video", size)

# ===== Flask Web Server for Replit =====
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Enhanced Telegram Bot is Running!"

@app.route('/health')
def health():
    return {"status": "healthy", "timestamp": time.time(), "service": "Enhanced Telegram Downloader Bot", "version": "3.1"}

@app.route('/ping')
def ping():
    return "pong - {}".format(time.strftime('%Y-%m-%d %H:%M:%S'))

@app.route('/status')
def status():
    import datetime
    return {"status": "running", "service": "Enhanced Telegram Downloader Bot", "timestamp": datetime.datetime.now().isoformat(), "platform": "Replit", "uptime": "active"}

@app.route('/keepalive')
def keepalive():
    return {"status": "alive", "time": time.time()}

# ===== Replit Keep-Alive System =====
def setup_replit_keepalive():
    """Complete Replit keeper setup"""
    try:
        print("Setting up Replit keep-alive system...")

        def replit_pinger():
            import requests
            import time

            print("Waiting for Flask server to start...")
            time.sleep(10)

            base_urls = [f"http://localhost:{PORT}", "http://localhost:5000"]
            endpoints = ['/', '/health', '/ping', '/status', '/keepalive']
            ping_count = 0

            while True:
                ping_count += 1
                success_count = 0

                for base_url in base_urls:
                    current_success = 0
                    print(f"Ping #{ping_count} with address: {base_url}")

                    for endpoint in endpoints:
                        try:
                            url = f"{base_url}{endpoint}"
                            response = requests.get(url, timeout=10)
                            if response.status_code == 200:
                                current_success += 1
                                success_count += 1
                                print(f"Ping #{ping_count} to {endpoint} successful")
                            else:
                                print(f"Ping #{ping_count} to {endpoint} with status: {response.status_code}")
                        except requests.exceptions.ConnectionError as e:
                            print(f"Connection error in ping #{ping_count} to {endpoint}: {e}")
                            break
                        except Exception as e:
                            print(f"Error in ping #{ping_count} to {endpoint}: {e}")

                    if current_success > 0:
                        break

                print(f"Ping #{ping_count}: {success_count}/{len(endpoints)} successful")

                sleep_time = 30 + (ping_count % 4) * 60
                print(f"Sleeping for {sleep_time} seconds...")
                time.sleep(sleep_time)

        pinger_thread = threading.Thread(target=replit_pinger, daemon=True)
        pinger_thread.start()
        print("Replit keep-alive system started")

    except Exception as e:
        print(f"Error setting up keep-alive: {e}")

def setup_uptimerobot_keepalive():
    """Simple system for UptimeRobot"""
    def simple_pinger():
        import time
        print("Waiting for Flask server to start for pulse...")
        time.sleep(10)

        ping_count = 0
        while True:
            ping_count += 1
            print(f"Bot active - Pulse #{ping_count} - Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            time.sleep(30)

    pinger_thread = threading.Thread(target=simple_pinger, daemon=True)
    pinger_thread.start()
    print("Simple pulse system started")

# ===== Main Entry Point =====
if __name__ == '__main__':
    print(f"Starting IRODENTV SC DOWNLOADER on port {PORT}...")
    print(f"Local URL: http://localhost:{PORT}")
    print(f"Health check: http://localhost:{PORT}/health")
    print(f"Cookies file available: {COOKIES_AVAILABLE}")

    def run_flask():
        app.run(host='0.0.0.0', port=PORT, debug=False)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    print("Waiting for Flask server to start...")
    time.sleep(5)

    setup_replit_keepalive()
    setup_uptimerobot_keepalive()

    print("Starting Enhanced Telegram Bot...")
    db_init()

    try:
        bot.polling(none_stop=True, timeout=60)
    except Exception as e:
        print(f"Bot error: {e}")
        import time
        time.sleep(10)
else:
    # For WSGI servers like Gunicorn
    db_init()
    def run_bot():
        try:
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            print(f"Bot error: {e}")

    bot_thread = threading.Thread(target=run_bot, daemon=True)
