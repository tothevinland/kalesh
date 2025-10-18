from fastapi import APIRouter, HTTPException, status, Depends, Query, Request, Response
from typing import Optional, List
from bson import ObjectId
from datetime import datetime, timezone, timedelta
from app.schemas import VideoResponse, VideoList, APIResponse
from app.auth import get_current_user, get_current_user_optional
from app.database import get_database
from app.models import NSFWPreference
from app.utils.datetime_helper import format_datetime_response
from app.utils.rate_limit import limiter, RATE_LIMIT_READ

import random

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get("/trending", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_READ)
async def get_trending_videos(
    request: Request,
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Get trending videos (sorted by likes + views + recency) with some randomization
    Excludes videos the user has already seen
    Rate limit: 500 per hour per IP
    """
    db = get_database()
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Get videos from last 30 days with engagement score
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    
    # Fetch more videos than needed for randomization
    fetch_size = min(page_size * 5, 150)  # Fetch 5x more videos but cap at 150
    
    # Get list of videos the user has already viewed if authenticated
    excluded_video_ids = []
    if current_user:
        user_id = str(current_user["_id"])
        now = datetime.now(timezone.utc)
        
        # Get all videos the user has viewed within the history window
        viewed_videos_cursor = db.user_view_history.find({
            "user_id": user_id,
            "expires_at": {"$gte": now}  # Only consider non-expired history
        })
        
        viewed_videos = await viewed_videos_cursor.to_list(length=1000)  # Reasonable limit
        excluded_video_ids = [ObjectId(v["video_id"]) for v in viewed_videos]
    
    # Create match condition based on whether we have videos to exclude
    match_condition = {
        "is_active": True,
        "processing_status": "completed",
        "created_at": {"$gte": thirty_days_ago}
    }
    
    # Filter NSFW content based on user preference
    nsfw_pref = NSFWPreference.ASK  # Default to ask before showing NSFW content
    if current_user:
        show_nsfw_value = current_user.get("show_nsfw", NSFWPreference.ASK)
        # Handle legacy boolean values
        if isinstance(show_nsfw_value, bool):
            show_nsfw_value = NSFWPreference.SHOW if show_nsfw_value else NSFWPreference.HIDE
        nsfw_pref = show_nsfw_value
    
    # Add NSFW filtering based on preference
    if nsfw_pref == NSFWPreference.HIDE:
        # Don't show any NSFW content
        match_condition["$or"] = [{"is_nsfw": False}, {"is_nsfw": {"$exists": False}}]
    elif nsfw_pref == NSFWPreference.ASK:
        # For the 'ask' option, we still return NSFW content but with a flag
        # The frontend will handle blurring/warning based on the is_nsfw flag
        pass  # No additional filtering needed, frontend will handle blurring
    if excluded_video_ids:
        match_condition["_id"] = {"$nin": excluded_video_ids}
    
    # Aggregate pipeline to calculate trending score
    pipeline = [
        {
            "$match": match_condition
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
            "$limit": max(1, fetch_size)  # Ensure limit is at least 1
        },
        # Add a random sample stage to select a subset of the top trending videos
        {
            "$sample": {"size": max(1, page_size)}  # Ensure sample size is at least 1
        }
    ]
    
    videos_cursor = db.videos.aggregate(pipeline)
    videos = await videos_cursor.to_list(length=max(1, page_size))
    
    # Get total count - don't exclude viewed videos from total count to ensure pagination works correctly
    total = await db.videos.count_documents({
        "is_active": True,
        "processing_status": "completed",
        "created_at": {"$gte": thirty_days_ago}
    })
    
    # If we didn't get enough videos due to exclusions, fetch additional videos
    if len(videos) < page_size and total > 0:
        # Remove the exclusion filter for a fallback query if needed
        fallback_match = {
            "is_active": True,
            "processing_status": "completed",
            "created_at": {"$gte": thirty_days_ago}
        }
        
        # Calculate how many more videos we need
        additional_needed = page_size - len(videos)
        
        # Get IDs of videos we already have to exclude them
        existing_ids = [ObjectId(str(video["_id"])) for video in videos]
        
        if existing_ids:
            fallback_match["_id"] = {"$nin": existing_ids}
        
        fallback_pipeline = [
            {
                "$match": fallback_match
            },
            {
                "$addFields": {
                    "trending_score": {
                        "$add": [
                            {"$multiply": ["$likes", 3]},
                            {"$multiply": ["$views", 1]},
                            {"$multiply": ["$saved_count", 2]},
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
                "$limit": max(1, additional_needed)  # Ensure limit is at least 1
            },
            {
                "$sample": {"size": max(1, additional_needed)}  # Ensure sample size is at least 1
            }
        ]
        
        fallback_cursor = db.videos.aggregate(fallback_pipeline)
        additional_videos = await fallback_cursor.to_list(length=max(1, additional_needed))
        videos.extend(additional_videos)
    
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
            created_at=format_datetime_response(video["created_at"]),
            user_interaction=user_interaction,
            is_nsfw=video.get("is_nsfw", False),
            last_part_id=video.get("last_part_id"),
            next_part_id=video.get("next_part_id")
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
@limiter.limit(RATE_LIMIT_READ)
async def get_recent_videos(
    request: Request,
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Get recently uploaded videos with some randomization
    Excludes videos the user has already seen
    """
    db = get_database()
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Fetch more videos than needed for randomization
    fetch_size = min(page_size * 4, 120)  # Fetch 4x more videos but cap at 120
    
    # Get list of videos the user has already viewed if authenticated
    excluded_video_ids = []
    if current_user:
        user_id = str(current_user["_id"])
        now = datetime.now(timezone.utc)
        
        # Get all videos the user has viewed within the history window
        viewed_videos_cursor = db.user_view_history.find({
            "user_id": user_id,
            "expires_at": {"$gte": now}  # Only consider non-expired history
        })
        
        viewed_videos = await viewed_videos_cursor.to_list(length=1000)  # Reasonable limit
        excluded_video_ids = [ObjectId(v["video_id"]) for v in viewed_videos]
    
    # Create match condition based on whether we have videos to exclude
    match_condition = {
        "is_active": True, 
        "processing_status": "completed"
    }
    
    # Filter NSFW content based on user preference
    nsfw_pref = NSFWPreference.ASK  # Default to ask before showing NSFW content
    if current_user:
        show_nsfw_value = current_user.get("show_nsfw", NSFWPreference.ASK)
        # Handle legacy boolean values
        if isinstance(show_nsfw_value, bool):
            show_nsfw_value = NSFWPreference.SHOW if show_nsfw_value else NSFWPreference.HIDE
        nsfw_pref = show_nsfw_value
    
    # Add NSFW filtering based on preference
    if nsfw_pref == NSFWPreference.HIDE:
        # Don't show any NSFW content
        match_condition["$or"] = [{"is_nsfw": False}, {"is_nsfw": {"$exists": False}}]
    elif nsfw_pref == NSFWPreference.ASK:
        # For the 'ask' option, we still return NSFW content but with a flag
        # The frontend will handle blurring/warning based on the is_nsfw flag
        pass  # No additional filtering needed, frontend will handle blurring
        
    # IMPORTANT: Don't filter out already watched videos in search results
    # Users expect search to return ALL matching videos regardless of watch history
    
    # Add exclusion filter if we have videos to exclude
    # For feeds like trending and discover, we want to exclude already watched videos
    # But for search and individual video endpoints, we want to show all videos
    if excluded_video_ids:
        match_condition["_id"] = {"$nin": excluded_video_ids}
    
    # Get videos sorted by creation date with randomization
    pipeline = [
        {
            "$match": match_condition
        },
        {
            "$sort": {"created_at": -1}
        },
        {
            "$skip": skip
        },
        {
            "$limit": max(1, fetch_size)  # Ensure limit is at least 1
        },
        # Add a random sample stage to select a subset of the recent videos
        {
            "$sample": {"size": max(1, page_size)}  # Ensure sample size is at least 1
        }
    ]
    
    videos_cursor = db.videos.aggregate(pipeline)
    
    videos = await videos_cursor.to_list(length=max(1, page_size))
    
    # Get total count - don't exclude viewed videos from total count to ensure pagination works correctly
    total = await db.videos.count_documents({
        "is_active": True,
        "processing_status": "completed"
    })
    
    # If we didn't get enough videos due to exclusions, fetch additional videos
    if len(videos) < page_size and total > 0:
        # Remove the exclusion filter for a fallback query if needed
        fallback_match = {
            "is_active": True,
            "processing_status": "completed"
        }
        
        # Calculate how many more videos we need
        additional_needed = page_size - len(videos)
        
        # Get IDs of videos we already have to exclude them
        existing_ids = [ObjectId(str(video["_id"])) for video in videos]
        
        if existing_ids:
            fallback_match["_id"] = {"$nin": existing_ids}
        
        fallback_pipeline = [
            {
                "$match": fallback_match
            },
            {
                "$sort": {"created_at": -1}
            },
            {
                "$skip": skip
            },
            {
                "$limit": max(1, additional_needed)  # Ensure limit is at least 1
            },
            {
                "$sample": {"size": max(1, additional_needed)}  # Ensure sample size is at least 1
            }
        ]
        
        fallback_cursor = db.videos.aggregate(fallback_pipeline)
        additional_videos = await fallback_cursor.to_list(length=max(1, additional_needed))
        videos.extend(additional_videos)
    
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
            created_at=format_datetime_response(video["created_at"]),
            user_interaction=user_interaction,
            is_nsfw=video.get("is_nsfw", False),
            last_part_id=video.get("last_part_id"),
            next_part_id=video.get("next_part_id")
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
@limiter.limit(RATE_LIMIT_READ)
async def get_saved_videos(
    request: Request,
    response: Response,
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
                created_at=format_datetime_response(video["created_at"]),
                user_interaction=user_interaction,
                is_nsfw=video.get("is_nsfw", False),
                last_part_id=video.get("last_part_id"),
                next_part_id=video.get("next_part_id")
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
@limiter.limit(RATE_LIMIT_READ)
async def discover_videos(
    request: Request,
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Discover videos - mix of trending and new content
    Excludes videos the user has already seen
    """
    db = get_database()
    
    # Get videos from last 30 days
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    
    # Determine how many trending vs new videos to include
    trending_count = max(page_size // 2, 5)  # At least 5 trending videos or half the page size
    new_count = page_size - trending_count    # Fill the rest with new videos
    
    # Get list of videos the user has already viewed if authenticated
    excluded_video_ids = []
    if current_user:
        user_id = str(current_user["_id"])
        now = datetime.now(timezone.utc)
        
        # Get all videos the user has viewed within the history window
        viewed_videos_cursor = db.user_view_history.find({
            "user_id": user_id,
            "expires_at": {"$gte": now}  # Only consider non-expired history
        })
        
        viewed_videos = await viewed_videos_cursor.to_list(length=1000)  # Reasonable limit
        excluded_video_ids = [ObjectId(v["video_id"]) for v in viewed_videos]
    
    # Create match conditions based on whether we have videos to exclude
    trending_match = {
        "is_active": True,
        "processing_status": "completed",
        "created_at": {"$gte": thirty_days_ago}
    }
    
    # Add exclusion filter if we have videos to exclude
    if excluded_video_ids:
        trending_match["_id"] = {"$nin": excluded_video_ids}
    
    # Get trending videos
    trending_pipeline = [
        {
            "$match": trending_match
        },
        {
            "$addFields": {
                "trending_score": {
                    "$add": [
                        {"$multiply": ["$likes", 3]},      # Likes weighted 3x
                        {"$multiply": ["$views", 1]},      # Views weighted 1x
                        {"$multiply": ["$saved_count", 2]}, # Saves weighted 2x
                        # Add small random factor for variety
                        {"$multiply": [{"$rand": {}}, 5]}
                    ]
                }
            }
        },
        {
            "$sort": {"trending_score": -1}
        },
        {
            "$limit": max(1, trending_count * 5)  # Get more than needed for randomization, ensure at least 1
        },
        {
            "$sample": {"size": max(1, trending_count)}  # Ensure sample size is at least 1
        }
    ]
    
    trending_cursor = db.videos.aggregate(trending_pipeline)
    trending_videos = await trending_cursor.to_list(length=max(1, trending_count))
    
    # Get new videos (excluding those already in trending)
    trending_ids = [str(video["_id"]) for video in trending_videos]
    
    # Combine excluded videos and trending videos for new video exclusion
    new_exclude_ids = excluded_video_ids.copy()
    new_exclude_ids.extend([ObjectId(id) for id in trending_ids])
    
    new_pipeline = [
        {
            "$match": {
                "is_active": True,
                "processing_status": "completed",
                "_id": {"$nin": new_exclude_ids}
            }
        },
        {
            "$sort": {"created_at": -1}
        },
        {
            "$limit": max(1, new_count * 5)  # Get more than needed for randomization, ensure at least 1
        },
        {
            "$sample": {"size": max(1, new_count)}  # Ensure sample size is at least 1
        }
    ]
    
    new_cursor = db.videos.aggregate(new_pipeline)
    new_videos = await new_cursor.to_list(length=max(1, new_count))
    
    # Combine videos and shuffle
    videos = trending_videos + new_videos
    random.shuffle(videos)
    
    # Get total count for pagination info
    total = await db.videos.count_documents({
        "is_active": True,
        "processing_status": "completed"
    })
    
    # If we didn't get enough videos, fetch additional ones without exclusion filters
    if len(videos) < page_size and total > 0:
        needed_count = max(1, page_size - len(videos))  # Ensure needed_count is at least 1
        
        # Get IDs of videos we already have to exclude them from fallback query
        existing_ids = [ObjectId(str(video["_id"])) for video in videos]
        
        fallback_match = {
            "is_active": True,
            "processing_status": "completed"
        }
        
        if existing_ids:
            fallback_match["_id"] = {"$nin": existing_ids}
        
        # Simple fallback query to get any remaining videos needed
        fallback_cursor = db.videos.find(fallback_match).sort("created_at", -1).limit(max(1, needed_count))
        fallback_videos = await fallback_cursor.to_list(length=max(1, needed_count))
        
        # Add fallback videos to our result set
        videos.extend(fallback_videos)
    
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
            created_at=format_datetime_response(video["created_at"]),
            user_interaction=user_interaction,
            is_nsfw=video.get("is_nsfw", False),
            last_part_id=video.get("last_part_id"),
            next_part_id=video.get("next_part_id")
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


@router.get("/search", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_READ)
async def search_videos(
    request: Request,
    response: Response,
    query: str = Query(..., min_length=1, description="Search query for video titles and descriptions"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    include_users: bool = Query(True, description="Whether to include users in search results"),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Search for videos by title and description, and optionally users by username
    Uses MongoDB text search for efficient querying
    """
    db = get_database()
    
    # Calculate skip for pagination
    skip = (page - 1) * page_size
    
    # Create the search query
    search_query = {
        "$text": {"$search": query},
        "is_active": True,
        "processing_status": "completed"
    }
    
    # Filter NSFW content based on user preference
    nsfw_pref = NSFWPreference.ASK  # Default to ask before showing NSFW content
    if current_user:
        show_nsfw_value = current_user.get("show_nsfw", NSFWPreference.ASK)
        # Handle legacy boolean values
        if isinstance(show_nsfw_value, bool):
            show_nsfw_value = NSFWPreference.SHOW if show_nsfw_value else NSFWPreference.HIDE
        nsfw_pref = show_nsfw_value
    
    # Add NSFW filtering based on preference
    if nsfw_pref == NSFWPreference.HIDE:
        # Don't show any NSFW content
        search_query["$or"] = [{"is_nsfw": False}, {"is_nsfw": {"$exists": False}}]
    elif nsfw_pref == NSFWPreference.ASK:
        # For the 'ask' option, we still return NSFW content but with a flag
        # The frontend will handle blurring/warning based on the is_nsfw flag
        pass  # No additional filtering needed, frontend will handle blurring
        
    # IMPORTANT: Search should show ALL matching videos including already watched ones
    
    # Get videos matching the search query
    videos_cursor = db.videos.find(
        search_query,
        # Add text score for sorting by relevance
        {"score": {"$meta": "textScore"}}
    ).sort([
        # Sort by text match score first
        ("score", {"$meta": "textScore"}),
        # Then by recency as secondary sort
        ("created_at", -1)
    ]).skip(skip).limit(page_size)
    
    videos = await videos_cursor.to_list(length=max(1, page_size))
    
    # Get total count
    total = await db.videos.count_documents(search_query)
    
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
            created_at=format_datetime_response(video["created_at"]),
            user_interaction=user_interaction,
            is_nsfw=video.get("is_nsfw", False),
            last_part_id=video.get("last_part_id"),
            next_part_id=video.get("next_part_id")
        )
        video_list.append(video_response.model_dump())
    
    # Search for users if requested
    users_list = []
    users_total = 0
    
    if include_users:
        # Create regex for case-insensitive username search
        regex_pattern = f".*{query}.*"
        users_cursor = db.users.find(
            {
                "username": {"$regex": regex_pattern, "$options": "i"},
                "is_active": True
            }
        ).limit(10)  # Limit to top 10 user matches
        
        users = await users_cursor.to_list(length=10)
        users_total = len(users)
        
        # Format user results
        for user in users:
            users_list.append({
                "id": str(user["_id"]),
                "username": user["username"],
                "full_name": user.get("full_name"),
                "profile_image_url": user.get("profile_image_url")
            })
    
    return APIResponse(
        status="success",
        message=f"Search results for '{query}'",
        data={
            "videos": video_list,
            "users": users_list,
            "total_videos": total,
            "total_users": users_total,
            "page": page,
            "page_size": page_size,
            "query": query
        }
    )


@router.get("/user/{username}", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_READ)
async def get_user_videos(
    request: Request,
    response: Response,
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
    
    videos = await videos_cursor.to_list(length=max(1, page_size))
    
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
            created_at=format_datetime_response(video["created_at"]),
            user_interaction=user_interaction,
            is_nsfw=video.get("is_nsfw", False),
            last_part_id=video.get("last_part_id"),
            next_part_id=video.get("next_part_id")
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

