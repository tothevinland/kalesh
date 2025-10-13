from fastapi import APIRouter, HTTPException, status, Depends
from datetime import timedelta
from bson import ObjectId
from app.schemas import UserRegister, UserLogin, Token, UserProfile, APIResponse
from app.auth import get_password_hash, verify_password, create_access_token, get_current_user
from app.database import get_database
from app.config import settings

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
    
    from datetime import datetime
    user_dict["created_at"] = datetime.utcnow()
    
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

