"""
setup_user.py — Create your user account. Run once after first deploy.

Usage: python setup_user.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import settings
from app.database import engine, SessionLocal, Base
from app.models import User
from app.auth import hash_api_key


def setup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        existing = db.query(User).first()
        if existing:
            print(f"User already exists: {existing.email}")
            print("Delete planner.db and run again to reset.")
            return

        user = User(
            email=input("Enter your email: ").strip(),
            name=input("Enter your name: ").strip(),
            api_key_hash=hash_api_key(settings.planner_api_key),
        )
        db.add(user)
        db.commit()

        print(f"\n✓ User created: {user.name} ({user.email})")
        print(f"✓ API key hashed and stored.")
        print(f"\nStart the server:")
        print(f"  uvicorn app.main:app --reload --port 8000")

    finally:
        db.close()


if __name__ == "__main__":
    setup()
