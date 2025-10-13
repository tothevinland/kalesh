import subprocess
import tempfile
import os
from typing import Tuple, Optional


class VideoProcessor:
    @staticmethod
    async def process_video(video_path: str) -> Tuple[Optional[float], Optional[bytes]]:
        """
        Process video to extract duration and generate thumbnail
        Returns: (duration_in_seconds, thumbnail_bytes)
        """
        try:
            duration = await VideoProcessor.get_video_duration(video_path)
            thumbnail_bytes = await VideoProcessor.generate_thumbnail(video_path)
            return duration, thumbnail_bytes
        except Exception as e:
            # Return None values if processing fails
            return None, None

    @staticmethod
    async def get_video_duration(video_path: str) -> Optional[float]:
        """
        Extract video duration using FFprobe
        """
        try:
            cmd = [
                'ffprobe',
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
        """
        Generate video thumbnail at specified time offset
        Returns thumbnail as bytes
        """
        try:
            # Create temporary file for thumbnail
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_thumb:
                thumbnail_path = tmp_thumb.name

            cmd = [
                'ffmpeg',
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
                
                # Clean up temp file
                os.unlink(thumbnail_path)
                return thumbnail_bytes
            
            # Clean up if failed
            if os.path.exists(thumbnail_path):
                os.unlink(thumbnail_path)
            return None
        except Exception:
            # Clean up on exception
            if 'thumbnail_path' in locals() and os.path.exists(thumbnail_path):
                os.unlink(thumbnail_path)
            return None


video_processor = VideoProcessor()

