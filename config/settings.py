# Bot Configuration
BOT_TOKEN = "8382981392:AAEdQptMng0Zu2keWRMrfylq6wepvmULCbI"
API_ID = 29511794
API_HASH = "b1f92c1a4cd9e0cf9ce3ad88f0beb14d"

# Admin IDs (add your Telegram ID from @userinfobot)
ADMIN_IDS = [123456789]  # Replace with your actual ID

# Database settings
DB_PATH = "bot_database.db"

# Download settings
MAX_DOWNLOAD_SIZE_MB = 100
MAX_CONCURRENT_DOWNLOADS = 5
DOWNLOAD_TIMEOUT = 300

# Cache settings
CACHE_EXPIRY_HOURS = 24

# Proxy settings (optional)
PROXIES = []  # Add proxies like: ["socks5://user:pass@ip:port", ...]

# Premium emoji IDs (custom telegram emoji)
PREMIUM_EMOJIS = {
    "star": "⭐",
    "fire": "🔥",
    "diamond": "💎",
    "music": "🎵",
    "check": "✅",
    "cross": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
    "download": "📥",
    "search": "🔍",
    "settings": "⚙️",
    "admin": "🛡️",
    "vip": "👑",
    "free": "🆓",
    "playlist": "📋",
    "album": "💿",
    "quality": "🎼",
    "speed": "⚡",
    "user": "👤",
    "stats": "📊",
    "broadcast": "📢",
    "ban": "🚫",
    "unban": "✅",
    "help": "❓",
    "home": "🏠",
    "back": "🔙",
    "next": "➡️",
    "prev": "⬅️",
    "spotify": "🟢",
    "deezer": "💜",
    "soundcloud": "🧡",
    "youtube": "🔴",
    "instagram": "📸",
    "tiktok": "🎵",
    "twitter": "🐦",
    "pinterest": "📌"
}

# User levels and quotas
USER_LEVELS = {
    0: {"name": "Free", "daily_downloads": 5, "max_size_mb": 100},
    1: {"name": "Active", "daily_downloads": 15, "max_size_mb": 500},
    2: {"name": "VIP", "daily_downloads": 50, "max_size_mb": 2048},
    3: {"name": "Admin", "daily_downloads": -1, "max_size_mb": -1}  # -1 = unlimited
}

# Supported platforms
PLATFORMS = ["soundcloud", "spotify", "deezer", "youtube", "instagram", "tiktok", "twitter", "pinterest"]

# Default search platform
DEFAULT_SEARCH_PLATFORM = "soundcloud"

# Languages
LANGUAGES = ["fa", "en"]
DEFAULT_LANGUAGE = "fa"
