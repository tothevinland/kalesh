from fastapi import APIRouter, HTTPException, status, Depends, Query, Request
from typing import Optional
from bson import ObjectId
from datetime import datetime, timezone
from app.schemas import CommentCreate, CommentResponse, CommentWithReplies, APIResponse, ReportCreate
from app.auth import get_current_user, get_current_user_optional
from app.database import get_database
from app.utils.datetime_helper import format_datetime_response
from app.utils.rate_limit import (
    limiter,
    RATE_LIMIT_COMMENT_CREATE,
    RATE_LIMIT_COMMENT_REPLY,
    RATE_LIMIT_COMMENT_LIKE,
    RATE_LIMIT_COMMENT_DELETE,
    RATE_LIMIT_REPORT,
    RATE_LIMIT_READ
)

router = APIRouter(prefix="/comments", tags=["comments"])


@router.post("/videos/{video_id}", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_COMMENT_CREATE)
async def create_comment(
    request: Request,
    video_id: str,
    comment_data: CommentCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a comment on a video
    Rate limit: 20 per hour per IP (STRICT anti-spam)
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
    
    # Create comment
    comment_doc = {
        "video_id": video_id,
        "user_id": str(current_user["_id"]),
        "username": current_user["username"],
        "text": comment_data.text,
        "parent_comment_id": None,
        "likes": 0,
        "replies_count": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "is_active": True
    }
    
    result = await db.comments.insert_one(comment_doc)
    
    comment_response = CommentResponse(
        id=str(result.inserted_id),
        video_id=comment_doc["video_id"],
        user_id=comment_doc["user_id"],
        username=comment_doc["username"],
        text=comment_doc["text"],
        parent_comment_id=None,
        likes=0,
        replies_count=0,
        created_at=format_datetime_response(comment_doc["created_at"]),
        user_liked=False
    )
    
    return APIResponse(
        status="success",
        message="Comment created successfully",
        data={"comment": comment_response.model_dump()}
    )


@router.post("/{comment_id}/reply", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_COMMENT_REPLY)
async def reply_to_comment(
    request: Request,
    comment_id: str,
    reply_data: CommentCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Reply to a comment (only 1 level deep - no nested replies)
    Rate limit: 30 per hour per IP (STRICT anti-spam)
    """
    db = get_database()
    
    # Validate comment_id
    if not ObjectId.is_valid(comment_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid comment ID"
        )
    
    # Get parent comment
    parent_comment = await db.comments.find_one({"_id": ObjectId(comment_id), "is_active": True})
    if not parent_comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found"
        )
    
    # Check if parent is already a reply (no nested replies allowed)
    if parent_comment.get("parent_comment_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reply to a reply. Only 1 level of replies allowed."
        )
    
    # Create reply
    reply_doc = {
        "video_id": parent_comment["video_id"],
        "user_id": str(current_user["_id"]),
        "username": current_user["username"],
        "text": reply_data.text,
        "parent_comment_id": comment_id,
        "likes": 0,
        "replies_count": 0,  # Replies can't have replies
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "is_active": True
    }
    
    result = await db.comments.insert_one(reply_doc)
    
    # Increment parent comment's replies_count
    await db.comments.update_one(
        {"_id": ObjectId(comment_id)},
        {"$inc": {"replies_count": 1}}
    )
    
    reply_response = CommentResponse(
        id=str(result.inserted_id),
        video_id=reply_doc["video_id"],
        user_id=reply_doc["user_id"],
        username=reply_doc["username"],
        text=reply_doc["text"],
        parent_comment_id=comment_id,
        likes=0,
        replies_count=0,
        created_at=format_datetime_response(reply_doc["created_at"]),
        user_liked=False
    )
    
    return APIResponse(
        status="success",
        message="Reply created successfully",
        data={"comment": reply_response.model_dump()}
    )


@router.get("/videos/{video_id}", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_READ)
async def get_video_comments(
    request: Request,
    video_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[dict] = Depends(get_current_user_optional)
):
    """
    Get all comments for a video with their replies
    Rate limit: 500 per hour per IP
    """
    db = get_database()
    
    # Validate video_id
    if not ObjectId.is_valid(video_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid video ID"
        )
    
    skip = (page - 1) * page_size
    
    # Get top-level comments (no parent)
    comments_cursor = db.comments.find({
        "video_id": video_id,
        "parent_comment_id": None,
        "is_active": True
    }).sort("created_at", -1).skip(skip).limit(page_size)
    
    comments = await comments_cursor.to_list(length=page_size)
    
    # Get total count
    total = await db.comments.count_documents({
        "video_id": video_id,
        "parent_comment_id": None,
        "is_active": True
    })
    
    # Format comments with replies
    comments_list = []
    for comment in comments:
        comment_id = str(comment["_id"])
        
        # Check if current user liked this comment
        user_liked = False
        if current_user:
            user_id = str(current_user["_id"])
            like = await db.comment_likes.find_one({
                "comment_id": comment_id,
                "user_id": user_id
            })
            user_liked = like is not None
        
        # Get replies
        replies_cursor = db.comments.find({
            "parent_comment_id": comment_id,
            "is_active": True
        }).sort("created_at", 1).limit(100)  # Limit replies shown
        
        replies = await replies_cursor.to_list(length=100)
        
        replies_list = []
        for reply in replies:
            reply_id = str(reply["_id"])
            reply_user_liked = False
            
            if current_user:
                reply_like = await db.comment_likes.find_one({
                    "comment_id": reply_id,
                    "user_id": user_id
                })
                reply_user_liked = reply_like is not None
            
            reply_response = CommentResponse(
                id=reply_id,
                video_id=reply["video_id"],
                user_id=reply["user_id"],
                username=reply["username"],
                text=reply["text"],
                parent_comment_id=reply.get("parent_comment_id"),
                likes=reply["likes"],
                replies_count=0,
                created_at=format_datetime_response(reply["created_at"]),
                user_liked=reply_user_liked
            )
            replies_list.append(reply_response.model_dump())
        
        comment_with_replies = CommentWithReplies(
            id=comment_id,
            video_id=comment["video_id"],
            user_id=comment["user_id"],
            username=comment["username"],
            text=comment["text"],
            likes=comment["likes"],
            replies_count=comment["replies_count"],
            created_at=format_datetime_response(comment["created_at"]),
            user_liked=user_liked,
            replies=replies_list
        )
        comments_list.append(comment_with_replies.model_dump())
    
    return APIResponse(
        status="success",
        message="Comments retrieved successfully",
        data={
            "comments": comments_list,
            "total": total,
            "page": page,
            "page_size": page_size
        }
    )


@router.post("/{comment_id}/like", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_COMMENT_LIKE)
async def like_comment(
    request: Request,
    comment_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Like or unlike a comment (toggle)
    Rate limit: 60 per hour per IP
    """
    db = get_database()
    user_id = str(current_user["_id"])
    
    # Validate comment_id
    if not ObjectId.is_valid(comment_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid comment ID"
        )
    
    # Check if comment exists
    comment = await db.comments.find_one({"_id": ObjectId(comment_id), "is_active": True})
    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found"
        )
    
    # Check if already liked
    existing_like = await db.comment_likes.find_one({
        "comment_id": comment_id,
        "user_id": user_id
    })
    
    if existing_like:
        # Unlike
        await db.comment_likes.delete_one({"_id": existing_like["_id"]})
        await db.comments.update_one(
            {"_id": ObjectId(comment_id)},
            {"$inc": {"likes": -1}}
        )
        
        updated_comment = await db.comments.find_one({"_id": ObjectId(comment_id)})
        
        return APIResponse(
            status="success",
            message="Comment unliked",
            data={
                "action": "removed",
                "new_count": updated_comment["likes"]
            }
        )
    else:
        # Like
        await db.comment_likes.insert_one({
            "comment_id": comment_id,
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc)
        })
        await db.comments.update_one(
            {"_id": ObjectId(comment_id)},
            {"$inc": {"likes": 1}}
        )
        
        updated_comment = await db.comments.find_one({"_id": ObjectId(comment_id)})
        
        return APIResponse(
            status="success",
            message="Comment liked",
            data={
                "action": "added",
                "new_count": updated_comment["likes"]
            }
        )


@router.delete("/{comment_id}", response_model=APIResponse)
@limiter.limit(RATE_LIMIT_COMMENT_DELETE)
async def delete_comment(
    request: Request,
    comment_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a comment (only owner can delete)
    Rate limit: 20 per hour per IP
    """
    db = get_database()
    
    # Validate comment_id
    if not ObjectId.is_valid(comment_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid comment ID"
        )
    
    # Get comment
    comment = await db.comments.find_one({"_id": ObjectId(comment_id), "is_active": True})
    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found"
        )
    
    # Check if current user is the owner
    if comment["user_id"] != str(current_user["_id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own comments"
        )
    
    # If it's a top-level comment, also mark all replies as inactive
    if not comment.get("parent_comment_id"):
        await db.comments.update_many(
            {"parent_comment_id": comment_id},
            {"$set": {"is_active": False}}
        )
    else:
        # If it's a reply, decrement parent's replies_count
        await db.comments.update_one(
            {"_id": ObjectId(comment["parent_comment_id"])},
            {"$inc": {"replies_count": -1}}
        )
    
    # Mark comment as inactive (soft delete)
    await db.comments.update_one(
        {"_id": ObjectId(comment_id)},
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}}
    )
    
    # Delete all likes on this comment
    await db.comment_likes.delete_many({"comment_id": comment_id})
    
    return APIResponse(
        status="success",
        message="Comment deleted successfully",
        data=None
    )


@router.post("/{comment_id}/report", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(RATE_LIMIT_REPORT)
async def report_comment(
    request: Request,
    comment_id: str,
    report_data: ReportCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Report a comment
    Rate limit: 10 per hour per IP (STRICT)
    """
    db = get_database()
    user_id = str(current_user["_id"])
    
    # Validate comment_id
    if not ObjectId.is_valid(comment_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid comment ID"
        )
    
    # Check if comment exists
    comment = await db.comments.find_one({"_id": ObjectId(comment_id), "is_active": True})
    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found"
        )
    
    # Check if user already reported this comment
    existing_report = await db.comment_reports.find_one({
        "comment_id": comment_id,
        "reporter_id": user_id
    })
    
    if existing_report:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already reported this comment"
        )
    
    # Create report
    report_doc = {
        "comment_id": comment_id,
        "reporter_id": user_id,
        "reason": report_data.reason,
        "details": report_data.details,
        "status": "pending",
        "created_at": datetime.now(timezone.utc)
    }
    
    result = await db.comment_reports.insert_one(report_doc)
    
    return APIResponse(
        status="success",
        message="Comment reported successfully",
        data={
            "report_id": str(result.inserted_id)
        }
    )

