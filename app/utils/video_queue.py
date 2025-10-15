import asyncio
from typing import Optional
from datetime import datetime
from bson import ObjectId
import tempfile
import os
import requests
from app.utils.video_processing import video_processor
from app.utils.storage import r2_storage


class VideoProcessingQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.processing = False
        self.worker_task = None
        
    def start_worker(self):
        """Start the background worker task"""
        if not self.processing:
            self.processing = True
            # Schedule the worker as a background task
            asyncio.create_task(self._worker())
            print("Video processing worker started")
    
    async def add_to_queue(self, video_id: str, raw_video_url: str):
        """Add a video to the processing queue"""
        await self.queue.put({
            'video_id': video_id,
            'raw_video_url': raw_video_url,
            'added_at': datetime.utcnow()
        })
        print(f"Video {video_id} added to processing queue. Queue size: {self.queue.qsize()}")
    
    async def _worker(self):
        """Background worker that processes videos one by one"""
        print("Worker task started, waiting for videos...")
        
        while self.processing:
            try:
                # Wait for next video with timeout
                task = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                
                video_id = task['video_id']
                raw_video_url = task['raw_video_url']
                
                print(f"Processing video {video_id}... Queue remaining: {self.queue.qsize()}")
                
                # Process the video
                try:
                    await self._process_video(video_id, raw_video_url)
                    print(f"Video {video_id} processed successfully")
                except Exception as process_error:
                    print(f"Error processing video {video_id}: {process_error}")
                finally:
                    self.queue.task_done()
                
            except asyncio.TimeoutError:
                # Queue timeout - this is normal, just continue
                continue
            except Exception as e:
                print(f"Worker error: {e}")
                continue
    
    async def _process_video(self, video_id: str, raw_video_url: str):
        """Process a single video"""
        from app.database import get_database
        db = get_database()
        
        try:
            # Update status to processing
            await db.videos.update_one(
                {"_id": ObjectId(video_id)},
                {"$set": {"processing_status": "processing", "updated_at": datetime.utcnow()}}
            )
            
            # Download raw video to temp file
            temp_path = None
            
            def download_file():
                response = requests.get(raw_video_url, stream=True, timeout=300)
                response.raise_for_status()
                
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_file:
                    for chunk in response.iter_content(chunk_size=8192):
                        tmp_file.write(chunk)
                    return tmp_file.name
            
            # Run download in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            temp_path = await loop.run_in_executor(None, download_file)
            
            # Process video to HLS
            duration, thumbnail_bytes, hls_data = await video_processor.process_video_to_hls(temp_path)
            
            if not hls_data:
                raise Exception("Failed to process video to HLS")
            
            # Upload HLS content to R2
            playlist_url = await r2_storage.upload_hls_content(hls_data, video_id)
            
            # Upload thumbnail if generated
            thumbnail_url = None
            if thumbnail_bytes:
                thumbnail_filename = f"thumb_{video_id}.jpg"
                thumbnail_url = await r2_storage.upload_file(
                    thumbnail_bytes,
                    thumbnail_filename,
                    "image/jpeg"
                )
            
            # Update video with processed content
            await db.videos.update_one(
                {"_id": ObjectId(video_id)},
                {"$set": {
                    "playlist_url": playlist_url,
                    "thumbnail_url": thumbnail_url,
                    "duration": duration,
                    "processing_status": "completed",
                    "updated_at": datetime.utcnow()
                }}
            )
            
            # Clean up temp file
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            
            print(f"Video {video_id} processing completed successfully")
            
        except Exception as e:
            print(f"Error processing video {video_id}: {e}")
            
            # Update status to failed
            await db.videos.update_one(
                {"_id": ObjectId(video_id)},
                {"$set": {
                    "processing_status": "failed",
                    "processing_error": str(e),
                    "updated_at": datetime.utcnow()
                }}
            )
            
            # Clean up temp file on error
            if 'temp_path' in locals() and temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
    
    async def stop_worker(self):
        """Stop the background worker"""
        self.processing = False
        print("Video processing worker stopped")
    
    def get_queue_size(self) -> int:
        """Get current queue size"""
        return self.queue.qsize()


# Global queue instance
video_queue = VideoProcessingQueue()

