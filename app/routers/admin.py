import os
import uuid
import asyncio
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import User, Room, RoomMember, Message
from app.schemas import UserOut, AnnounceCreate, MessageOut
from app.deps import get_current_user
from app.sockets.chat import sio, active_connections, user_connections

router = APIRouter(prefix="/api/admin", tags=["admin"])

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user

@router.get("/users", response_model=List[UserOut])
def list_all_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """Return all registered users."""
    return db.query(User).order_by(User.created_at.asc()).all()

@router.patch("/users/{user_id}/restrict")
def toggle_restriction(
    user_id: int,
    restrict: bool,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """Restrict or unrestrict a user from sending messages."""
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot restrict yourself"
        )
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.is_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot restrict another admin"
        )

    target.is_restricted = restrict
    db.commit()
    db.refresh(target)

    # Notify the user in real-time via socket if they are connected
    if user_id in user_connections:
        for sid in user_connections[user_id]:
            asyncio.create_task(
                sio.emit("user_restricted", {"is_restricted": restrict}, to=sid)
            )

    return {"user_id": user_id, "is_restricted": restrict}

@router.post("/announce", response_model=MessageOut)
def post_announcement(
    body: AnnounceCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    """Post a system announcement to Global Chat visible to all users."""
    global_room = db.query(Room).filter(
        Room.is_group == True, Room.name == "Global Chat"
    ).first()
    if not global_room:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Global Chat room not found"
        )

    msg = Message(
        room_id=global_room.id,
        sender_id=admin.id,
        content=body.content or "",
        is_announcement=True,
        attachment_url=body.attachment_url,
        attachment_type=body.attachment_type
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    payload = {
        "id": msg.id,
        "room_id": msg.room_id,
        "sender_id": msg.sender_id,
        "sender_username": admin.username,
        "content": msg.content,
        "is_announcement": True,
        "attachment_url": msg.attachment_url,
        "attachment_type": msg.attachment_type,
        "created_at": (msg.created_at.isoformat() + "Z") if msg.created_at else None
    }

    # Broadcast to all clients in the global room via socket
    asyncio.create_task(
        sio.emit("message", payload, room=str(global_room.id))
    )

    return MessageOut(
        id=msg.id,
        room_id=msg.room_id,
        sender_id=msg.sender_id,
        sender_username=admin.username,
        content=msg.content,
        is_announcement=True,
        attachment_url=msg.attachment_url,
        attachment_type=msg.attachment_type,
        created_at=msg.created_at
    )

@router.post("/upload")
def upload_file(
    file: UploadFile = File(...),
    admin: User = Depends(require_admin)
):
    """Upload an image or voice recording (stored locally). Only accessible to admins."""
    content_type = file.content_type or ""
    filename = file.filename or "file"
    ext = os.path.splitext(filename)[1].lower()
    
    # Simple validation for image/voice files
    file_type = None
    if content_type.startswith("image/") or ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]:
        file_type = "image"
    elif content_type.startswith("audio/") or ext in [".mp3", ".wav", ".ogg", ".m4a", ".aac", ".webm"]:
        file_type = "voice"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Only images and audio files are allowed."
        )

    # Generate a unique secure file name
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    
    # Store in the server's uploads folder
    uploads_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "uploads"
    )
    if not os.path.exists(uploads_dir):
        os.makedirs(uploads_dir)
        
    file_path = os.path.join(uploads_dir, unique_filename)
    
    try:
        # Save file to disk
        with open(file_path, "wb") as buffer:
            content = file.file.read()
            buffer.write(content)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save file: {str(e)}"
        )
        
    return {
        "url": f"/uploads/{unique_filename}",
        "type": file_type
    }
