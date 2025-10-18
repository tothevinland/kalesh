from fastapi import APIRouter, HTTPException, status, Depends, Query, Request
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from app.schemas import APIResponse
from app.auth import get_current_user, get_current_user_optional
from app.database import get_database
from app.utils.datetime_helper import format_datetime_response
from app.utils.rate_limit import limiter, RATE_LIMIT_READ

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("/trending", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_READ)
async def get_trending_tags(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Get trending tags based on recent video uploads and views
    Rate limit: 500 per hour per IP
    """
    db = get_database()
    
    # Get videos from the last 7 days
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    
    # Aggregate to find most used tags in recent popular videos
    pipeline = [
        {
            "$match": {
                "created_at": {"$gte": seven_days_ago},
                "is_active": True,
                "tags": {"$exists": True, "$ne": []}
            }
        },
        {
            "$project": {
                "tags": 1,
                "views": 1,
                "likes": 1,
                "created_at": 1,
                # Calculate a trending score based on views, likes, and recency
                "score": {
                    "$add": [
                        "$views",
                        {"$multiply": ["$likes", 5]},
                        # Boost newer videos
                        {"$divide": [
                            {"$subtract": [datetime.now(timezone.utc), "$created_at"]},
                            3600000  # Convert ms to hours
                        ]}
                    ]
                }
            }
        },
        {"$unwind": "$tags"},
        {
            "$group": {
                "_id": "$tags",
                "count": {"$sum": 1},
                "total_score": {"$sum": "$score"},
                "videos": {"$sum": 1}
            }
        },
        {"$sort": {"total_score": -1}},
        {"$limit": limit},
        {
            "$project": {
                "_id": 0,
                "tag": "$_id",
                "count": 1,
                "videos": 1
            }
        }
    ]
    
    trending_tags = await db.videos.aggregate(pipeline).to_list(length=limit)
    
    return APIResponse(
        status="success",
        message="Trending tags retrieved",
        data={"tags": trending_tags}
    )


@router.get("/suggest", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_READ)
async def suggest_tags(
    request: Request,
    query: str = Query(..., min_length=1, max_length=50),
    limit: int = Query(10, ge=1, le=50),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Suggest tags based on partial input
    """
    db = get_database()
    
    # Create case-insensitive regex for the query
    regex_pattern = f"^{query}"
    
    # Aggregate to find matching tags
    pipeline = [
        {
            "$match": {
                "is_active": True,
                "tags": {"$regex": regex_pattern, "$options": "i"}
            }
        },
        {"$unwind": "$tags"},
        {
            "$match": {
                "tags": {"$regex": regex_pattern, "$options": "i"}
            }
        },
        {
            "$group": {
                "_id": "$tags",
                "count": {"$sum": 1}
            }
        },
        {"$sort": {"count": -1}},
        {"$limit": limit},
        {
            "$project": {
                "_id": 0,
                "tag": "$_id",
                "count": 1
            }
        }
    ]
    
    suggested_tags = await db.videos.aggregate(pipeline).to_list(length=limit)
    
    return APIResponse(
        status="success",
        message="Tag suggestions retrieved",
        data={"tags": suggested_tags}
    )


@router.get("/explore", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_READ)
async def explore_by_tag(
    request: Request,
    tag: str = Query(..., min_length=1, max_length=50),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Explore videos by tag
    """
    db = get_database()
    
    # Calculate skip for pagination
    skip = (page - 1) * page_size
    
    # Find videos with the specified tag
    query = {
        "tags": {"$regex": f"^{tag}$", "$options": "i"},
        "is_active": True
    }
    
    # Count total matching videos
    total_videos = await db.videos.count_documents(query)
    
    # Get paginated videos
    videos_cursor = db.videos.find(query).sort("created_at", -1).skip(skip).limit(page_size)
    videos = await videos_cursor.to_list(length=page_size)
    
    # Format response
    video_responses = []
    for video in videos:
        # Get user interaction if authenticated
        user_interaction = None
        if current_user:
            user_id = str(current_user["_id"])
            like = await db.interactions.find_one({
                "user_id": user_id,
                "video_id": str(video["_id"]),
                "interaction_type": "like"
            })
            dislike = await db.interactions.find_one({
                "user_id": user_id,
                "video_id": str(video["_id"]),
                "interaction_type": "dislike"
            })
            save = await db.interactions.find_one({
                "user_id": user_id,
                "video_id": str(video["_id"]),
                "interaction_type": "save"
            })
            
            user_interaction = {
                "liked": like is not None,
                "disliked": dislike is not None,
                "saved": save is not None
            }
        
        video_responses.append({
            "id": str(video["_id"]),
            "uploader_id": video["uploader_id"],
            "uploader_username": video["uploader_username"],
            "uploader_profile_image_url": video.get("uploader_profile_image_url"),
            "title": video["title"],
            "description": video.get("description"),
            "tags": video.get("tags", []),
            "playlist_url": video["playlist_url"],
            "thumbnail_url": video.get("thumbnail_url"),
            "duration": video.get("duration"),
            "views": video["views"],
            "likes": video["likes"],
            "dislikes": video["dislikes"],
            "saved_count": video["saved_count"],
            "processing_status": video.get("processing_status", "completed"),
            "created_at": format_datetime_response(video["created_at"]),
            "user_interaction": user_interaction
        })
    
    return APIResponse(
        status="success",
        message=f"Videos with tag '{tag}' retrieved",
        data={
            "videos": video_responses,
            "total": total_videos,
            "page": page,
            "page_size": page_size,
            "tag": tag
        }
    )
