import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Voice/text-controlled Roku system using LLMs"
)

cors_origins = [f"{settings.hostname}"]

# Configure CORS based on environment
if settings.environment == "production":
    cors_allow_credentials = False
    cors_allow_methods = ["GET", "POST", "PUT", "DELETE"]
    cors_allow_headers = ["Content-Type", "Authorization", "Accept"]
    cors_max_age = 86400  # 24 hours
else:
    cors_allow_credentials = True
    cors_allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    cors_allow_headers = ["*"]
    cors_max_age = 0  # No caching in development

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=cors_allow_methods,
    allow_headers=cors_allow_headers,
    max_age=cors_max_age,
)

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    )
