"""YouTube downloader module."""
import os
import yt_dlp
from utils.helpers import sanitize_filename, get_temp_dir, format_size, logger
from database.db_manager import add_download_history, increment_download_count
from config.settings import PREMIUM_EMOJIS

class YouTubeDownloader:
    """Handle YouTube downloads."""
    
    def __init__(self):
        self.name = "youtube"
        self.display_name = "YouTube"
        self.emoji = "🔴"
    
    def extract_info(self, url: str) -> dict:
        """Extract information from YouTube URL."""
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
            logger.error(f"YouTube extract error: {e}")
            return None
    
    def _parse_info(self, info: dict) -> dict:
        """Parse yt-dlp info to our format."""
        if not info:
            return None
        
        is_shorts = 'shorts' in info.get('webpage_url', '').lower() or \
                    info.get('id', '') in self._get_shorts_cache()
        
        result = {
            'platform': 'youtube',
            'type': 'shorts' if is_shorts else 'video',
            'title': info.get('title', 'Unknown'),
            'artist': info.get('uploader', 'Unknown'),
            'duration': info.get('duration', 0),
            'thumbnail': info.get('thumbnail', ''),
            'url': info.get('webpage_url', info.get('url', '')),
            'id': info.get('id', ''),
            'is_shorts': is_shorts,
        }
        
        # Get available formats
        formats = info.get('formats', [])
        video_formats = {}
        
        for f in formats:
            if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                quality = f.get('format_note', f.get('resolution', 'unknown'))
                filesize = f.get('filesize') or f.get('filesize_approx', 0)
                if quality and quality not in video_formats:
                    video_formats[quality] = {'filesize': filesize, 'format_id': f.get('format_id')}
        
        result['available_qualities'] = video_formats
        
        return result
    
    def _get_shorts_cache(self) -> list:
        """Get cached shorts IDs (simplified)."""
        return []
    
    def download_video(self, url: str, quality: str = '720', workdir: str = None) -> dict:
        """Download a YouTube video."""
        if not workdir:
            workdir = get_temp_dir()
        
        opts = {
            'format': f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best',
            'merge_output_format': 'mp4',
            'outtmpl': os.path.join(workdir, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                
                # Find merged file
                for ext in ['mp4', 'mkv', 'webm']:
                    test_file = filename.replace('.webm', f'.{ext}').replace('.mkv', f'.{ext}')
                    if os.path.exists(test_file):
                        filename = test_file
                        break
                
                if os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    return {
                        'success': True,
                        'file_path': filename,
                        'file_size': file_size,
                        'title': info.get('title', 'Unknown'),
                        'quality': quality,
                        'thumbnail': info.get('thumbnail', ''),
                        'duration': info.get('duration', 0),
                    }
                
                return {'success': False, 'error': 'File not found after download'}
                
        except Exception as e:
            logger.error(f"YouTube download error: {e}")
            return {'success': False, 'error': str(e)}
    
    def download_audio(self, url: str, quality: str = '320', workdir: str = None) -> dict:
        """Download audio only from YouTube."""
        if not workdir:
            workdir = get_temp_dir()
        
        opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': quality if quality in ['128', '256', '320'] else '320',
            }],
            'outtmpl': os.path.join(workdir, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                filename = filename.replace('.webm', '.mp3').replace('.m4a', '.mp3')
                
                if os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    return {
                        'success': True,
                        'file_path': filename,
                        'file_size': file_size,
                        'title': info.get('title', 'Unknown'),
                        'quality': quality,
                    }
                
                return {'success': False, 'error': 'File not found after download'}
                
        except Exception as e:
            logger.error(f"YouTube audio download error: {e}")
            return {'success': False, 'error': str(e)}
    
    def search(self, query: str, limit: int = 10) -> list:
        """Search YouTube."""
        search_url = f"ytsearch{limit}:{query}"
        opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(search_url, download=False)
                results = []
                
                for entry in info.get('entries', []):
                    if entry:
                        results.append({
                            'title': entry.get('title', 'Unknown'),
                            'artist': entry.get('uploader', 'Unknown'),
                            'duration': entry.get('duration', 0),
                            'url': entry.get('url', ''),
                            'id': entry.get('id', ''),
                            'thumbnail': entry.get('thumbnail', ''),
                        })
                
                return results
        except Exception as e:
            logger.error(f"YouTube search error: {e}")
            return []
    
    def get_quality_options(self, is_video: bool = True) -> list:
        """Get available quality options."""
        if is_video:
            return [
                {'value': '2160', 'label': '4K (2160p)'},
                {'value': '1440', 'label': '2K (1440p)'},
                {'value': '1080', 'label': 'Full HD (1080p)'},
                {'value': '720', 'label': 'HD (720p)'},
                {'value': '480', 'label': 'SD (480p)'},
                {'value': '360', 'label': 'Low (360p)'},
            ]
        else:
            return [
                {'value': '128', 'label': '128 kbps'},
                {'value': '256', 'label': '256 kbps'},
                {'value': '320', 'label': '320 kbps'},
            ]
