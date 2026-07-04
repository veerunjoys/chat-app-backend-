import socketio
from app.security import decode_access_token
from app.database import SessionLocal
from app.models import User, RoomMember, Message, Room

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

# active_connections: sid -> {"user_id": int, "username": str}
active_connections = {}
# user_connections: user_id -> set of sids
user_connections = {}

@sio.event
async def connect(sid, environ, auth):
    print(f"[Socket] Connection attempt by sid: {sid}")
    if not auth or "token" not in auth:
        print("[Socket] Rejected: no auth token")
        return False

    payload = decode_access_token(auth["token"])
    if not payload:
        print("[Socket] Rejected: invalid token")
        return False

    user_id_str = payload.get("sub")
    if not user_id_str:
        return False

    try:
        user_id = int(user_id_str)
    except ValueError:
        return False

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            print("[Socket] Rejected: user not found")
            return False

        active_connections[sid] = {
            "user_id": user.id,
            "username": user.username,
            "is_restricted": user.is_restricted
        }

        if user.id not in user_connections:
            user_connections[user.id] = set()
        user_connections[user.id].add(sid)

        print(f"[Socket] Connected: {user.username} (id:{user.id}) sid:{sid}")

        # Join all rooms the user is a member of
        memberships = db.query(RoomMember).filter(RoomMember.user_id == user.id).all()
        for m in memberships:
            await sio.enter_room(sid, str(m.room_id))

        # Immediately inform this client of their restriction status
        await sio.emit("user_restricted", {"is_restricted": user.is_restricted}, to=sid)

    except Exception as e:
        print(f"[Socket] Connect error: {e}")
        return False
    finally:
        db.close()

    return True

@sio.event
async def disconnect(sid):
    if sid in active_connections:
        info = active_connections.pop(sid)
        user_id = info["user_id"]
        if user_id in user_connections:
            user_connections[user_id].discard(sid)
            if not user_connections[user_id]:
                user_connections.pop(user_id)
        print(f"[Socket] Disconnected: {info['username']} sid:{sid}")

@sio.event
async def join_room(sid, data):
    room_id = data.get("room_id")
    if not room_id:
        return
    user_info = active_connections.get(sid)
    if not user_info:
        return

    db = SessionLocal()
    try:
        is_member = db.query(RoomMember).filter(
            RoomMember.room_id == int(room_id),
            RoomMember.user_id == user_info["user_id"]
        ).first()
        if is_member:
            await sio.enter_room(sid, str(room_id))
    finally:
        db.close()

@sio.event
async def leave_room(sid, data):
    room_id = data.get("room_id")
    if room_id:
        await sio.leave_room(sid, str(room_id))

@sio.event
async def send_message(sid, data):
    room_id = data.get("room_id")
    content = data.get("content", "").strip()
    attachment_url = data.get("attachment_url")
    attachment_type = data.get("attachment_type")

    if not room_id or (not content and not attachment_url):
        return

    user_info = active_connections.get(sid)
    if not user_info:
        return

    user_id = user_info["user_id"]

    db = SessionLocal()
    try:
        # Re-check restriction from DB (most up-to-date)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return

        if user.is_restricted:
            # Tell only this user their message was blocked
            await sio.emit("send_error", {
                "message": "You have been restricted by an admin and cannot send messages."
            }, to=sid)
            return

        # Verify room membership
        is_member = db.query(RoomMember).filter(
            RoomMember.room_id == int(room_id),
            RoomMember.user_id == user_id
        ).first()
        if not is_member:
            return

        # Fetch room to check group status
        room = db.query(Room).filter(Room.id == int(room_id)).first()
        if not room:
            return

        # If room is a group and user is not admin, prevent attachments
        if room.is_group and not user.is_admin:
            if attachment_url or attachment_type:
                await sio.emit("send_error", {
                    "message": "Only admins are allowed to send images and voice notes in group chats."
                }, to=sid)
                return

        # Ensure all connected room members are in the socket room
        room_members = db.query(RoomMember).filter(RoomMember.room_id == int(room_id)).all()
        for member in room_members:
            if member.user_id in user_connections:
                for member_sid in user_connections[member.user_id]:
                    await sio.enter_room(member_sid, str(room_id))

        # Persist message
        msg = Message(
            room_id=int(room_id),
            sender_id=user_id,
            content=content,
            is_announcement=False,
            attachment_url=attachment_url,
            attachment_type=attachment_type
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)

        payload = {
            "id": msg.id,
            "room_id": msg.room_id,
            "sender_id": msg.sender_id,
            "sender_username": user_info["username"],
            "content": msg.content,
            "is_announcement": False,
            "attachment_url": msg.attachment_url,
            "attachment_type": msg.attachment_type,
            "created_at": (msg.created_at.isoformat() + "Z") if msg.created_at else None
        }

        # Broadcast to entire room
        await sio.emit("message", payload, room=str(room_id))
        print(f"[Socket] Message from {user_info['username']} in room {room_id}")

    except Exception as e:
        print(f"[Socket] send_message error: {e}")
    finally:
        db.close()

@sio.event
async def typing(sid, data):
    room_id = data.get("room_id")
    is_typing = data.get("is_typing", False)
    if not room_id:
        return

    user_info = active_connections.get(sid)
    if not user_info:
        return

    # Restricted users cannot emit typing indicators either
    if user_info.get("is_restricted"):
        return

    payload = {
        "room_id": int(room_id),
        "user_id": user_info["user_id"],
        "username": user_info["username"],
        "is_typing": is_typing
    }
    await sio.emit("typing", payload, room=str(room_id), skip_sid=sid)
