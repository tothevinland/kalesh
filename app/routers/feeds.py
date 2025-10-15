from fastapi import APIRouter, HTTPException, status, Depends, Query
from typing import Optional
from bson import ObjectId
from datetime import datetime, timezone, timedelta
from app.schemas import VideoResponse, VideoList, APIResponse
from app.auth import get_current_user, get_current_user_optional
from app.database import get_database

import random

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get("/trending", response_model=APIResponse)
async def get_trending_videos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Get trending videos (sorted by likes + views + recency) with some randomization
    """
    db = get_database()
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Get videos from last 30 days with engagement score
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    
    # Fetch more videos than needed for randomization
    fetch_size = min(page_size * 3, 100)  # Fetch 3x more videos but cap at 100
    
    # Aggregate pipeline to calculate trending score
    pipeline = [
        {
            "$match": {
                "is_active": True,
                "processing_status": "completed",
                "created_at": {"$gte": thirty_days_ago}
            }
        },
        {
            "$addFields": {
                "trending_score": {
                    "$add": [
                        {"$multiply": ["$likes", 3]},  # Likes weighted 3x
                        {"$multiply": ["$views", 1]},   # Views weighted 1x
                        {"$multiply": ["$saved_count", 2]},  # Saves weighted 2x
                        # Add small random factor for variety
                        {"$multiply": [{"$rand": {}}, 10]}
                    ]
                }
            }
        },
        {
            "$sort": {"trending_score": -1, "created_at": -1}
        },
        {
            "$skip": skip
        },
        {
            "$limit": fetch_size
        },
        # Add a random sample stage to select a subset of the top trending videos
        {
            "$sample": {"size": page_size}
        }
    ]
    
    videos_cursor = db.videos.aggregate(pipeline)
    videos = await videos_cursor.to_list(length=page_size)
    
    # Get total count
    total = await db.videos.count_documents({
        "is_active": True,
        "processing_status": "completed",
        "created_at": {"$gte": thirty_days_ago}
    })
    
    # Format videos with user interactions if authenticated
    video_list = []
    for video in videos:
        user_interaction = None
        if current_user:
            user_id = str(current_user["_id"])
            video_id = str(video["_id"])
            
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
        video_list.append(video_response.model_dump())
    
    return APIResponse(
        status="success",
        message="Trending videos retrieved successfully",
        data={
            "videos": video_list,
            "total": total,
            "page": page,
            "page_size": page_size
        }
    )


@router.get("/recent", response_model=APIResponse)
async def get_recent_videos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Get recently uploaded videos with some randomization
    """
    db = get_database()
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Fetch more videos than needed for randomization
    fetch_size = min(page_size * 2, 100)  # Fetch 2x more videos but cap at 100
    
    # Get videos sorted by creation date with randomization
    pipeline = [
        {
            "$match": {
                "is_active": True, 
                "processing_status": "completed"
            }
        },
        {
            "$sort": {"created_at": -1}
        },
        {
            "$skip": skip
        },
        {
            "$limit": fetch_size
        },
        # Add a random sample stage to select a subset of the recent videos
        {
            "$sample": {"size": page_size}
        }
    ]
    
    videos_cursor = db.videos.aggregate(pipeline)
    
    videos = await videos_cursor.to_list(length=page_size)
    
    # Get total count
    total = await db.videos.count_documents({"is_active": True})
    
    # Format videos with user interactions if authenticated
    video_list = []
    for video in videos:
        user_interaction = None
        if current_user:
            user_id = str(current_user["_id"])
            video_id = str(video["_id"])
            
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
        video_list.append(video_response.model_dump())
    
    return APIResponse(
        status="success",
        message="Recent videos retrieved successfully",
        data={
            "videos": video_list,
            "total": total,
            "page": page,
            "page_size": page_size
        }
    )


@router.get("/saved", response_model=APIResponse)
async def get_saved_videos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """
    Get user's saved videos (requires authentication)
    """
    db = get_database()
    user_id = str(current_user["_id"])
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Get saved video IDs
    saved_interactions = await db.interactions.find({
        "user_id": user_id,
        "interaction_type": "save"
    }).sort("created_at", -1).skip(skip).limit(page_size).to_list(length=page_size)
    
    video_ids = [ObjectId(interaction["video_id"]) for interaction in saved_interactions]
    
    # Get videos
    videos_cursor = db.videos.find({
        "_id": {"$in": video_ids},
        "is_active": True,
        "processing_status": "completed"
    })
    videos = await videos_cursor.to_list(length=len(video_ids))
    
    # Get total count
    total = await db.interactions.count_documents({
        "user_id": user_id,
        "interaction_type": "save"
    })
    
    # Create video_id to video mapping for preserving order
    video_map = {str(video["_id"]): video for video in videos}
    
    # Format videos in the order of saved_interactions
    video_list = []
    for interaction in saved_interactions:
        video_id = interaction["video_id"]
        if video_id in video_map:
            video = video_map[video_id]
            
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
            
            user_interaction = {
                "liked": like is not None,
                "disliked": dislike is not None,
                "saved": True  # Obviously true since we're in saved videos
            }
            
            video_response = VideoResponse(
                id=str(video["_id"]),
                uploader_id=video["uploader_id"],
                uploader_username=video["uploader_username"],
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
                created_at=video["created_at"],
                user_interaction=user_interaction
            )
            video_list.append(video_response.model_dump())
    
    return APIResponse(
        status="success",
        message="Saved videos retrieved successfully",
        data={
            "videos": video_list,
            "total": total,
            "page": page,
            "page_size": page_size
        }
    )


@router.get("/discover", response_model=APIResponse)
async def discover_videos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Discover videos - mix of trending and new content
    """
    db = get_database()
    
    # Get videos from last 30 days
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    
    # Determine how many trending vs new videos to include
    trending_count = max(page_size // 2, 5)  # At least 5 trending videos or half the page size
    new_count = page_size - trending_count    # Fill the rest with new videos
    
    # Get trending videos
    trending_pipeline = [
        {
            "$match": {
                "is_active": True,
                "processing_status": "completed",
                "created_at": {"$gte": thirty_days_ago}
            }
        },
        {
            "$addFields": {
                "trending_score": {
                    "$add": [
                        {"$multiply": ["$likes", 3]},      # Likes weighted 3x
                        {"$multiply": ["$views", 1]},      # Views weighted 1x
                        {"$multiply": ["$saved_count", 2]} # Saves weighted 2x
                    ]
                }
            }
        },
        {
            "$sort": {"trending_score": -1}
        },
        {
            "$limit": trending_count * 3  # Get more than needed for randomization
        },
        {
            "$sample": {"size": trending_count}
        }
    ]
    
    trending_cursor = db.videos.aggregate(trending_pipeline)
    trending_videos = await trending_cursor.to_list(length=trending_count)
    
    # Get new videos (excluding those already in trending)
    trending_ids = [str(video["_id"]) for video in trending_videos]
    
    new_pipeline = [
        {
            "$match": {
                "is_active": True,
                "processing_status": "completed",
                "_id": {"$nin": [ObjectId(id) for id in trending_ids]}
            }
        },
        {
            "$sort": {"created_at": -1}
        },
        {
            "$limit": new_count * 3  # Get more than needed for randomization
        },
        {
            "$sample": {"size": new_count}
        }
    ]
    
    new_cursor = db.videos.aggregate(new_pipeline)
    new_videos = await new_cursor.to_list(length=new_count)
    
    # Combine videos and shuffle
    videos = trending_videos + new_videos
    random.shuffle(videos)
    
    # Get total count for pagination info
    total = await db.videos.count_documents({
        "is_active": True,
        "processing_status": "completed"
    })
    
    # Format videos with user interactions if authenticated
    video_list = []
    for video in videos:
        user_interaction = None
        if current_user:
            user_id = str(current_user["_id"])
            video_id = str(video["_id"])
            
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
        video_list.append(video_response.model_dump())
    
    return APIResponse(
        status="success",
        message="Discover feed retrieved successfully",
        data={
            "videos": video_list,
            "total": total,
            "page": page,
            "page_size": page_size
        }
    )


@router.get("/user/{username}", response_model=APIResponse)
async def get_user_videos(
    username: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Get videos by a specific user
    """
    db = get_database()
    
    # Get user by username
    user = await db.users.find_one({"username": username})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    user_id = str(user["_id"])
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Get user's videos
    videos_cursor = db.videos.find({
        "uploader_id": user_id,
        "is_active": True,
        "processing_status": "completed"
    }).sort("created_at", -1).skip(skip).limit(page_size)
    
    videos = await videos_cursor.to_list(length=page_size)
    
    # Get total count
    total = await db.videos.count_documents({
        "uploader_id": user_id,
        "is_active": True
    })
    
    # Format videos with user interactions if authenticated
    video_list = []
    for video in videos:
        user_interaction = None
        if current_user:
            current_user_id = str(current_user["_id"])
            video_id = str(video["_id"])
            
            like = await db.interactions.find_one({
                "user_id": current_user_id,
                "video_id": video_id,
                "interaction_type": "like"
            })
            dislike = await db.interactions.find_one({
                "user_id": current_user_id,
                "video_id": video_id,
                "interaction_type": "dislike"
            })
            save = await db.interactions.find_one({
                "user_id": current_user_id,
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
        video_list.append(video_response.model_dump())
    
    return APIResponse(
        status="success",
        message=f"Videos by {username} retrieved successfully",
        data={
            "videos": video_list,
            "total": total,
            "page": page,
            "page_size": page_size,
            "user": {
                "id": user_id,
                "username": username
            }
        }
    )

