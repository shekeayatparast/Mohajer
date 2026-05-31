"""Database models and connection management."""
import sqlite3
from contextlib import contextmanager
from config.settings import DB_PATH, USER_LEVELS
import threading

# Thread-local storage for connections
local_data = threading.local()

def get_connection():
    """Get a database connection from the pool."""
    if not hasattr(local_data, 'connections'):
        local_data.connections = []
    
    if local_data.connections:
        return local_data.connections.pop()
    
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def return_connection(conn):
    """Return a connection to the pool."""
    if hasattr(local_data, 'connections'):
        local_data.connections.append(conn)

@contextmanager
def db_cursor():
    """Context manager for database cursor."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        return_connection(conn)

def init_database():
    """Initialize all database tables."""
    with db_cursor() as c:
        # Users table
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language TEXT DEFAULT 'fa',
                search_platform TEXT DEFAULT 'soundcloud',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # User levels and quotas
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_levels (
                user_id INTEGER PRIMARY KEY,
                level INTEGER DEFAULT 0,
                daily_downloads INTEGER DEFAULT 0,
                download_count_reset DATE DEFAULT CURRENT_DATE,
                total_downloads INTEGER DEFAULT 0,
                total_bytes INTEGER DEFAULT 0,
                vip_expiry DATE,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Banned users
        c.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                banned_by INTEGER,
                expires_at TIMESTAMP,
                FOREIGN KEY (banned_by) REFERENCES users(user_id)
            )
        """)
        
        # Download history
        c.execute("""
            CREATE TABLE IF NOT EXISTS download_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                platform TEXT,
                url TEXT,
                title TEXT,
                file_size INTEGER,
                quality TEXT,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Search cache
        c.execute("""
            CREATE TABLE IF NOT EXISTS search_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                query TEXT,
                results BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        
        # YouTube quality cache
        c.execute("""
            CREATE TABLE IF NOT EXISTS youtube_quality_cache (
                video_id TEXT PRIMARY KEY,
                qualities BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # YouTube shorts cache
        c.execute("""
            CREATE TABLE IF NOT EXISTS youtube_shorts_cache (
                video_id TEXT PRIMARY KEY,
                is_shorts INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Playlist cache
        c.execute("""
            CREATE TABLE IF NOT EXISTS playlist_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                playlist_id TEXT,
                tracks BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        
        # Broadcast messages
        c.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_text TEXT,
                sent_by INTEGER,
                sent_to TEXT,
                sent_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sent_by) REFERENCES users(user_id)
            )
        """)
        
        # Proxy stats
        c.execute("""
            CREATE TABLE IF NOT EXISTS proxy_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proxy_url TEXT UNIQUE,
                platform TEXT,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                last_tested TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Admin logs
        c.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                target_user_id INTEGER,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (admin_id) REFERENCES users(user_id),
                FOREIGN KEY (target_user_id) REFERENCES users(user_id)
            )
        """)
        
        # Daily user stats
        c.execute("""
            CREATE TABLE IF NOT EXISTS daily_user_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date DATE,
                downloads INTEGER DEFAULT 0,
                bytes_downloaded INTEGER DEFAULT 0,
                platforms_used TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, date),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Bot settings
        c.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert default settings
        default_settings = [
            ('welcome_message', 'Welcome to the music bot!'),
            ('caption_signature', '\n🎵 Downloaded with Music Bot'),
            ('force_channel', ''),
            ('force_channel_enabled', '0'),
            ('max_download_size', '100'),
            ('max_video_duration', '60'),
            ('soundcloud_quality', '320'),
            ('operation_mode', 'normal')
        ]
        
        for key, value in default_settings:
            c.execute(
                "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
                (key, value)
            )
        
        # Create indexes for better performance
        c.execute("CREATE INDEX IF NOT EXISTS idx_users_language ON users(language)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_users_platform ON users(search_platform)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_download_history_user ON download_history(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_download_history_platform ON download_history(platform)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_daily_stats_user_date ON daily_user_stats(user_id, date)")

def create_user_if_not_exists(user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    """Create a new user if they don't exist."""
    with db_cursor() as c:
        c.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (user_id,)
        )
        if not c.fetchone():
            c.execute(
                """INSERT INTO users (user_id, username, first_name, last_name) 
                   VALUES (?, ?, ?, ?)""",
                (user_id, username, first_name, last_name)
            )
            c.execute(
                """INSERT INTO user_levels (user_id) VALUES (?)""",
                (user_id,)
            )

def get_user_level(user_id: int) -> int:
    """Get user's access level."""
    with db_cursor() as c:
        c.execute("SELECT level FROM user_levels WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        return result['level'] if result else 0

def set_user_level(user_id: int, level: int, days: int = None):
    """Set user's access level."""
    with db_cursor() as c:
        vip_expiry = None
        if level == 2 and days:  # VIP with expiry
            from datetime import datetime, timedelta
            vip_expiry = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
        
        c.execute(
            """UPDATE user_levels SET level = ?, vip_expiry = ? WHERE user_id = ?""",
            (level, vip_expiry, user_id)
        )

def get_user_quota(user_id: int) -> dict:
    """Get user's download quota."""
    level = get_user_level(user_id)
    quota = USER_LEVELS.get(level, USER_LEVELS[0]).copy()
    
    with db_cursor() as c:
        c.execute(
            """SELECT daily_downloads, download_count_reset 
               FROM user_levels WHERE user_id = ?""",
            (user_id,)
        )
        result = c.fetchone()
        if result:
            quota['used_today'] = result['daily_downloads']
            quota['reset_date'] = result['download_count_reset']
    
    return quota

def increment_download_count(user_id: int, file_size: int = 0, platform: str = ''):
    """Increment user's daily download count."""
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    
    with db_cursor() as c:
        # Reset if new day
        c.execute(
            """UPDATE user_levels 
               SET daily_downloads = 0, download_count_reset = ? 
               WHERE user_id = ? AND download_count_reset < ?""",
            (today, user_id, today)
        )
        
        # Increment count
        c.execute(
            """UPDATE user_levels 
               SET daily_downloads = daily_downloads + 1, 
                   total_downloads = total_downloads + 1,
                   total_bytes = total_bytes + ?
               WHERE user_id = ?""",
            (file_size, user_id)
        )
        
        # Update daily stats
        c.execute(
            """INSERT INTO daily_user_stats (user_id, date, downloads, bytes_downloaded, platforms_used)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(user_id, date) DO UPDATE SET
               downloads = downloads + 1,
               bytes_downloaded = bytes_downloaded + ?,
               platforms_used = CASE 
                   WHEN platforms_used LIKE '%{}%' THEN platforms_used
                   ELSE platforms_used || ',{}'
               END""",
            (user_id, today, file_size, platform, file_size, platform)
        )

def is_user_banned(user_id: int) -> tuple:
    """Check if user is banned. Returns (is_banned, reason)."""
    with db_cursor() as c:
        c.execute(
            """SELECT reason, expires_at FROM banned_users 
               WHERE user_id = ? AND (expires_at IS NULL OR expires_at > datetime('now'))""",
            (user_id,)
        )
        result = c.fetchone()
        if result:
            return True, result['reason']
        return False, None

def ban_user(user_id: int, reason: str, admin_id: int, days: int = None):
    """Ban a user."""
    from datetime import datetime, timedelta
    
    expires_at = None
    if days:
        expires_at = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    
    with db_cursor() as c:
        c.execute(
            """INSERT OR REPLACE INTO banned_users (user_id, reason, banned_by, expires_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, reason, admin_id, expires_at)
        )
        
        # Log admin action
        c.execute(
            """INSERT INTO admin_logs (admin_id, action, target_user_id, details)
               VALUES (?, 'ban', ?, ?)""",
            (admin_id, user_id, f"Reason: {reason}, Days: {days}")
        )

def unban_user(user_id: int, admin_id: int):
    """Unban a user."""
    with db_cursor() as c:
        c.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        
        # Log admin action
        c.execute(
            """INSERT INTO admin_logs (admin_id, action, target_user_id, details)
               VALUES (?, 'unban', ?, ?)""",
            (admin_id, user_id, "User unbanned")
        )

def log_admin_action(admin_id: int, action: str, target_user_id: int = None, details: str = ""):
    """Log an admin action."""
    with db_cursor() as c:
        c.execute(
            """INSERT INTO admin_logs (admin_id, action, target_user_id, details)
               VALUES (?, ?, ?, ?)""",
            (admin_id, action, target_user_id, details)
        )

def get_user_search_platform(user_id: int) -> str:
    """Get user's preferred search platform."""
    with db_cursor() as c:
        c.execute("SELECT search_platform FROM users WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        return result['search_platform'] if result else 'soundcloud'

def set_user_search_platform(user_id: int, platform: str):
    """Set user's preferred search platform."""
    with db_cursor() as c:
        c.execute(
            "UPDATE users SET search_platform = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
            (platform, user_id)
        )

def update_user_activity(user_id: int):
    """Update user's last active timestamp."""
    with db_cursor() as c:
        c.execute(
            "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
            (user_id,)
        )

def get_bot_setting(key: str, default: str = '') -> str:
    """Get a bot setting."""
    with db_cursor() as c:
        c.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
        result = c.fetchone()
        return result['value'] if result else default

def set_bot_setting(key: str, value: str):
    """Set a bot setting."""
    from datetime import datetime
    with db_cursor() as c:
        c.execute(
            """INSERT OR REPLACE INTO bot_settings (key, value, updated_at)
               VALUES (?, ?, ?)""",
            (key, value, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )

def add_download_history(user_id: int, platform: str, url: str, title: str, file_size: int, quality: str):
    """Add entry to download history."""
    with db_cursor() as c:
        c.execute(
            """INSERT INTO download_history (user_id, platform, url, title, file_size, quality)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, platform, url, title, file_size, quality)
        )

def get_all_users() -> list:
    """Get all users."""
    with db_cursor() as c:
        c.execute("SELECT * FROM users")
        return c.fetchall()

def get_user_stats(user_id: int) -> dict:
    """Get detailed user statistics."""
    with db_cursor() as c:
        # Basic info
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user_info = c.fetchone()
        
        # Level info
        c.execute("SELECT * FROM user_levels WHERE user_id = ?", (user_id,))
        level_info = c.fetchone()
        
        # Download count
        c.execute("SELECT COUNT(*) as count FROM download_history WHERE user_id = ?", (user_id,))
        download_count = c.fetchone()['count']
        
        return {
            'user': user_info,
            'level': level_info,
            'total_downloads': download_count
        }
