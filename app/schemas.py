from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field


# Response Models
class APIResponse(BaseModel):
    status: str
    message: str
    data: Optional[dict] = None


# User Schemas
class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)


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
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[str] = None


# Video Schemas
class VideoUpload(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)
    tags: Optional[List[str]] = Field(default_factory=list, max_items=10)


class VideoResponse(BaseModel):
    id: str
    uploader_id: str
    uploader_username: str
    title: str
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    video_url: str
    thumbnail_url: Optional[str] = None
    duration: Optional[float] = None
    views: int = 0
    likes: int = 0
    dislikes: int = 0
    saved_count: int = 0
    created_at: datetime
    user_interaction: Optional[dict] = None  # {"liked": bool, "disliked": bool, "saved": bool}


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

