"""Utility functions for the bot."""
import os
import re
import logging
from datetime import datetime
from config.settings import PREMIUM_EMOJIS

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def format_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    size = float(size_bytes)
    
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    
    return f"{size:.2f} {units[unit_index]}"

def format_duration(seconds: int) -> str:
    """Format duration in MM:SS or HH:MM:SS format."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"

def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file system usage."""
    # Remove invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Replace multiple spaces with single space
    filename = re.sub(r'\s+', ' ', filename)
    # Trim whitespace
    filename = filename.strip()
    # Limit length
    if len(filename) > 200:
        filename = filename[:200]
    return filename or "untitled"

def extract_url(text: str) -> str:
    """Extract URL from text."""
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    match = re.search(url_pattern, text)
    return match.group(0) if match else None

def is_valid_url(url: str) -> bool:
    """Check if URL is valid."""
    url_pattern = r'^https?://[^\s]+$'
    return bool(re.match(url_pattern, url))

def get_platform_from_url(url: str) -> str:
    """Detect platform from URL."""
    if not url:
        return None
    
    url_lower = url.lower()
    
    if 'soundcloud.com' in url_lower:
        return 'soundcloud'
    elif 'spotify.com' in url_lower or 'open.spotify.com' in url_lower:
        return 'spotify'
    elif 'deezer.com' in url_lower:
        return 'deezer'
    elif 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'instagram.com' in url_lower:
        return 'instagram'
    elif 'tiktok.com' in url_lower:
        return 'tiktok'
    elif 'twitter.com' in url_lower or 'x.com' in url_lower:
        return 'twitter'
    elif 'pinterest.com' in url_lower:
        return 'pinterest'
    
    return None

def create_progress_bar(current: int, total: int, width: int = 10) -> str:
    """Create a text-based progress bar."""
    if total == 0:
        return "[          ] 0%"
    
    percentage = min(100, int((current / total) * 100))
    filled = int((percentage / 100) * width)
    empty = width - filled
    
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {percentage}%"

def parse_spotify_url(url: str) -> dict:
    """Parse Spotify URL to extract type and ID."""
    patterns = {
        'track': r'spotify\.com/track/([a-zA-Z0-9]+)',
        'album': r'spotify\.com/album/([a-zA-Z0-9]+)',
        'playlist': r'spotify\.com/playlist/([a-zA-Z0-9]+)',
        'artist': r'spotify\.com/artist/([a-zA-Z0-9]+)',
    }
    
    for media_type, pattern in patterns.items():
        match = re.search(pattern, url)
        if match:
            return {'type': media_type, 'id': match.group(1)}
    
    return None

def parse_deezer_url(url: str) -> dict:
    """Parse Deezer URL to extract type and ID."""
    patterns = {
        'track': r'deezer\.com/track/(\d+)',
        'album': r'deezer\.com/album/(\d+)',
        'playlist': r'deezer\.com/playlist/(\d+)',
        'artist': r'deezer\.com/artist/(\d+)',
    }
    
    for media_type, pattern in patterns.items():
        match = re.search(pattern, url)
        if match:
            return {'type': media_type, 'id': match.group(1)}
    
    return None

def parse_soundcloud_url(url: str) -> dict:
    """Parse SoundCloud URL to extract type."""
    if '/sets/' in url:
        return {'type': 'playlist'}
    elif '/albums/' in url:
        return {'type': 'album'}
    else:
        return {'type': 'track'}

def get_emoji(name: str) -> str:
    """Get emoji by name from premium emojis dict."""
    return PREMIUM_EMOJIS.get(name, "•")

def format_number(num: int) -> str:
    """Format large numbers with K, M, B suffixes."""
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}B"
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(num)

def cleanup_old_files(directory: str, max_age_hours: int = 24):
    """Clean up files older than specified hours."""
    import time
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    
    if not os.path.exists(directory):
        return
    
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        try:
            if os.path.isfile(filepath):
                file_mtime = os.path.getmtime(filepath)
                if now - file_mtime > max_age_seconds:
                    os.remove(filepath)
                    logger.info(f"Cleaned up old file: {filepath}")
        except Exception as e:
            logger.error(f"Error cleaning up {filepath}: {e}")

def ensure_dir(directory: str):
    """Ensure directory exists."""
    os.makedirs(directory, exist_ok=True)

def get_temp_dir(user_id: int = None) -> str:
    """Get temporary directory for downloads."""
    base_dir = "downloads"
    ensure_dir(base_dir)
    
    if user_id:
        user_dir = os.path.join(base_dir, str(user_id))
        ensure_dir(user_dir)
        return user_dir
    
    return base_dir

def clear_user_temp(user_id: int):
    """Clear temporary files for a user."""
    import shutil
    user_dir = get_temp_dir(user_id)
    if os.path.exists(user_dir):
        shutil.rmtree(user_dir)
        os.makedirs(user_dir)
