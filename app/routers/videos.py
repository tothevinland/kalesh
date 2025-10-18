from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form, Query, Request
from typing import Optional, List
from bson import ObjectId
from datetime import datetime, timezone, timedelta
import tempfile
import os
import re
from app.schemas import VideoUpload, VideoResponse, APIResponse
from app.auth import get_current_user, get_current_user_optional
from app.database import get_database
from app.utils.storage import r2_storage
from app.utils.video_processing import video_processor
from app.utils.video_queue import video_queue
from app.config import settings
from app.utils.datetime_helper import format_datetime_response
from app.utils.rate_limit import limiter, RATE_LIMIT_VIDEO_UPLOAD, RATE_LIMIT_READ, RATE_LIMIT_PROFILE_UPDATE

router = APIRouter(prefix="/videos", tags=["videos"])


@router.post("/upload", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_VIDEO_UPLOAD)
async def upload_video(
    request: Request,
    title: str = Form(...),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),  # Comma-separated tags
    is_nsfw: bool = Form(False),  # Flag for NSFW content
    last_part_id: Optional[str] = Form(None),  # Reference to previous video in series
    video: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload a new video (requires authentication)
    Returns immediately after upload, video will be processed in background
    Rate limit: 5 per hour per IP (STRICT to prevent spam)
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
        # Validate and check last_part_id if provided
        if last_part_id:
            if not ObjectId.is_valid(last_part_id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid last_part_id"
                )
            
            # Check if the referenced video exists and belongs to the same user
            last_part_video = await db.videos.find_one({
                "_id": ObjectId(last_part_id),
                "uploader_id": str(current_user["_id"]),
                "is_active": True
            })
            
            if not last_part_video:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Referenced video not found or doesn't belong to you"
                )
            
            # Check if the last part already has a next part
            if last_part_video.get("next_part_id"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="The referenced video already has a next part. Please reference the latest part in the series."
                )
        
        # Parse and sanitize tags
        tags_list = []
        if tags:
            # Split by comma and sanitize each tag
            for tag in tags.split(','):
                tag = tag.strip()
                if tag:
                    # Only allow alphanumeric and some punctuation in tags
                    clean_tag = re.sub(r'[^\w\s\-]', '', tag).strip()
                    if clean_tag:  # Only add non-empty tags
                        tags_list.append(clean_tag)
        
        # Upload RAW video to R2 first (for queue processing later)
        raw_video_filename = f"raw_{datetime.now(timezone.utc).timestamp()}_{video.filename}"
        raw_video_url = await r2_storage.upload_file(
            video_content,
            raw_video_filename,
            video.content_type
        )
        
        # Sanitize inputs
        sanitized_title = re.sub(r'<[^>]*>', '', title).strip()
        sanitized_description = None
        if description:
            sanitized_description = re.sub(r'<[^>]*>', '', description)
        
        # Create video document with "pending" status
        video_doc = {
            "uploader_id": str(current_user["_id"]),
            "uploader_username": current_user["username"],
            "uploader_profile_image_url": current_user.get("profile_image_url"),
            "title": sanitized_title,
            "description": sanitized_description,
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
            "is_active": True,
            "is_nsfw": is_nsfw,
            "last_part_id": last_part_id,
            "next_part_id": None
        }
        
        result = await db.videos.insert_one(video_doc)
        video_id = str(result.inserted_id)
        
        # If this video references a previous part, update that video's next_part_id
        if last_part_id:
            await db.videos.update_one(
                {"_id": ObjectId(last_part_id)},
                {"$set": {"next_part_id": video_id, "updated_at": datetime.now(timezone.utc)}}
            )
        
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
            created_at=format_datetime_response(video_doc["created_at"]),
            is_nsfw=video_doc["is_nsfw"],
            last_part_id=video_doc.get("last_part_id"),
            next_part_id=video_doc.get("next_part_id")
        )
        
        return APIResponse(
            status="success",
            message=f"Video uploaded successfully! Processing in queue (position: {video_queue.get_queue_size()})",
            data={"video": video_response.model_dump()}
        )
    
    except Exception as e:
        # Log the full error for debugging
        print(f"Video upload error: {str(e)}")
        
        # Try to clean up any resources if possible
        try:
            if 'result' in locals() and 'video_id' in locals():
                # If the video was added to the database but processing failed
                await db.videos.update_one(
                    {"_id": ObjectId(video_id)},
                    {"$set": {"processing_status": "failed", "processing_error": str(e)}}
                )
        except Exception:
            pass
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload video. Please try again."
        )


@router.get("/{video_id}/status", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_READ)
async def get_video_processing_status(
    request: Request,
    video_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get video processing status (authenticated users only)
    Rate limit: 500 per hour per IP
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
@limiter.limit(RATE_LIMIT_READ)
async def track_video_view(
    request: Request,
    video_id: str,
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Track a video view (call when video starts playing)
    Works for both authenticated and unauthenticated users
    Also records to user view history for authenticated users to prevent duplicate recommendations
    Rate limit: 500 per hour per IP
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
    
    # If user is authenticated, record to view history
    if current_user:
        user_id = str(current_user["_id"])
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=14)  # History expires after 14 days
        
        # Check if this view is already recorded
        existing_view = await db.user_view_history.find_one({
            "user_id": user_id,
            "video_id": video_id
        })
        
        if existing_view:
            # Update the existing view timestamp
            await db.user_view_history.update_one(
                {"_id": existing_view["_id"]},
                {"$set": {"created_at": now, "expires_at": expires_at}}
            )
        else:
            # Record new view
            await db.user_view_history.insert_one({
                "user_id": user_id,
                "video_id": video_id,
                "created_at": now,
                "expires_at": expires_at
            })
    
    return APIResponse(
        status="success",
        message="View tracked",
        data={"views": video["views"] + 1}
    )


@router.get("/my-videos", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_READ)
async def search_my_videos(
    request: Request,
    query: Optional[str] = Query(None, description="Search query for your video titles and descriptions"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """
    Search through the authenticated user's own videos
    Rate limit: 500 per hour per IP
    """
    db = get_database()
    user_id = str(current_user["_id"])
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Build search query
    search_query = {
        "uploader_id": user_id,
        "is_active": True
    }
    
    # Add text search if query is provided
    if query and query.strip():
        search_query["$text"] = {"$search": query}
    
    # Get videos
    if query and query.strip():
        # Use text search with relevance scoring
        videos_cursor = db.videos.find(
            search_query,
            {"score": {"$meta": "textScore"}}
        ).sort([
            ("score", {"$meta": "textScore"}),
            ("created_at", -1)
        ]).skip(skip).limit(page_size)
    else:
        # Just get all user's videos sorted by date
        videos_cursor = db.videos.find(search_query).sort("created_at", -1).skip(skip).limit(page_size)
    
    videos = await videos_cursor.to_list(length=page_size)
    
    # Get total count
    total = await db.videos.count_documents(search_query)
    
    # Format videos with user interactions
    video_list = []
    for video in videos:
        video_id = str(video["_id"])
        
        # Get user interactions
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
            id=video_id,
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
            created_at=format_datetime_response(video["created_at"]),
            user_interaction=user_interaction,
            is_nsfw=video.get("is_nsfw", False),
            last_part_id=video.get("last_part_id"),
            next_part_id=video.get("next_part_id")
        )
        video_list.append(video_response.model_dump())
    
    return APIResponse(
        status="success",
        message=f"Your videos retrieved successfully" + (f" (search: '{query}')" if query else ""),
        data={
            "videos": video_list,
            "total": total,
            "page": page,
            "page_size": page_size
        }
    )


@router.get("/{video_id}", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_READ)
async def get_video(
    request: Request,
    video_id: str,
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Get video details (public endpoint, but shows user interaction if authenticated)
    Does NOT increment view count - use POST /{video_id}/view for that
    Rate limit: 500 per hour per IP
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
        created_at=format_datetime_response(video["created_at"]),
        user_interaction=user_interaction,
        is_nsfw=video.get("is_nsfw", False),
        last_part_id=video.get("last_part_id"),
        next_part_id=video.get("next_part_id")
    )
    
    return APIResponse(
        status="success",
        message="Video retrieved successfully",
        data={"video": video_response.model_dump()}
    )


@router.delete("/{video_id}", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_PROFILE_UPDATE)
async def delete_video(
    request: Request,
    video_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete video (only uploader can delete)
    Rate limit: 20 per hour per IP
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
    
    # Queue files for deletion in background
    from app.utils.deletion_queue import deletion_queue
    await deletion_queue.add_to_queue('hls', video["playlist_url"])
    if video.get("thumbnail_url"):
        await deletion_queue.add_to_queue('regular', video["thumbnail_url"])
    
    # Mark as inactive (soft delete)
    await db.videos.update_one(
        {"_id": ObjectId(video_id)},
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}}
    )
    
    # Delete all interactions
    await db.interactions.delete_many({"video_id": video_id})
    
    # Update part references if this video is part of a series
    if video.get("last_part_id"):
        # Remove this video as the next part from the previous video
        await db.videos.update_one(
            {"_id": ObjectId(video["last_part_id"])},
            {"$set": {"next_part_id": video.get("next_part_id"), "updated_at": datetime.now(timezone.utc)}}
        )
    
    if video.get("next_part_id"):
        # Remove this video as the last part from the next video
        await db.videos.update_one(
            {"_id": ObjectId(video["next_part_id"])},
            {"$set": {"last_part_id": video.get("last_part_id"), "updated_at": datetime.now(timezone.utc)}}
        )
    
    return APIResponse(
        status="success",
        message="Video deleted successfully",
        data=None
    )
