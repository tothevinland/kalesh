import subprocess
import tempfile
import os
import shutil
from typing import Tuple, Optional, Dict, List
from pathlib import Path


class VideoProcessor:
    
    # Quality presets for HLS streaming
    QUALITIES = {
        '1080p': {'width': 1920, 'height': 1080, 'bitrate': '5000k', 'audio_bitrate': '192k'},
        '720p': {'width': 1280, 'height': 720, 'bitrate': '2800k', 'audio_bitrate': '128k'},
        '480p': {'width': 854, 'height': 480, 'bitrate': '1400k', 'audio_bitrate': '128k'},
        '360p': {'width': 640, 'height': 360, 'bitrate': '800k', 'audio_bitrate': '96k'},
    }
    
    @staticmethod
    async def process_video_to_hls(video_path: str) -> Tuple[Optional[float], Optional[bytes], Optional[Dict]]:
        """
        Process video to HLS format with multiple qualities
        Returns: (duration, thumbnail_bytes, hls_data)
        hls_data = {
            'master_playlist': 'master.m3u8 content',
            'playlists': {
                '1080p': {'playlist': 'content', 'segments': [bytes]},
                '720p': {...},
            }
        }
        """
        try:
            duration = await VideoProcessor.get_video_duration(video_path)
            thumbnail_bytes = await VideoProcessor.generate_thumbnail(video_path)
            hls_data = await VideoProcessor.create_hls_streams(video_path)
            return duration, thumbnail_bytes, hls_data
        except Exception as e:
            print(f"HLS processing error: {e}")
            return None, None, None

    @staticmethod
    async def create_hls_streams(video_path: str) -> Optional[Dict]:
        """
        Create HLS streams with multiple qualities
        """
        try:
            # Create temp directory for HLS output
            temp_dir = tempfile.mkdtemp(prefix='hls_')
            
            hls_data = {
                'master_playlist': '',
                'playlists': {}
            }
            
            # Get video resolution to determine available qualities
            video_height = await VideoProcessor.get_video_height(video_path)
            
            # Filter qualities based on source resolution
            available_qualities = {}
            for quality, settings in VideoProcessor.QUALITIES.items():
                if video_height and video_height >= settings['height']:
                    available_qualities[quality] = settings
            
            # If no qualities available, use original resolution
            if not available_qualities:
                available_qualities = {'360p': VideoProcessor.QUALITIES['360p']}
            
            # Generate HLS for each quality
            master_playlist_lines = ['#EXTM3U', '#EXT-X-VERSION:3']
            
            for quality, settings in available_qualities.items():
                quality_dir = os.path.join(temp_dir, quality)
                os.makedirs(quality_dir, exist_ok=True)
                
                output_path = os.path.join(quality_dir, 'playlist.m3u8')
                
                # FFmpeg command for HLS
                cmd = [
                    '/usr/bin/ffmpeg',
                    '-i', video_path,
                    '-vf', f"scale={settings['width']}:{settings['height']}:force_original_aspect_ratio=decrease",
                    '-c:v', 'libx264',
                    '-b:v', settings['bitrate'],
                    '-c:a', 'aac',
                    '-b:a', settings['audio_bitrate'],
                    '-hls_time', '6',
                    '-hls_playlist_type', 'vod',
                    '-hls_segment_filename', os.path.join(quality_dir, 'segment_%03d.ts'),
                    '-f', 'hls',
                    output_path
                ]
                
                result = subprocess.run(cmd, capture_output=True, timeout=300)
                
                if result.returncode == 0:
                    # Read playlist
                    with open(output_path, 'r') as f:
                        playlist_content = f.read()
                    
                    # Read segments
                    segments = []
                    for segment_file in sorted(Path(quality_dir).glob('segment_*.ts')):
                        with open(segment_file, 'rb') as f:
                            segments.append({
                                'filename': segment_file.name,
                                'data': f.read()
                            })
                    
                    hls_data['playlists'][quality] = {
                        'playlist': playlist_content,
                        'segments': segments
                    }
                    
                    # Add to master playlist
                    bandwidth = int(settings['bitrate'].replace('k', '000'))
                    resolution = f"{settings['width']}x{settings['height']}"
                    master_playlist_lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={resolution}')
                    master_playlist_lines.append(f'{quality}/playlist.m3u8')
            
            # Create master playlist
            hls_data['master_playlist'] = '\n'.join(master_playlist_lines)
            
            # Clean up temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            return hls_data if hls_data['playlists'] else None
            
        except Exception as e:
            print(f"HLS creation error: {e}")
            if 'temp_dir' in locals():
                shutil.rmtree(temp_dir, ignore_errors=True)
            return None

    @staticmethod
    async def get_video_height(video_path: str) -> Optional[int]:
        """Get video height"""
        try:
            cmd = [
                '/usr/bin/ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=height',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip())
        except Exception:
            pass
        return None

    @staticmethod
    async def get_video_duration(video_path: str) -> Optional[float]:
        """Extract video duration using FFprobe"""
        try:
            cmd = [
                '/usr/bin/ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
            return None
        except Exception:
            return None

    @staticmethod
    async def generate_thumbnail(video_path: str, time_offset: str = "00:00:01") -> Optional[bytes]:
        """Generate video thumbnail at specified time offset"""
        try:
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_thumb:
                thumbnail_path = tmp_thumb.name

            cmd = [
                '/usr/bin/ffmpeg',
                '-ss', time_offset,
                '-i', video_path,
                '-vframes', '1',
                '-q:v', '2',
                '-y',
                thumbnail_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            
            if result.returncode == 0 and os.path.exists(thumbnail_path):
                with open(thumbnail_path, 'rb') as f:
                    thumbnail_bytes = f.read()
                os.unlink(thumbnail_path)
                return thumbnail_bytes
            
            if os.path.exists(thumbnail_path):
                os.unlink(thumbnail_path)
            return None
        except Exception:
            if 'thumbnail_path' in locals() and os.path.exists(thumbnail_path):
                os.unlink(thumbnail_path)
            return None


video_processor = VideoProcessor()

