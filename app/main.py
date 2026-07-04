import os
import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from app.database import engine, Base
from app.routers import auth, chats, admin
from app.sockets.chat import sio

# Create all DB tables
Base.metadata.create_all(bind=engine)

# Programmatically verify and add columns to messages if they don't exist
with engine.connect() as conn:
    dialect_name = engine.dialect.name
    if dialect_name == "postgresql":
        conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS attachment_url VARCHAR;"))
        conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS attachment_type VARCHAR;"))
        conn.commit()
    else:
        # SQLite or other databases
        try:
            conn.execute(text("ALTER TABLE messages ADD COLUMN attachment_url VARCHAR;"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE messages ADD COLUMN attachment_type VARCHAR;"))
            conn.commit()
        except Exception:
            pass

# FastAPI application (REST only)
fastapi_app = FastAPI(
    title="Chime Chat API",
    description="Real-time chat backend with JWT auth and admin controls",
    version="2.0.0",
    # Disable interactive docs in production for security
    docs_url="/docs" if os.getenv("APP_ENV", "development") != "production" else None,
    redoc_url="/redoc" if os.getenv("APP_ENV", "development") != "production" else None,
)

# CORS — read allowed origins from env or default to localhost for local dev
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

# REST routers
fastapi_app.include_router(auth.router)
fastapi_app.include_router(chats.router)
fastapi_app.include_router(admin.router)

# Mount uploads static directory
uploads_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir)

fastapi_app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

@fastapi_app.api_route("/", methods=["GET", "HEAD"])
def read_root():
    return {"status": "ok", "message": "Chime server is running"}

@fastapi_app.api_route("/health", methods=["GET", "HEAD"])
def health_check():
    return {"status": "ok"}

# Wrap FastAPI inside Socket.IO ASGI app
# IMPORTANT: uvicorn must serve THIS `app` object — not `fastapi_app`
socket_app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)

# Alias so `uvicorn app.main:app` works correctly
app = socket_app
