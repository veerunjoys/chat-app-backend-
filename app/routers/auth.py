from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Room, RoomMember
from app.schemas import UserCreate, UserLogin, UserOut
from app.security import hash_password, verify_password, create_access_token
from app.deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

def _ensure_global_chat_member(db: Session, user_id: int):
    """Create Global Chat room if needed and add the user as a member."""
    global_room = db.query(Room).filter(
        Room.is_group == True, Room.name == "Global Chat"
    ).first()
    if not global_room:
        global_room = Room(name="Global Chat", is_group=True)
        db.add(global_room)
        db.commit()
        db.refresh(global_room)

    member = db.query(RoomMember).filter(
        RoomMember.room_id == global_room.id,
        RoomMember.user_id == user_id
    ).first()
    if not member:
        member = RoomMember(room_id=global_room.id, user_id=user_id)
        db.add(member)
        db.commit()

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    # Validate username length
    username = user_in.username.strip()
    if len(username) < 3 or len(username) > 32:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must be between 3 and 32 characters"
        )
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )

    # First ever user becomes admin
    is_first_user = db.query(User).count() == 0
    hashed = hash_password(user_in.password)
    new_user = User(
        username=username,
        password_hash=hashed,
        is_admin=is_first_user
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Auto-add to Global Chat
    _ensure_global_chat_member(db, new_user.id)
    return new_user

@router.post("/login")
def login(user_in: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == user_in.username.strip()).first()
    if not user or not verify_password(user_in.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"}
        )

    if user.is_restricted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been restricted. Contact an admin."
        )

    # Auto-add to Global Chat on login too (in case they missed registration step)
    _ensure_global_chat_member(db, user.id)

    access_token = create_access_token(data={"sub": str(user.id)})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "is_admin": user.is_admin,
            "is_restricted": user.is_restricted,
            "created_at": (user.created_at.isoformat() + "Z") if user.created_at else None
        }
    }

@router.get("/me", response_model=UserOut)
def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user
