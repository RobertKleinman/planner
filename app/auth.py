"""
auth.py â API Key Authentication
==================================
Uses SHA-256 hashing for API keys instead of bcrypt.
(bcrypt has compatibility issues with passlib on Windows.)

For API keys (long random strings), SHA-256 is perfectly secure.
bcrypt's intentional slowness is designed for short human passwords,
which isn't needed here.
"""

import hashlib
import hmac
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def hash_api_key(api_key: str) -> str:
    """Hash an API key for storage using SHA-256."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def verify_api_key(plain_key: str, hashed_key: str) -> bool:
    """Check if a plain API key matches a hashed one."""
    return hmac.compare_digest(
        hashlib.sha256(plain_key.encode()).hexdigest(),
        hashed_key
    )


async def get_current_user(
    api_key: str = Security(api_key_header),
    db: Session = Depends(get_db),
) -> User:
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header.")

    # O(1) lookup: hash the key and query directly instead of loading all users
    key_hash = hash_api_key(api_key)
    user = db.query(User).filter(User.api_key_hash == key_hash, User.is_active == True).first()
    if user:
        return user

    raise HTTPException(status_code=401, detail="Invalid API key.")
