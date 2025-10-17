import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from typing import Optional, Dict, Any, Callable
import uuid
import asyncio
from functools import partial
from app.config import settings


class R2Storage:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=Config(signature_version='s3v4'),
            region_name='auto'
        )
        self.bucket_name = settings.R2_BUCKET_NAME
        self.public_url = settings.R2_PUBLIC_URL
        
        # Create a semaphore to limit concurrent S3 operations
        self.s3_semaphore = asyncio.Semaphore(getattr(settings, 'MAX_CONCURRENT_S3_OPS', 10))

    async def _run_in_executor(self, func: Callable, *args, **kwargs) -> Any:
        """Run a blocking function in a thread pool executor with semaphore to limit concurrency"""
        async with self.s3_semaphore:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, partial(func, *args, **kwargs))
    
    async def upload_file(self, file_data: bytes, filename: str, content_type: str) -> str:
        """
        Upload file to R2 and return the public URL
        """
        try:
            # Generate unique filename
            unique_filename = f"{uuid.uuid4()}_{filename}"
            
            # Upload to R2 in a thread pool to avoid blocking the event loop
            await self._run_in_executor(
                self.s3_client.put_object,
                Bucket=self.bucket_name,
                Key=unique_filename,
                Body=file_data,
                ContentType=content_type
            )
            
            # Return public URL
            file_url = f"{self.public_url}/{unique_filename}"
            return file_url
        except ClientError as e:
            print(f"R2 upload error: {str(e)}")
            raise Exception(f"Failed to upload file to R2")

    async def upload_hls_content(self, hls_data: Dict, video_id: str) -> str:
        """
        Upload HLS master playlist, variant playlists, and segments to R2
        Returns the master playlist URL
        """
        try:
            base_path = f"hls/{video_id}"
            
            # Upload master playlist
            master_key = f"{base_path}/master.m3u8"
            await self._run_in_executor(
                self.s3_client.put_object,
                Bucket=self.bucket_name,
                Key=master_key,
                Body=hls_data['master_playlist'].encode('utf-8'),
                ContentType='application/vnd.apple.mpegurl'
            )
            
            # Upload each quality variant
            for quality, data in hls_data['playlists'].items():
                # Upload playlist
                playlist_key = f"{base_path}/{quality}/playlist.m3u8"
                await self._run_in_executor(
                    self.s3_client.put_object,
                    Bucket=self.bucket_name,
                    Key=playlist_key,
                    Body=data['playlist'].encode('utf-8'),
                    ContentType='application/vnd.apple.mpegurl'
                )
                
                # Upload segments
                for segment in data['segments']:
                    segment_key = f"{base_path}/{quality}/{segment['filename']}"
                    await self._run_in_executor(
                        self.s3_client.put_object,
                        Bucket=self.bucket_name,
                        Key=segment_key,
                        Body=segment['data'],
                        ContentType='video/MP2T'
                    )
            
            # Return master playlist URL
            master_url = f"{self.public_url}/{master_key}"
            return master_url
            
        except ClientError as e:
            raise Exception(f"Failed to upload HLS content to R2: {e}")

    async def delete_file(self, file_url: str) -> bool:
        """
        Delete file from R2 given its public URL
        """
        try:
            # Extract filename from URL
            filename = file_url.split('/')[-1]
            
            # Delete from R2
            await self._run_in_executor(
                self.s3_client.delete_object,
                Bucket=self.bucket_name,
                Key=filename
            )
            return True
        except ClientError as e:
            # Don't raise error on delete failure
            return False
    
    async def delete_hls_content(self, playlist_url: str) -> bool:
        """
        Delete all HLS content (master playlist, variants, segments)
        """
        try:
            # Extract video_id from URL
            # Format: https://domain.com/hls/VIDEO_ID/master.m3u8
            parts = playlist_url.split('/')
            if 'hls' in parts:
                hls_index = parts.index('hls')
                if len(parts) > hls_index + 1:
                    video_id = parts[hls_index + 1]
                    base_path = f"hls/{video_id}/"
                    
                    # List all objects with this prefix
                    response = await self._run_in_executor(
                        self.s3_client.list_objects_v2,
                        Bucket=self.bucket_name,
                        Prefix=base_path
                    )
                    
                    # Delete all objects
                    if 'Contents' in response:
                        for obj in response['Contents']:
                            await self._run_in_executor(
                                self.s3_client.delete_object,
                                Bucket=self.bucket_name,
                                Key=obj['Key']
                            )
            return True
        except ClientError as e:
            return False


r2_storage = R2Storage()

