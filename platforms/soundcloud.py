"""SoundCloud downloader module."""
import os
import yt_dlp
from utils.helpers import sanitize_filename, get_temp_dir, format_size, logger, get_emoji
from database.db_manager import add_download_history, increment_download_count
from config.settings import PREMIUM_EMOJIS

class SoundCloudDownloader:
    """Handle SoundCloud downloads."""
    
    def __init__(self):
        self.name = "soundcloud"
        self.display_name = "SoundCloud"
        self.emoji = "🧡"
    
    def extract_info(self, url: str) -> dict:
        """Extract information from SoundCloud URL."""
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
            logger.error(f"SoundCloud extract error: {e}")
            return None
    
    def _parse_info(self, info: dict) -> dict:
        """Parse yt-dlp info to our format."""
        if not info:
            return None
        
        result = {
            'platform': 'soundcloud',
            'type': 'track',
            'title': info.get('title', 'Unknown'),
            'artist': info.get('artist', info.get('uploader', 'Unknown')),
            'duration': info.get('duration', 0),
            'thumbnail': info.get('thumbnail', ''),
            'url': info.get('webpage_url', info.get('url', '')),
            'id': info.get('id', ''),
        }
        
        # Check if it's a playlist/album
        if info.get('_type') == 'playlist' or 'entries' in info:
            result['type'] = 'playlist'
            result['tracks'] = []
            
            for entry in info.get('entries', []):
                if entry:
                    track = {
                        'title': entry.get('title', 'Unknown'),
                        'artist': entry.get('artist', entry.get('uploader', 'Unknown')),
                        'duration': entry.get('duration', 0),
                        'url': entry.get('webpage_url', entry.get('url', '')),
                        'id': entry.get('id', ''),
                        'thumbnail': entry.get('thumbnail', info.get('thumbnail', '')),
                    }
                    result['tracks'].append(track)
            
            result['track_count'] = len(result['tracks'])
        
        return result
    
    def download_track(self, url: str, quality: str = '320', workdir: str = None) -> dict:
        """Download a single track from SoundCloud."""
        if not workdir:
            workdir = get_temp_dir()
        
        opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': quality if quality in ['128', '256', '320', 'flac'] else '320',
            }],
            'outtmpl': os.path.join(workdir, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                # Find the downloaded file
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
            logger.error(f"SoundCloud download error: {e}")
            return {'success': False, 'error': str(e)}
    
    def download_playlist(self, url: str, quality: str = '320', workdir: str = None, 
                         track_indices: list = None, callback=None) -> dict:
        """Download multiple tracks from a playlist/album."""
        if not workdir:
            workdir = get_temp_dir()
        
        # First get playlist info
        info = self.extract_info(url)
        if not info or info['type'] != 'playlist':
            return {'success': False, 'error': 'Invalid playlist URL'}
        
        tracks = info['tracks']
        if track_indices:
            tracks = [tracks[i] for i in track_indices if i < len(tracks)]
        
        results = {
            'success': True,
            'platform': 'soundcloud',
            'playlist_title': info['title'],
            'total_tracks': len(tracks),
            'downloaded': 0,
            'failed': 0,
            'files': [],
            'errors': [],
        }
        
        total_size = 0
        
        for idx, track in enumerate(tracks):
            if callback:
                callback(idx + 1, len(tracks), track['title'])
            
            try:
                track_opts = {
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': quality if quality in ['128', '256', '320', 'flac'] else '320',
                    }],
                    'outtmpl': os.path.join(workdir, f'%(title)s.%(ext)s'),
                    'quiet': True,
                    'no_warnings': True,
                    'noplaylist': True,
                }
                
                with yt_dlp.YoutubeDL(track_opts) as ydl:
                    track_info = ydl.extract_info(track['url'], download=True)
                    filename = ydl.prepare_filename(track_info)
                    filename = filename.replace('.webm', '.mp3').replace('.m4a', '.mp3')
                    
                    if os.path.exists(filename):
                        file_size = os.path.getsize(filename)
                        total_size += file_size
                        results['files'].append({
                            'path': filename,
                            'title': track['title'],
                            'size': file_size,
                        })
                        results['downloaded'] += 1
                    else:
                        results['failed'] += 1
                        results['errors'].append(f"Failed: {track['title']}")
                        
            except Exception as e:
                logger.error(f"Error downloading track {track['title']}: {e}")
                results['failed'] += 1
                results['errors'].append(f"{track['title']}: {str(e)}")
        
        results['total_size'] = total_size
        return results
    
    def search(self, query: str, limit: int = 10) -> list:
        """Search SoundCloud."""
        search_url = f"scsearch:{query}"
        opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'playlistend': limit,
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
            logger.error(f"SoundCloud search error: {e}")
            return []
    
    def get_quality_options(self) -> list:
        """Get available quality options."""
        return [
            {'value': '128', 'label': '128 kbps'},
            {'value': '256', 'label': '256 kbps'},
            {'value': '320', 'label': '320 kbps'},
            {'value': 'flac', 'label': 'FLAC (Lossless)'},
        ]
