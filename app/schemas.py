from datetime import datetime
from typing import Optional, List, Dict, Any
import re
from pydantic import BaseModel, EmailStr, Field, field_validator
from app.models import NSFWPreference


# Response Models
class DateTimeResponse(BaseModel):
    """Schema for datetime responses with timezone information"""
    iso: str  # ISO 8601 format with timezone
    timestamp: float  # Unix timestamp in seconds
    timezone: str  # UTC offset string

class APIResponse(BaseModel):
    status: str
    message: str
    data: Optional[dict] = None


# User Schemas
class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_]+$')
    password: str = Field(..., min_length=6)
    
    @field_validator('username')
    @classmethod
    def validate_username(cls, v):
        # Only allow letters, numbers, and underscores
        if not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError('Username can only contain letters, numbers, and underscores')
        return v


class UserLogin(BaseModel):
    username: str
    password: str


class UserProfile(BaseModel):
    id: str
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    bio: Optional[str] = None
    profile_image_url: Optional[str] = None
    created_at: DateTimeResponse
    show_nsfw: NSFWPreference = NSFWPreference.ASK


class UserProfileUpdate(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = Field(None, max_length=100)
    bio: Optional[str] = Field(None, max_length=500)
    show_nsfw: Optional[NSFWPreference] = None
    
    @field_validator('full_name')
    @classmethod
    def sanitize_full_name(cls, v):
        if v is None:
            return v
        # Remove any potentially harmful characters
        return re.sub(r'[^\w\s\-\'\.]', '', v).strip()
    
    @field_validator('bio')
    @classmethod
    def sanitize_bio(cls, v):
        if v is None:
            return v
        # Basic sanitization - allow common punctuation but remove potential script tags
        return re.sub(r'<[^>]*>', '', v)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[str] = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict  # Contains id, username, etc.


# Video Schemas
class VideoUpload(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)
    tags: Optional[List[str]] = Field(default_factory=list, max_items=10)
    is_nsfw: bool = False
    
    @field_validator('title')
    @classmethod
    def sanitize_title(cls, v):
        if v is None:
            return v
        return re.sub(r'<[^>]*>', '', v).strip()
    
    @field_validator('description')
    @classmethod
    def sanitize_description(cls, v):
        if v is None:
            return v
        return re.sub(r'<[^>]*>', '', v)
    
    @field_validator('tags')
    @classmethod
    def sanitize_tags(cls, v):
        if not v:
            return []
        # Sanitize each tag - only allow alphanumeric and some punctuation
        sanitized = []
        for tag in v:
            clean_tag = re.sub(r'[^\w\s\-]', '', tag).strip()
            if clean_tag:  # Only add non-empty tags
                sanitized.append(clean_tag)
        return sanitized


class VideoResponse(BaseModel):
    id: str
    uploader_id: str
    uploader_username: str
    uploader_profile_image_url: Optional[str] = None
    title: str
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    playlist_url: str  # HLS master playlist URL
    thumbnail_url: Optional[str] = None
    duration: Optional[float] = None
    views: int = 0
    likes: int = 0
    dislikes: int = 0
    saved_count: int = 0
    processing_status: str = "completed"  # pending, processing, completed, failed
    created_at: DateTimeResponse
    user_interaction: Optional[dict] = None  # {"liked": bool, "disliked": bool, "saved": bool}
    is_nsfw: bool = False
    last_part_id: Optional[str] = None  # Reference to previous video in series
    next_part_id: Optional[str] = None  # Reference to next video in series


class VideoList(BaseModel):
    videos: List[VideoResponse]
    total: int
    page: int
    page_size: int


# Interaction Schemas
class InteractionResponse(BaseModel):
    success: bool
    action: str  # "added" or "removed"
    new_count: int


# Report Schema
class ReportCreate(BaseModel):
    reason: str = Field(..., min_length=1, max_length=100)
    details: Optional[str] = Field(None, max_length=1000)


# Comment Schemas
class CommentCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)


class CommentResponse(BaseModel):
    id: str
    video_id: str
    user_id: str
    username: str
    text: str
    parent_comment_id: Optional[str] = None
    likes: int = 0
    replies_count: int = 0
    created_at: DateTimeResponse
    user_liked: bool = False  # Whether current user liked this comment


class CommentWithReplies(BaseModel):
    id: str
    video_id: str
    user_id: str
    username: str
    text: str
    likes: int = 0
    replies_count: int = 0
    created_at: DateTimeResponse
    user_liked: bool = False
    replies: List[CommentResponse] = Field(default_factory=list)

