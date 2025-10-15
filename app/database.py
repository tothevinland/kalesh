from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

client = None
db = None


async def connect_to_mongo():
    global client, db
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.MONGODB_DB_NAME]
    
    # Create indexes
    await db.users.create_index("username", unique=True)
    await db.videos.create_index([("created_at", -1)])
    await db.videos.create_index([("likes", -1)])
    await db.videos.create_index([("views", -1)])
    await db.videos.create_index("uploader_id")
    
    # Tag-related indexes
    await db.videos.create_index("tags")  # For tag-based queries
    await db.videos.create_index([("tags", 1), ("created_at", -1)])  # For explore by tag with sorting
    await db.videos.create_index([("tags", 1), ("views", -1)])  # For popular videos by tag
    
    # Comment indexes
    await db.comments.create_index([("video_id", 1), ("created_at", -1)])
    await db.comments.create_index([("parent_comment_id", 1), ("created_at", -1)])
    await db.comments.create_index("user_id")
    await db.comment_likes.create_index([("comment_id", 1), ("user_id", 1)], unique=True)
    await db.comment_reports.create_index("comment_id")
    

async def close_mongo_connection():
    global client
    if client:
        client.close()


def get_database():
    return db

