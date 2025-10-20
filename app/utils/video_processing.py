import subprocess
import tempfile
import os
import shutil
from typing import Tuple, Optional, Dict, List
from pathlib import Path
from app.config import settings


class VideoProcessor:
    # Quality presets are now loaded from config settings
    @staticmethod
    def get_qualities():
        return {
            '1080p': {
                'width': 1920, 
                'height': 1080, 
                'bitrate': getattr(settings, 'VIDEO_BITRATE_1080P', '3500k'), 
                'audio_bitrate': '128k', 
                'crf': settings.VIDEO_CRF_1080P
            },
            '720p': {
                'width': 1280, 
                'height': 720, 
                'bitrate': getattr(settings, 'VIDEO_BITRATE_720P', '1800k'), 
                'audio_bitrate': '96k', 
                'crf': getattr(settings, 'VIDEO_CRF_720P', 24)
            },
            '480p': {
                'width': 854, 
                'height': 480, 
                'bitrate': getattr(settings, 'VIDEO_BITRATE_480P', '900k'), 
                'audio_bitrate': '96k', 
                'crf': getattr(settings, 'VIDEO_CRF_480P', 25)
            },
            '360p': {
                'width': 640, 
                'height': 360, 
                'bitrate': getattr(settings, 'VIDEO_BITRATE_360P', '500k'), 
                'audio_bitrate': '64k', 
                'crf': getattr(settings, 'VIDEO_CRF_360P', 26)
            },
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
            
            # Get qualities from config
            qualities = VideoProcessor.get_qualities()
            
            # Filter qualities based on source resolution
            available_qualities = {}
            for quality, settings in qualities.items():
                if video_height and video_height >= settings['height']:
                    available_qualities[quality] = settings
            
            # If no qualities available, use original resolution
            if not available_qualities:
                available_qualities = {'360p': qualities['360p']}
            
            # Generate HLS for each quality
            master_playlist_lines = ['#EXTM3U', '#EXT-X-VERSION:3']
            
            for quality, settings_dict in available_qualities.items():
                quality_dir = os.path.join(temp_dir, quality)
                os.makedirs(quality_dir, exist_ok=True)
                
                output_path = os.path.join(quality_dir, 'playlist.m3u8')
                
                # Video filter (just scaling, no watermark)
                video_filter = f"scale={settings_dict['width']}:{settings_dict['height']}:force_original_aspect_ratio=decrease"
                
                # FFmpeg command for HLS with improved compression
                # Check if we should use two-pass encoding
                use_two_pass = getattr(settings, 'USE_TWO_PASS_ENCODING', False)
                if use_two_pass:
                    # First pass - analyze video
                    first_pass_log = os.path.join(quality_dir, 'ffmpeg2pass')
                    first_pass_cmd = [
                        '/usr/bin/ffmpeg',
                    '-y',
                    '-i', video_path,
                    '-vf', video_filter,
                    '-c:v', 'libx264',
                    '-b:v', settings_dict['bitrate'],
                    '-maxrate', f"{int(float(settings_dict['bitrate'].replace('k', '')) * 1.5)}k",
                    '-bufsize', f"{int(float(settings_dict['bitrate'].replace('k', '')) * 2)}k",
                    '-crf', str(settings_dict['crf']),
                    '-preset', getattr(settings, 'VIDEO_COMPRESSION_PRESET', 'medium'),
                    '-x264opts', 'keyint=48:min-keyint=48:no-scenecut',
                    '-an',  # No audio in first pass
                    '-f', 'null',
                    '-pass', '1',
                    '-passlogfile', first_pass_log,
                    '/dev/null'
                ]
                
                    # Run first pass
                    first_pass_result = subprocess.run(first_pass_cmd, capture_output=True, timeout=300)
                    
                    if first_pass_result.returncode == 0:
                        # Second pass - encode with knowledge from first pass
                        cmd = [
                            '/usr/bin/ffmpeg',
                        '-i', video_path,
                        '-vf', video_filter,
                        '-c:v', 'libx264',
                        '-b:v', settings_dict['bitrate'],
                        '-maxrate', f"{int(float(settings_dict['bitrate'].replace('k', '')) * 1.5)}k",
                        '-bufsize', f"{int(float(settings_dict['bitrate'].replace('k', '')) * 2)}k",
                        '-crf', str(settings_dict['crf']),
                        '-preset', getattr(settings, 'VIDEO_COMPRESSION_PRESET', 'medium'),
                        '-x264opts', 'keyint=48:min-keyint=48:no-scenecut',
                        '-c:a', 'aac',
                        '-b:a', settings_dict['audio_bitrate'],
                        '-ac', '2',  # Stereo audio
                        '-ar', '44100',  # Audio sample rate
                        '-hls_time', '6',
                        '-hls_playlist_type', 'vod',
                        '-hls_segment_filename', os.path.join(quality_dir, 'segment_%03d.ts'),
                        '-f', 'hls',
                        output_path
                    ]
                    
                        result = subprocess.run(cmd, capture_output=True, timeout=600)  # Longer timeout for better compression
                    else:
                        # If first pass fails, fall back to single-pass encoding
                        cmd = [
                            '/usr/bin/ffmpeg',
                        '-i', video_path,
                        '-vf', video_filter,
                        '-c:v', 'libx264',
                        '-b:v', settings_dict['bitrate'],
                        '-crf', str(settings_dict['crf']),
                        '-preset', 'medium',  # Fallback preset
                        '-c:a', 'aac',
                        '-b:a', settings_dict['audio_bitrate'],
                        '-hls_time', '6',
                        '-hls_playlist_type', 'vod',
                        '-hls_segment_filename', os.path.join(quality_dir, 'segment_%03d.ts'),
                        '-f', 'hls',
                        output_path
                    ]
                    
                        result = subprocess.run(cmd, capture_output=True, timeout=300)
                else:
                    # Single-pass encoding if two-pass is disabled
                    # Get thread count from settings
                    thread_count = getattr(settings, 'FFMPEG_THREADS', 0)
                    thread_param = [] if thread_count == 0 else ['-threads', str(thread_count)]
                    
                    cmd = [
                        '/usr/bin/ffmpeg',
                        '-i', video_path,
                    ] + thread_param + [
                        '-vf', video_filter,
                        '-c:v', 'libx264',
                        '-b:v', settings_dict['bitrate'],
                        '-maxrate', f"{int(float(settings_dict['bitrate'].replace('k', '')) * 1.5)}k",
                        '-bufsize', f"{int(float(settings_dict['bitrate'].replace('k', '')) * 2)}k",
                        '-crf', str(settings_dict['crf']),
                        '-preset', getattr(settings, 'VIDEO_COMPRESSION_PRESET', 'medium'),
                        '-c:a', 'aac',
                        '-b:a', settings_dict['audio_bitrate'],
                        '-ac', '2',  # Stereo audio
                        '-ar', '44100',  # Audio sample rate
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
                    bandwidth = int(settings_dict['bitrate'].replace('k', '000'))
                    resolution = f"{settings_dict['width']}x{settings_dict['height']}"
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

            # Get thread count from settings
            thread_count = getattr(settings, 'FFMPEG_THREADS', 0)
            thread_param = [] if thread_count == 0 else ['-threads', str(thread_count)]
            
            cmd = [
                '/usr/bin/ffmpeg',
                '-ss', time_offset,
                '-i', video_path,
            ] + thread_param + [
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

