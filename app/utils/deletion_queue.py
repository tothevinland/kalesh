import asyncio
from typing import Dict, Any
from datetime import datetime, timezone
from app.utils.storage import r2_storage
from app.config import settings


class DeletionQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.processing = False
        self.semaphore = asyncio.Semaphore(getattr(settings, 'MAX_CONCURRENT_DELETIONS', 2))
        
    def start_worker(self):
        """Start the background worker task"""
        if not self.processing:
            self.processing = True
            # Schedule the worker as a background task
            asyncio.create_task(self._worker())
            print("File deletion worker started")
    
    async def add_to_queue(self, file_type: str, file_url: str):
        """Add a file to the deletion queue
        
        Args:
            file_type: Type of file ('regular' or 'hls')
            file_url: URL of the file to delete
        """
        await self.queue.put({
            'file_type': file_type,
            'file_url': file_url,
            'added_at': datetime.now(timezone.utc)
        })
        print(f"{file_type.capitalize()} file queued for deletion. Queue size: {self.queue.qsize()}")
    
    async def _worker(self):
        """Background worker that processes deletions with concurrency control"""
        print("Deletion worker started, waiting for files...")
        
        # Create a list to hold all running tasks
        tasks = []
        
        while self.processing:
            try:
                # Wait for next file with timeout
                task = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                
                file_type = task['file_type']
                file_url = task['file_url']
                
                print(f"Queueing {file_type} file for deletion... Queue remaining: {self.queue.qsize()}")
                
                # Create a task for processing this deletion
                delete_task = asyncio.create_task(self._delete_file_with_semaphore(file_type, file_url))
                
                # Add cleanup callback
                delete_task.add_done_callback(lambda t: tasks.remove(t) if t in tasks else None)
                tasks.append(delete_task)
                
                # Mark the queue item as done
                self.queue.task_done()
                
            except asyncio.TimeoutError:
                # Queue timeout - this is normal, just continue
                continue
            except Exception as e:
                print(f"Deletion worker error: {e}")
                continue
                
            # Clean up completed tasks
            tasks = [t for t in tasks if not t.done()]
    
    async def _delete_file_with_semaphore(self, file_type: str, file_url: str):
        """Delete a file with semaphore for concurrency control"""
        async with self.semaphore:
            print(f"Deleting {file_type} file...")
            try:
                if file_type == 'hls':
                    success = await r2_storage.delete_hls_content(file_url)
                else:  # regular file
                    success = await r2_storage.delete_file(file_url)
                
                if success:
                    print(f"{file_type.capitalize()} file deleted successfully")
                else:
                    print(f"Failed to delete {file_type} file: {file_url}")
            except Exception as delete_error:
                print(f"Error deleting file: {delete_error}")
    
    async def stop_worker(self):
        """Stop the background worker"""
        self.processing = False
        print("File deletion worker stopped")
    
    def get_queue_size(self) -> int:
        """Get current queue size"""
        return self.queue.qsize()


# Global queue instance
deletion_queue = DeletionQueue()
