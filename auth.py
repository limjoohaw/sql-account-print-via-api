"""Authentication: user management, bcrypt password hashing, session cookies."""

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from typing import Optional

import bcrypt as _bcrypt
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

from config import settings

_users_lock = threading.Lock()

SESSION_COOKIE_NAME = "session"
SESSION_MAX_AGE = 43200  # 12 hours in seconds


@dataclass
class User:
    username: str
    password_hash: str
    companies: list = field(default_factory=list)
    is_admin: bool = False


def _load_users_raw() -> list[dict]:
    """Load raw user dicts from users.json."""
    path = settings.users_path
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_users_raw(users: list[dict]):
    """Save raw user dicts to users.json."""
    path = settings.users_path
    with open(path, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)


def load_users() -> list[User]:
    """Load all users from users.json."""
    with _users_lock:
        return [User(**u) for u in _load_users_raw()]


def save_users(users: list[User]):
    """Save all users to users.json."""
    with _users_lock:
        _save_users_raw([asdict(u) for u in users])


def find_user(username: str) -> Optional[User]:
    """Find a user by username (case-insensitive)."""
    for u in load_users():
        if u.username.lower() == username.lower():
            return u
    return None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return _bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def hash_password(plain_password: str) -> str:
    """Hash a password with bcrypt."""
    return _bcrypt.hashpw(
        plain_password.encode("utf-8"),
        _bcrypt.gensalt(),
    ).decode("utf-8")


def create_user(username: str, password: str, companies: list,
                is_admin: bool = False) -> User:
    """Create a new user and save to users.json."""
    users = load_users()
    # Check for duplicate username
    for u in users:
        if u.username.lower() == username.lower():
            raise ValueError(f"Username '{username}' already exists")

    new_user = User(
        username=username,
        password_hash=hash_password(password),
        companies=companies,
        is_admin=is_admin,
    )
    users.append(new_user)
    save_users(users)
    return new_user


def update_user(username: str, **kwargs) -> Optional[User]:
    """Update a user's fields and save. Pass password= to change password."""
    users = load_users()
    for u in users:
        if u.username.lower() == username.lower():
            if "password" in kwargs:
                u.password_hash = hash_password(kwargs.pop("password"))
            if "companies" in kwargs:
                u.companies = kwargs["companies"]
            if "is_admin" in kwargs:
                u.is_admin = kwargs["is_admin"]
            save_users(users)
            return u
    return None


def delete_user(username: str) -> bool:
    """Delete a user by username."""
    users = load_users()
    original_len = len(users)
    users = [u for u in users if u.username.lower() != username.lower()]
    if len(users) < original_len:
        save_users(users)
        return True
    return False


# --- Session management via itsdangerous ---

def _get_signer() -> TimestampSigner:
    return TimestampSigner(settings.session_secret)


def create_session_token(username: str) -> str:
    """Create a signed session token for a username."""
    return _get_signer().sign(username).decode("utf-8")


def validate_session_token(token: str) -> Optional[str]:
    """Validate a session token, return username or None if invalid/expired."""
    try:
        username = _get_signer().unsign(token, max_age=SESSION_MAX_AGE)
        return username.decode("utf-8")
    except (BadSignature, SignatureExpired):
        return None
