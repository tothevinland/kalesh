#!/usr/bin/env python
"""
Local Video Upload Script
Upload videos directly to MongoDB + R2 without using the API
"""
import os
import asyncio
from pathlib import Path
from datetime import datetime
import subprocess
import tempfile
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

import boto3
from botocore.client import Config
from motor.motor_asyncio import AsyncIOMotorClient
import uuid

# Configuration from .env
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "kalesh_db")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL")

# Directory containing videos to upload
VIDEOS_DIR = "videos"

# Your user info (from login response)
DEFAULT_UPLOADER_ID = "68ec77193d0964cb1a9dae53"
DEFAULT_USERNAME = "9330"


class VideoUploader:
    def __init__(self):
        # Setup R2
        self.s3_client = boto3.client(
            's3',
            endpoint_url=R2_ENDPOINT_URL,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            config=Config(signature_version='s3v4'),
            region_name='auto'
        )
        
        # Setup MongoDB
        self.mongo_client = None
        self.db = None
    
    async def connect_db(self):
        """Connect to MongoDB"""
        self.mongo_client = AsyncIOMotorClient(MONGODB_URL)
        self.db = self.mongo_client[MONGODB_DB_NAME]
        print(f"✓ Connected to MongoDB: {MONGODB_DB_NAME}")
    
    async def close_db(self):
        """Close MongoDB connection"""
        if self.mongo_client:
            self.mongo_client.close()
    
    def get_video_duration(self, video_path: str) -> float:
        """Extract video duration using FFprobe"""
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
        except Exception as e:
            print(f"  ⚠ Could not extract duration: {e}")
        return None
    
    def generate_thumbnail(self, video_path: str) -> bytes:
        """Generate video thumbnail at 1 second mark"""
        try:
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_thumb:
                thumbnail_path = tmp_thumb.name
            
            cmd = [
                'ffmpeg',
                '-ss', '00:00:01',
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
        except Exception as e:
            print(f"  ⚠ Could not generate thumbnail: {e}")
        return None
    
    def upload_to_r2(self, file_data: bytes, filename: str, content_type: str) -> str:
        """Upload file to R2"""
        try:
            unique_filename = f"{uuid.uuid4()}_{filename}"
            
            self.s3_client.put_object(
                Bucket=R2_BUCKET_NAME,
                Key=unique_filename,
                Body=file_data,
                ContentType=content_type
            )
            
            file_url = f"{R2_PUBLIC_URL}/{unique_filename}"
            return file_url
        except Exception as e:
            raise Exception(f"Failed to upload to R2: {e}")
    
    async def upload_video(self, video_path: Path, title: str = None, description: str = None, tags: list = None):
        """Process and upload a single video"""
        print(f"\n📹 Processing: {video_path.name}")
        
        # Read video file
        with open(video_path, 'rb') as f:
            video_content = f.read()
        
        file_size = len(video_content)
        print(f"  📊 Size: {file_size / 1024 / 1024:.2f} MB")
        
        # Process video
        print(f"  🎬 Extracting duration...")
        duration = self.get_video_duration(str(video_path))
        if duration:
            print(f"  ⏱ Duration: {duration:.2f} seconds")
        
        print(f"  🖼 Generating thumbnail...")
        thumbnail_bytes = self.generate_thumbnail(str(video_path))
        if thumbnail_bytes:
            print(f"  ✓ Thumbnail generated")
        
        # Upload video to R2
        print(f"  ☁️ Uploading video to R2...")
        video_url = self.upload_to_r2(
            video_content,
            video_path.name,
            'video/mp4'
        )
        print(f"  ✓ Video uploaded: {video_url}")
        
        # Upload thumbnail to R2
        thumbnail_url = None
        if thumbnail_bytes:
            print(f"  ☁️ Uploading thumbnail to R2...")
            thumbnail_filename = f"thumb_{video_path.stem}.jpg"
            thumbnail_url = self.upload_to_r2(
                thumbnail_bytes,
                thumbnail_filename,
                'image/jpeg'
            )
            print(f"  ✓ Thumbnail uploaded: {thumbnail_url}")
        
        # Prepare metadata
        if not title:
            title = video_path.stem.replace('_', ' ').replace('-', ' ').title()
        
        if not tags:
            tags = []
        
        video_doc = {
            "uploader_id": DEFAULT_UPLOADER_ID,
            "uploader_username": DEFAULT_USERNAME,
            "title": title,
            "description": description or f"Uploaded from local: {video_path.name}",
            "tags": tags,
            "video_url": video_url,
            "thumbnail_url": thumbnail_url,
            "duration": duration,
            "file_size": file_size,
            "views": 0,
            "likes": 0,
            "dislikes": 0,
            "saved_count": 0,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "is_active": True
        }
        
        # Save to MongoDB
        print(f"  💾 Saving to MongoDB...")
        result = await self.db.videos.insert_one(video_doc)
        print(f"  ✓ Saved to database with ID: {result.inserted_id}")
        
        print(f"✅ Successfully uploaded: {title}")
        return str(result.inserted_id)
    
    async def process_directory(self):
        """Process all videos in the videos directory"""
        videos_path = Path(VIDEOS_DIR)
        
        if not videos_path.exists():
            print(f"❌ Directory not found: {VIDEOS_DIR}")
            print(f"Creating directory: {VIDEOS_DIR}")
            videos_path.mkdir(parents=True, exist_ok=True)
            print(f"✓ Created. Please add videos to this folder and run again.")
            return
        
        # Find all video files
        video_extensions = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.mpeg']
        video_files = []
        for ext in video_extensions:
            video_files.extend(videos_path.glob(f'*{ext}'))
        
        if not video_files:
            print(f"❌ No video files found in {VIDEOS_DIR}/")
            print(f"Supported formats: {', '.join(video_extensions)}")
            return
        
        print(f"\n🎥 Found {len(video_files)} video(s) to upload\n")
        print("=" * 60)
        
        # Connect to database
        await self.connect_db()
        
        # Process each video
        uploaded = 0
        failed = 0
        
        for video_file in video_files:
            try:
                await self.upload_video(video_file)
                uploaded += 1
            except Exception as e:
                print(f"❌ Failed to upload {video_file.name}: {e}")
                failed += 1
        
        # Close database
        await self.close_db()
        
        print("\n" + "=" * 60)
        print(f"\n📊 Summary:")
        print(f"  ✓ Uploaded: {uploaded}")
        if failed > 0:
            print(f"  ✗ Failed: {failed}")
        print(f"\n🎉 Done!")


async def main():
    """Main function"""
    print("=" * 60)
    print("🚀 Kalesh.me Local Video Uploader")
    print("=" * 60)
    
    # Check configuration
    if not all([R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_PUBLIC_URL]):
        print("\n❌ Missing R2 configuration in .env file!")
        print("Please set: R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_PUBLIC_URL")
        return
    
    if not MONGODB_URL:
        print("\n❌ Missing MongoDB configuration in .env file!")
        print("Please set: MONGODB_URL")
        return
    
    print(f"\n📁 Videos directory: {VIDEOS_DIR}/")
    print(f"👤 Uploader: {DEFAULT_USERNAME} (ID: {DEFAULT_UPLOADER_ID})")
    print(f"🗄️ Database: {MONGODB_DB_NAME}")
    print(f"☁️ Storage: {R2_BUCKET_NAME}")
    
    uploader = VideoUploader()
    await uploader.process_directory()


if __name__ == "__main__":
    asyncio.run(main())

