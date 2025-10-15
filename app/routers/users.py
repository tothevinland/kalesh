from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File
from datetime import timedelta
from bson import ObjectId
from app.schemas import UserRegister, UserLogin, Token, UserProfile, UserProfileUpdate, APIResponse
from app.auth import get_password_hash, verify_password, create_access_token, get_current_user
from app.database import get_database
from app.config import settings
from app.utils.storage import r2_storage

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/register", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
async def register_user(user_data: UserRegister):
    """
    Register a new user - only username and password required
    """
    db = get_database()
    
    # Check if username already exists
    existing_username = await db.users.find_one({"username": user_data.username})
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken"
        )
    
    # Create user
    user_dict = {
        "username": user_data.username,
        "email": None,
        "hashed_password": get_password_hash(user_data.password),
        "full_name": None,
        "bio": None,
        "profile_image_url": None,
        "is_active": True
    }
    
    from datetime import datetime, timezone
    user_dict["created_at"] = datetime.now(timezone.utc)
    
    result = await db.users.insert_one(user_dict)
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(result.inserted_id)}, expires_delta=access_token_expires
    )
    
    return APIResponse(
        status="success",
        message="User registered successfully",
        data={
            "access_token": access_token,
            "token_type": "bearer",
            "user": {
                "id": str(result.inserted_id),
                "username": user_data.username
            }
        }
    )


@router.post("/login", response_model=APIResponse)
async def login_user(user_data: UserLogin):
    """
    Login user with username and password
    """
    db = get_database()
    
    # Find user by username
    user = await db.users.find_one({"username": user_data.username})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    # Verify password
    if not verify_password(user_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    # Check if user is active
    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is inactive"
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user["_id"])}, expires_delta=access_token_expires
    )
    
    return APIResponse(
        status="success",
        message="Login successful",
        data={
            "access_token": access_token,
            "token_type": "bearer",
            "user": {
                "id": str(user["_id"]),
                "username": user["username"]
            }
        }
    )


@router.get("/profile", response_model=APIResponse)
async def get_user_profile(current_user: dict = Depends(get_current_user)):
    """
    Get current user's profile
    """
    profile = UserProfile(
        id=str(current_user["_id"]),
        username=current_user["username"],
        email=current_user.get("email"),
        full_name=current_user.get("full_name"),
        bio=current_user.get("bio"),
        profile_image_url=current_user.get("profile_image_url"),
        created_at=current_user["created_at"]
    )
    
    return APIResponse(
        status="success",
        message="Profile retrieved successfully",
        data={"profile": profile.model_dump()}
    )


@router.get("/profile/{username}", response_model=APIResponse)
async def get_user_profile_by_username(username: str):
    """
    Get user profile by username (public endpoint)
    """
    db = get_database()
    
    user = await db.users.find_one({"username": username})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    profile = UserProfile(
        id=str(user["_id"]),
        username=user["username"],
        email=user.get("email"),
        full_name=user.get("full_name"),
        bio=user.get("bio"),
        profile_image_url=user.get("profile_image_url"),
        created_at=user["created_at"]
    )
    
    return APIResponse(
        status="success",
        message="Profile retrieved successfully",
        data={"profile": profile.model_dump()}
    )


@router.put("/profile", response_model=APIResponse)
async def update_user_profile(
    profile_data: UserProfileUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update current user's profile information
    """
    db = get_database()
    
    # Build update dict (only include fields that are provided)
    update_fields = {}
    if profile_data.email is not None:
        update_fields["email"] = profile_data.email
    if profile_data.full_name is not None:
        update_fields["full_name"] = profile_data.full_name
    if profile_data.bio is not None:
        update_fields["bio"] = profile_data.bio
    
    # If no fields to update, return current profile
    if not update_fields:
        updated_user = current_user
    else:
        # Update user in database
        await db.users.update_one(
            {"_id": ObjectId(current_user["_id"])},
            {"$set": update_fields}
        )
        
        # Fetch updated user
        updated_user = await db.users.find_one({"_id": ObjectId(current_user["_id"])})
    
    # Return updated profile
    profile = UserProfile(
        id=str(updated_user["_id"]),
        username=updated_user["username"],
        email=updated_user.get("email"),
        full_name=updated_user.get("full_name"),
        bio=updated_user.get("bio"),
        profile_image_url=updated_user.get("profile_image_url"),
        created_at=updated_user["created_at"]
    )
    
    return APIResponse(
        status="success",
        message="Profile updated successfully",
        data={"profile": profile.model_dump()}
    )


@router.post("/avatar/upload", response_model=APIResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload or update user's profile avatar/image
    """
    # Validate file type
    allowed_image_types = ["image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"]
    if file.content_type not in allowed_image_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Only JPEG, PNG, GIF, and WebP images are allowed"
        )
    
    # Validate file size (max 5MB for avatars)
    max_size = 5 * 1024 * 1024  # 5MB
    file_data = await file.read()
    if len(file_data) > max_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size too large. Maximum size is 5MB"
        )
    
    try:
        # Upload to R2
        avatar_url = await r2_storage.upload_file(
            file_data=file_data,
            filename=f"avatar_{current_user['username']}_{file.filename}",
            content_type=file.content_type
        )
        
        # Update user's profile_image_url in database
        db = get_database()
        await db.users.update_one(
            {"_id": ObjectId(current_user["_id"])},
            {"$set": {"profile_image_url": avatar_url}}
        )
        
        return APIResponse(
            status="success",
            message="Avatar uploaded successfully",
            data={"profile_image_url": avatar_url}
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload avatar"
        )

