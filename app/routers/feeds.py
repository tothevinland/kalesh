from fastapi import APIRouter, HTTPException, status, Depends, Query
from typing import Optional
from bson import ObjectId
from datetime import datetime, timedelta
from app.schemas import VideoResponse, VideoList, APIResponse
from app.auth import get_current_user, get_current_user_optional
from app.database import get_database

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get("/trending", response_model=APIResponse)
async def get_trending_videos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Get trending videos (sorted by likes + views + recency)
    """
    db = get_database()
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Get videos from last 30 days with engagement score
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    # Aggregate pipeline to calculate trending score
    pipeline = [
        {
            "$match": {
                "is_active": True,
                "created_at": {"$gte": thirty_days_ago}
            }
        },
        {
            "$addFields": {
                "trending_score": {
                    "$add": [
                        {"$multiply": ["$likes", 3]},  # Likes weighted 3x
                        {"$multiply": ["$views", 1]},   # Views weighted 1x
                        {"$multiply": ["$saved_count", 2]}  # Saves weighted 2x
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
            "$limit": page_size
        }
    ]
    
    videos_cursor = db.videos.aggregate(pipeline)
    videos = await videos_cursor.to_list(length=page_size)
    
    # Get total count
    total = await db.videos.count_documents({
        "is_active": True,
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
            title=video["title"],
            description=video.get("description"),
            tags=video.get("tags", []),
            video_url=video["video_url"],
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
    Get recently uploaded videos
    """
    db = get_database()
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Get videos sorted by creation date
    videos_cursor = db.videos.find(
        {"is_active": True}
    ).sort("created_at", -1).skip(skip).limit(page_size)
    
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
            title=video["title"],
            description=video.get("description"),
            tags=video.get("tags", []),
            video_url=video["video_url"],
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
        "is_active": True
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
                video_url=video["video_url"],
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
        "is_active": True
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
            title=video["title"],
            description=video.get("description"),
            tags=video.get("tags", []),
            video_url=video["video_url"],
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

