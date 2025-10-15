import asyncio
import threading
from queue import Queue
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
        self.queue = Queue()
        self.processing = False
        self.worker_thread = None
        
    def start_worker(self):
        """Start the background worker thread"""
        if not self.processing:
            self.processing = True
            self.worker_thread = threading.Thread(target=self._worker, daemon=True)
            self.worker_thread.start()
            print("Video processing worker started")
    
    def add_to_queue(self, video_id: str, raw_video_url: str):
        """Add a video to the processing queue"""
        self.queue.put({
            'video_id': video_id,
            'raw_video_url': raw_video_url,
            'added_at': datetime.utcnow()
        })
        print(f"Video {video_id} added to processing queue. Queue size: {self.queue.qsize()}")
    
    def _worker(self):
        """Background worker that processes videos one by one"""
        print("Worker thread started, waiting for videos...")
        
        while self.processing:
            try:
                # Wait for next video (blocks until available)
                task = self.queue.get(timeout=1)
                
                video_id = task['video_id']
                raw_video_url = task['raw_video_url']
                
                print(f"Processing video {video_id}... Queue remaining: {self.queue.qsize()}")
                
                # Process the video
                asyncio.run(self._process_video(video_id, raw_video_url))
                
                self.queue.task_done()
                print(f"Video {video_id} processed successfully")
                
            except Exception as e:
                if "Empty" not in str(e):
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
            response = requests.get(raw_video_url, stream=True, timeout=300)
            response.raise_for_status()
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                temp_path = tmp_file.name
            
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
    
    def stop_worker(self):
        """Stop the background worker"""
        self.processing = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        print("Video processing worker stopped")
    
    def get_queue_size(self) -> int:
        """Get current queue size"""
        return self.queue.qsize()


# Global queue instance
video_queue = VideoProcessingQueue()

