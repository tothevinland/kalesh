from fastapi import APIRouter, HTTPException, status, Depends
from bson import ObjectId
from datetime import datetime, timezone
from app.schemas import InteractionResponse, ReportCreate, APIResponse
from app.auth import get_current_user
from app.database import get_database

router = APIRouter(prefix="/interactions", tags=["interactions"])


@router.post("/videos/{video_id}/like", response_model=APIResponse)
async def like_video(
    video_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Like a video (toggle - like/unlike)
    """
    db = get_database()
    user_id = str(current_user["_id"])
    
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
    
    # Check if user already liked
    existing_like = await db.interactions.find_one({
        "user_id": user_id,
        "video_id": video_id,
        "interaction_type": "like"
    })
    
    # Remove dislike if exists
    existing_dislike = await db.interactions.find_one({
        "user_id": user_id,
        "video_id": video_id,
        "interaction_type": "dislike"
    })
    
    if existing_dislike:
        await db.interactions.delete_one({"_id": existing_dislike["_id"]})
        await db.videos.update_one(
            {"_id": ObjectId(video_id)},
            {"$inc": {"dislikes": -1}}
        )
    
    if existing_like:
        # Unlike
        await db.interactions.delete_one({"_id": existing_like["_id"]})
        await db.videos.update_one(
            {"_id": ObjectId(video_id)},
            {"$inc": {"likes": -1}}
        )
        
        updated_video = await db.videos.find_one({"_id": ObjectId(video_id)})
        
        return APIResponse(
            status="success",
            message="Video unliked",
            data={
                "action": "removed",
                "new_count": updated_video["likes"]
            }
        )
    else:
        # Like
        await db.interactions.insert_one({
            "user_id": user_id,
            "video_id": video_id,
            "interaction_type": "like",
            "created_at": datetime.now(timezone.utc)
        })
        await db.videos.update_one(
            {"_id": ObjectId(video_id)},
            {"$inc": {"likes": 1}}
        )
        
        updated_video = await db.videos.find_one({"_id": ObjectId(video_id)})
        
        return APIResponse(
            status="success",
            message="Video liked",
            data={
                "action": "added",
                "new_count": updated_video["likes"]
            }
        )


@router.post("/videos/{video_id}/dislike", response_model=APIResponse)
async def dislike_video(
    video_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Dislike a video (toggle - dislike/undislike)
    """
    db = get_database()
    user_id = str(current_user["_id"])
    
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
    
    # Check if user already disliked
    existing_dislike = await db.interactions.find_one({
        "user_id": user_id,
        "video_id": video_id,
        "interaction_type": "dislike"
    })
    
    # Remove like if exists
    existing_like = await db.interactions.find_one({
        "user_id": user_id,
        "video_id": video_id,
        "interaction_type": "like"
    })
    
    if existing_like:
        await db.interactions.delete_one({"_id": existing_like["_id"]})
        await db.videos.update_one(
            {"_id": ObjectId(video_id)},
            {"$inc": {"likes": -1}}
        )
    
    if existing_dislike:
        # Undislike
        await db.interactions.delete_one({"_id": existing_dislike["_id"]})
        await db.videos.update_one(
            {"_id": ObjectId(video_id)},
            {"$inc": {"dislikes": -1}}
        )
        
        updated_video = await db.videos.find_one({"_id": ObjectId(video_id)})
        
        return APIResponse(
            status="success",
            message="Video undisliked",
            data={
                "action": "removed",
                "new_count": updated_video["dislikes"]
            }
        )
    else:
        # Dislike
        await db.interactions.insert_one({
            "user_id": user_id,
            "video_id": video_id,
            "interaction_type": "dislike",
            "created_at": datetime.now(timezone.utc)
        })
        await db.videos.update_one(
            {"_id": ObjectId(video_id)},
            {"$inc": {"dislikes": 1}}
        )
        
        updated_video = await db.videos.find_one({"_id": ObjectId(video_id)})
        
        return APIResponse(
            status="success",
            message="Video disliked",
            data={
                "action": "added",
                "new_count": updated_video["dislikes"]
            }
        )


@router.post("/videos/{video_id}/save", response_model=APIResponse)
async def save_video(
    video_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Save/bookmark a video (toggle - save/unsave)
    """
    db = get_database()
    user_id = str(current_user["_id"])
    
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
    
    # Check if user already saved
    existing_save = await db.interactions.find_one({
        "user_id": user_id,
        "video_id": video_id,
        "interaction_type": "save"
    })
    
    if existing_save:
        # Unsave
        await db.interactions.delete_one({"_id": existing_save["_id"]})
        await db.videos.update_one(
            {"_id": ObjectId(video_id)},
            {"$inc": {"saved_count": -1}}
        )
        
        updated_video = await db.videos.find_one({"_id": ObjectId(video_id)})
        
        return APIResponse(
            status="success",
            message="Video removed from saved",
            data={
                "action": "removed",
                "new_count": updated_video["saved_count"]
            }
        )
    else:
        # Save
        await db.interactions.insert_one({
            "user_id": user_id,
            "video_id": video_id,
            "interaction_type": "save",
            "created_at": datetime.now(timezone.utc)
        })
        await db.videos.update_one(
            {"_id": ObjectId(video_id)},
            {"$inc": {"saved_count": 1}}
        )
        
        updated_video = await db.videos.find_one({"_id": ObjectId(video_id)})
        
        return APIResponse(
            status="success",
            message="Video saved",
            data={
                "action": "added",
                "new_count": updated_video["saved_count"]
            }
        )


@router.post("/videos/{video_id}/report", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
async def report_video(
    video_id: str,
    report_data: ReportCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Report a video
    """
    db = get_database()
    user_id = str(current_user["_id"])
    
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
    
    # Check if user already reported this video
    existing_report = await db.reports.find_one({
        "reporter_id": user_id,
        "video_id": video_id
    })
    
    if existing_report:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already reported this video"
        )
    
    # Create report
    report_doc = {
        "reporter_id": user_id,
        "video_id": video_id,
        "reason": report_data.reason,
        "details": report_data.details,
        "status": "pending",
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.reports.insert_one(report_doc)
    
    return APIResponse(
        status="success",
        message="Video reported successfully",
        data={
            "report_id": str(result.inserted_id)
        }
    )

