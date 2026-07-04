import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import User, Room, RoomMember, Message
from app.schemas import UserOut, RoomOut, RoomCreate, MessageOut
from app.deps import get_current_user

router = APIRouter(prefix="/api/chats", tags=["chats"])

@router.get("/users", response_model=List[UserOut])
def get_users(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Return all registered users except the current logged-in user
    return db.query(User).filter(User.id != current_user.id).all()

@router.get("/rooms", response_model=List[RoomOut])
def get_rooms(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Ensure Global Room exists
    global_room = db.query(Room).filter(Room.is_group == True, Room.name == "Global Chat").first()
    if not global_room:
        global_room = Room(name="Global Chat", is_group=True)
        db.add(global_room)
        db.commit()
        db.refresh(global_room)
    
    # Ensure the user is a member of the Global Room
    member = db.query(RoomMember).filter(
        RoomMember.room_id == global_room.id,
        RoomMember.user_id == current_user.id
    ).first()
    if not member:
        member = RoomMember(room_id=global_room.id, user_id=current_user.id)
        db.add(member)
        db.commit()
    
    # Fetch rooms that the current user is a member of
    memberships = db.query(RoomMember).filter(RoomMember.user_id == current_user.id).all()
    room_ids = [m.room_id for m in memberships]
    rooms = db.query(Room).filter(Room.id.in_(room_ids)).all()
    return rooms

@router.post("/rooms", response_model=RoomOut)
def create_room(room_in: RoomCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not room_in.is_group:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use /rooms/private endpoint to create a 1-to-1 chat"
        )
    
    new_room = Room(name=room_in.name or "Unnamed Group", is_group=True)
    db.add(new_room)
    db.commit()
    db.refresh(new_room)
    
    # Add creator as room member
    creator_member = RoomMember(room_id=new_room.id, user_id=current_user.id)
    db.add(creator_member)
    
    # Add initial group members
    for u_id in room_in.member_ids:
        if u_id == current_user.id:
            continue
        user_exists = db.query(User).filter(User.id == u_id).first()
        if user_exists:
            member = RoomMember(room_id=new_room.id, user_id=u_id)
            db.add(member)
            
    db.commit()
    db.refresh(new_room)
    return new_room

@router.post("/rooms/private", response_model=RoomOut)
def get_or_create_private_room(other_user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    other_user = db.query(User).filter(User.id == other_user_id).first()
    if not other_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Other user not found"
        )
    
    if other_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot start a private chat with yourself"
        )
        
    # Query for an existing 1-to-1 private chat room between the two users
    my_memberships = db.query(RoomMember).filter(RoomMember.user_id == current_user.id).all()
    my_room_ids = [m.room_id for m in my_memberships]
    
    room = db.query(Room).join(RoomMember).filter(
        Room.id.in_(my_room_ids),
        Room.is_group == False,
        RoomMember.user_id == other_user_id
    ).first()
    
    if room:
        return room
        
    # Create new 1-to-1 private room
    new_room = Room(is_group=False)
    db.add(new_room)
    db.commit()
    db.refresh(new_room)
    
    # Add members
    m1 = RoomMember(room_id=new_room.id, user_id=current_user.id)
    m2 = RoomMember(room_id=new_room.id, user_id=other_user_id)
    db.add_all([m1, m2])
    db.commit()
    db.refresh(new_room)
    return new_room

@router.get("/rooms/{room_id}/messages", response_model=List[MessageOut])
def get_room_messages(room_id: int, limit: int = 50, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Verify user membership in the requested room
    is_member = db.query(RoomMember).filter(
        RoomMember.room_id == room_id,
        RoomMember.user_id == current_user.id
    ).first()
    
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view messages in this room"
        )
        
    messages = db.query(Message).filter(Message.room_id == room_id).order_by(Message.created_at.asc()).limit(limit).all()
    
    # Map to schema-friendly structures containing sender_username
    out_messages = []
    for msg in messages:
      out_messages.append(MessageOut(
          id=msg.id,
          room_id=msg.room_id,
          sender_id=msg.sender_id,
          sender_username=msg.sender.username if msg.sender else "Unknown User",
          content=msg.content,
          is_announcement=msg.is_announcement,
          attachment_url=msg.attachment_url,
          attachment_type=msg.attachment_type,
          created_at=msg.created_at
      ))
    return out_messages

@router.post("/upload")
def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """Upload an image or voice recording (stored locally). Accessible to all authenticated users."""
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

@router.post("/rooms/{room_id}/members", response_model=RoomOut)
def add_room_member(
    room_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Add a user to a group. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can manage group members"
        )
        
    room = db.query(Room).filter(Room.id == room_id, Room.is_group == True).first()
    if not room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group chat not found")
        
    user_to_add = db.query(User).filter(User.id == user_id).first()
    if not user_to_add:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
    existing = db.query(RoomMember).filter(
        RoomMember.room_id == room_id,
        RoomMember.user_id == user_id
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is already a member of this group")
        
    member = RoomMember(room_id=room_id, user_id=user_id)
    db.add(member)
    db.commit()
    db.refresh(room)
    return room

@router.delete("/rooms/{room_id}/members/{user_id}", response_model=RoomOut)
def remove_room_member(
    room_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Remove a user from a group. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can manage group members"
        )
        
    room = db.query(Room).filter(Room.id == room_id, Room.is_group == True).first()
    if not room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group chat not found")
        
    member = db.query(RoomMember).filter(
        RoomMember.room_id == room_id,
        RoomMember.user_id == user_id
    ).first()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found in this group")
        
    db.delete(member)
    db.commit()
    db.refresh(room)
    return room

@router.delete("/rooms/{room_id}")
def delete_room(
    room_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a room. Admin only."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete rooms"
        )
        
    room = db.query(Room).filter(Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
        
    db.delete(room)
    db.commit()
    return {"message": "Room deleted successfully"}
