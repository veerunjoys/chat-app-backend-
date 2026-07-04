from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str = Field(..., min_length=4, max_length=128)

class UserLogin(UserBase):
    password: str = Field(..., min_length=1, max_length=128)

class UserOut(UserBase):
    id: int
    is_admin: bool
    is_restricted: bool
    created_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class RoomCreate(BaseModel):
    name: Optional[str] = None
    is_group: bool = False
    member_ids: List[int] = []

class RoomMemberOut(BaseModel):
    user: UserOut

    class Config:
        from_attributes = True

class RoomOut(BaseModel):
    id: int
    name: Optional[str] = None
    is_group: bool
    created_at: datetime
    members: List[RoomMemberOut]

    class Config:
        from_attributes = True

class MessageOut(BaseModel):
    id: int
    room_id: int
    sender_id: int
    sender_username: str
    content: str
    is_announcement: bool
    attachment_url: Optional[str] = None
    attachment_type: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class AnnounceCreate(BaseModel):
    content: Optional[str] = ""
    attachment_url: Optional[str] = None
    attachment_type: Optional[str] = None
