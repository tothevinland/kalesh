from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # MongoDB
    MONGODB_URL: str
    MONGODB_DB_NAME: str = "kalesh_db"
    
    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Cloudflare R2
    R2_ENDPOINT_URL: str
    R2_ACCESS_KEY_ID: str
    R2_SECRET_ACCESS_KEY: str
    R2_BUCKET_NAME: str
    R2_PUBLIC_URL: str
    
    # Application
    MAX_VIDEO_SIZE_MB: int = 200
    ALLOWED_VIDEO_TYPES: str = "video/mp4,video/mpeg,video/quicktime,video/x-msvideo,video/webm"
    
    # Video Compression Settings
    VIDEO_COMPRESSION_PRESET: str = "fast"  # ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
    VIDEO_CRF_1080P: int = 24  # Higher CRF = faster encoding, slightly lower quality
    VIDEO_CRF_720P: int = 25
    VIDEO_CRF_480P: int = 26
    VIDEO_CRF_360P: int = 27
    VIDEO_BITRATE_1080P: str = "3500k"
    VIDEO_BITRATE_720P: str = "1800k"
    VIDEO_BITRATE_480P: str = "900k"
    VIDEO_BITRATE_360P: str = "500k"
    USE_TWO_PASS_ENCODING: bool = False  # Disable two-pass by default to save resources
    
    # Resource Limiting Settings
    FFMPEG_THREADS: int = 0  # 0 means auto-select based on CPU cores, set to specific number to limit
    MAX_CONCURRENT_UPLOADS: int = 1  # Maximum number of concurrent video processing tasks
    MAX_CONCURRENT_DELETIONS: int = 2  # Maximum number of concurrent deletion tasks
    
    @property
    def allowed_video_types_list(self) -> List[str]:
        return [t.strip() for t in self.ALLOWED_VIDEO_TYPES.split(",")]
    
    @property
    def max_video_size_bytes(self) -> int:
        return self.MAX_VIDEO_SIZE_MB * 1024 * 1024
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()

