from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request


def get_client_ip(request: Request) -> str:
    """
    Get client IP address from request, checking for proxy headers
    """
    # Check for common proxy headers
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, get the first one (client IP)
        return forwarded_for.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    # Fallback to direct connection IP
    return get_remote_address(request)


# Initialize rate limiter
limiter = Limiter(
    key_func=get_client_ip,
    default_limits=["200 per minute"],  # Default limit for all endpoints
    storage_uri="memory://",  # Use in-memory storage
    headers_enabled=True  # Add rate limit info to response headers
)

# Custom rate limit strings for different endpoint types

# VERY STRICT: Account creation (3 per hour per IP)
RATE_LIMIT_REGISTER = "3 per hour"

# STRICT: Login attempts (10 per hour per IP to prevent brute force)
RATE_LIMIT_LOGIN = "10 per hour"

# VERY STRICT: Comment creation (20 per hour per IP to prevent spam)
RATE_LIMIT_COMMENT_CREATE = "20 per hour"

# MODERATE: Comment replies (30 per hour per IP)
RATE_LIMIT_COMMENT_REPLY = "30 per hour"

# MODERATE: Comment likes (60 per hour per IP)
RATE_LIMIT_COMMENT_LIKE = "60 per hour"

# MODERATE: Comment deletion (20 per hour per IP)
RATE_LIMIT_COMMENT_DELETE = "20 per hour"

# STRICT: Video upload (5 per hour per IP)
RATE_LIMIT_VIDEO_UPLOAD = "5 per hour"

# MODERATE: Video interactions (like, dislike, favorite)
RATE_LIMIT_VIDEO_INTERACTION = "100 per hour"

# STRICT: Reporting (10 per hour per IP)
RATE_LIMIT_REPORT = "10 per hour"

# MODERATE: Profile updates (20 per hour per IP)
RATE_LIMIT_PROFILE_UPDATE = "20 per hour"

# MODERATE: Avatar upload (5 per hour per IP)
RATE_LIMIT_AVATAR_UPLOAD = "5 per hour"

# GENEROUS: Read operations (500 per hour per IP)
RATE_LIMIT_READ = "500 per hour"

