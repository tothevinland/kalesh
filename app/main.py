from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.database import connect_to_mongo, close_mongo_connection
from app.routers import users, videos, interactions, feeds, comments
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await connect_to_mongo()
    
    # Start video processing queue worker
    from app.utils.video_queue import video_queue
    video_queue.start_worker()
    
    yield
    
    # Shutdown
    await video_queue.stop_worker()
    await close_mongo_connection()


app = FastAPI(
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this based on your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Sanitize error messages to avoid exposing sensitive information
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "status": "error",
            "message": "An unexpected error occurred",
            "data": None
        }
    )


# Include routers
app.include_router(users.router)
app.include_router(videos.router)
app.include_router(interactions.router)
app.include_router(feeds.router)
app.include_router(comments.router)


@app.get("/")
async def root():
    return "works :)"


@app.get("/health")
async def health_check():
    return {
        "status": "success",
        "message": "API is healthy"
    }

