from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form
from typing import Optional, List
from bson import ObjectId
from datetime import datetime, timezone
import tempfile
import os
from app.schemas import VideoUpload, VideoResponse, APIResponse
from app.auth import get_current_user, get_current_user_optional
from app.database import get_database
from app.utils.storage import r2_storage
from app.utils.video_processing import video_processor
from app.utils.video_queue import video_queue
from app.config import settings

router = APIRouter(prefix="/videos", tags=["videos"])


@router.post("/upload", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
async def upload_video(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),  # Comma-separated tags
    video: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload a new video (requires authentication)
    Returns immediately after upload, video will be processed in background
    """
    db = get_database()
    
    # Validate file type
    if video.content_type not in settings.allowed_video_types_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid video format"
        )
    
    # Read file
    video_content = await video.read()
    file_size = len(video_content)
    
    # Validate file size
    if file_size > settings.max_video_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Video size exceeds maximum limit of {settings.MAX_VIDEO_SIZE_MB}MB"
        )
    
    try:
        # Parse tags
        tags_list = []
        if tags:
            tags_list = [tag.strip() for tag in tags.split(',') if tag.strip()]
        
        # Upload RAW video to R2 first (for queue processing later)
        raw_video_filename = f"raw_{datetime.now(timezone.utc).timestamp()}_{video.filename}"
        raw_video_url = await r2_storage.upload_file(
            video_content,
            raw_video_filename,
            video.content_type
        )
        
        # Create video document with "pending" status
        video_doc = {
            "uploader_id": str(current_user["_id"]),
            "uploader_username": current_user["username"],
            "uploader_profile_image_url": current_user.get("profile_image_url"),
            "title": title,
            "description": description,
            "tags": tags_list,
            "playlist_url": raw_video_url,  # Store raw video URL temporarily
            "thumbnail_url": None,
            "duration": None,
            "file_size": file_size,
            "views": 0,
            "likes": 0,
            "dislikes": 0,
            "saved_count": 0,
            "processing_status": "pending",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "is_active": True
        }
        
        result = await db.videos.insert_one(video_doc)
        video_id = str(result.inserted_id)
        
        # Add to processing queue
        await video_queue.add_to_queue(video_id, raw_video_url)
        
        # Return immediately
        video_response = VideoResponse(
            id=video_id,
            uploader_id=video_doc["uploader_id"],
            uploader_username=video_doc["uploader_username"],
            uploader_profile_image_url=video_doc.get("uploader_profile_image_url"),
            title=video_doc["title"],
            description=video_doc["description"],
            tags=video_doc["tags"],
            playlist_url=video_doc["playlist_url"],
            thumbnail_url=video_doc["thumbnail_url"],
            duration=video_doc["duration"],
            views=video_doc["views"],
            likes=video_doc["likes"],
            dislikes=video_doc["dislikes"],
            saved_count=video_doc["saved_count"],
            processing_status=video_doc["processing_status"],
            created_at=video_doc["created_at"]
        )
        
        return APIResponse(
            status="success",
            message=f"Video uploaded successfully! Processing in queue (position: {video_queue.get_queue_size()})",
            data={"video": video_response.model_dump()}
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload video: {str(e)}"
        )


@router.get("/{video_id}/status", response_model=APIResponse)
async def get_video_processing_status(
    video_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get video processing status (authenticated users only)
    """
    db = get_database()
    
    # Validate video_id
    if not ObjectId.is_valid(video_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid video ID"
        )
    
    # Get video
    video = await db.videos.find_one({"_id": ObjectId(video_id)})
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="not found"
        )
    
    # Check if user owns this video
    if video["uploader_id"] != str(current_user["_id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your video"
        )
    
    status_info = {
        "video_id": video_id,
        "processing_status": video.get("processing_status", "completed"),
        "queue_position": video_queue.get_queue_size() if video.get("processing_status") == "pending" else 0,
        "title": video["title"],
        "created_at": video["created_at"],
        "updated_at": video.get("updated_at")
    }
    
    if video.get("processing_status") == "failed":
        status_info["error"] = video.get("processing_error", "Unknown error")
    
    return APIResponse(
        status="success",
        message="Processing status retrieved",
        data=status_info
    )


@router.post("/{video_id}/view", response_model=APIResponse)
async def track_video_view(
    video_id: str,
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Track a video view (call when video starts playing)
    Works for both authenticated and unauthenticated users
    """
    db = get_database()
    
    # Validate video_id
    if not ObjectId.is_valid(video_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid video ID"
        )
    
    # Check if video exists
    video = await db.videos.find_one({"_id": ObjectId(video_id), "is_active": True})
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found"
        )
    
    # Increment view count
    await db.videos.update_one(
        {"_id": ObjectId(video_id)},
        {"$inc": {"views": 1}}
    )
    
    return APIResponse(
        status="success",
        message="View tracked",
        data={"views": video["views"] + 1}
    )


@router.get("/{video_id}", response_model=APIResponse)
async def get_video(
    video_id: str,
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Get video details (public endpoint, but shows user interaction if authenticated)
    Does NOT increment view count - use POST /{video_id}/view for that
    """
    db = get_database()
    
    # Validate video_id
    if not ObjectId.is_valid(video_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid video ID"
        )
    
    # Get video
    video = await db.videos.find_one({"_id": ObjectId(video_id), "is_active": True})
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found"
        )
    
    # Get user interaction if authenticated
    user_interaction = None
    if current_user:
        user_id = str(current_user["_id"])
        like = await db.interactions.find_one({
            "user_id": user_id,
            "video_id": video_id,
            "interaction_type": "like"
        })
        dislike = await db.interactions.find_one({
            "user_id": user_id,
            "video_id": video_id,
            "interaction_type": "dislike"
        })
        save = await db.interactions.find_one({
            "user_id": user_id,
            "video_id": video_id,
            "interaction_type": "save"
        })
        
        user_interaction = {
            "liked": like is not None,
            "disliked": dislike is not None,
            "saved": save is not None
        }
    
    video_response = VideoResponse(
        id=str(video["_id"]),
        uploader_id=video["uploader_id"],
        uploader_username=video["uploader_username"],
        uploader_profile_image_url=video.get("uploader_profile_image_url"),
        title=video["title"],
        description=video.get("description"),
        tags=video.get("tags", []),
        playlist_url=video["playlist_url"],
        thumbnail_url=video.get("thumbnail_url"),
        duration=video.get("duration"),
        views=video["views"],
        likes=video["likes"],
        dislikes=video["dislikes"],
        saved_count=video["saved_count"],
        processing_status=video.get("processing_status", "completed"),
        created_at=video["created_at"],
        user_interaction=user_interaction
    )
    
    return APIResponse(
        status="success",
        message="Video retrieved successfully",
        data={"video": video_response.model_dump()}
    )


@router.delete("/{video_id}", response_model=APIResponse)
async def delete_video(
    video_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete video (only uploader can delete)
    """
    db = get_database()
    
    # Validate video_id
    if not ObjectId.is_valid(video_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid video ID"
        )
    
    # Get video
    video = await db.videos.find_one({"_id": ObjectId(video_id), "is_active": True})
    if not video:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found"
        )
    
    # Check if current user is the uploader
    if video["uploader_id"] != str(current_user["_id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own videos"
        )
    
    # Delete from R2 (HLS content)
    await r2_storage.delete_hls_content(video["playlist_url"])
    if video.get("thumbnail_url"):
        await r2_storage.delete_file(video["thumbnail_url"])
    
    # Mark as inactive (soft delete)
    await db.videos.update_one(
        {"_id": ObjectId(video_id)},
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}}
    )
    
    # Delete all interactions
    await db.interactions.delete_many({"video_id": video_id})
    
    return APIResponse(
        status="success",
        message="Video deleted successfully",
        data=None
    )

