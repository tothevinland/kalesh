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
    

async def close_mongo_connection():
    global client
    if client:
        client.close()


def get_database():
    return db

