"""TikTok downloader module."""
import os
import yt_dlp
from utils.helpers import sanitize_filename, get_temp_dir, format_size, logger
from config.settings import PREMIUM_EMOJIS

class TikTokDownloader:
    """Handle TikTok downloads (without watermark)."""
    
    def __init__(self):
        self.name = "tiktok"
        self.display_name = "TikTok"
        self.emoji = "🎵"
    
    def extract_info(self, url: str) -> dict:
        """Extract information from TikTok URL."""
        opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return self._parse_info(info)
        except Exception as e:
            logger.error(f"TikTok extract error: {e}")
            return None
    
    def _parse_info(self, info: dict) -> dict:
        """Parse yt-dlp info to our format."""
        if not info:
            return None
        
        result = {
            'platform': 'tiktok',
            'type': 'video',
            'title': info.get('title', 'TikTok Video'),
            'artist': info.get('uploader', 'Unknown'),
            'duration': info.get('duration', 0),
            'thumbnail': info.get('thumbnail', ''),
            'url': info.get('webpage_url', info.get('url', '')),
            'id': info.get('id', ''),
            'description': info.get('description', ''),
        }
        
        return result
    
    def download(self, url: str, workdir: str = None, no_watermark: bool = True) -> dict:
        """Download TikTok video (optionally without watermark)."""
        if not workdir:
            workdir = get_temp_dir()
        
        opts = {
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'outtmpl': os.path.join(workdir, '%(title)s_%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
        
        # Try to get no-watermark version first
        if no_watermark:
            opts['format'] = 'best[quality*=h264]/best'
        
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                
                # Find the actual file
                for ext in ['mp4', 'webm']:
                    test_file = filename.rsplit('.', 1)[0] + f'.{ext}'
                    if os.path.exists(test_file):
                        filename = test_file
                        break
                
                if os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    
                    return {
                        'success': True,
                        'file_path': filename,
                        'file_size': file_size,
                        'title': info.get('title', 'TikTok Video'),
                        'no_watermark': no_watermark,
                        'thumbnail': info.get('thumbnail', ''),
                        'description': info.get('description', ''),
                    }
                
                return {'success': False, 'error': 'File not found after download'}
                
        except Exception as e:
            logger.error(f"TikTok download error: {e}")
            return {'success': False, 'error': str(e)}
