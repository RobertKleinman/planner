"""
modules/base.py — Module Interface
====================================
Defines the pattern every module follows. Adding a new module means:
1. Add the intent to the LLM prompt (intent.py)
2. Create a new module file following this pattern
3. Register it in the dispatcher (routers/input.py)

That's it. Database schema, API endpoint, and Shortcuts all stay the same.

NOTE ON PYTHON TYPING:
The `Protocol` class below is Python's way of defining an interface — it
says "any module must have a function called `handle` with this signature."
It's optional (Python is flexible) but helps catch mistakes early.
"""

from typing import Protocol
from sqlalchemy.orm import Session
from app.models import User
from app.schemas import InputResponse


class ModuleHandler(Protocol):
    """The interface every module handler should follow."""

    async def handle(
        self,
        user: User,
        raw_input: str,         # Raw transcript or image description
        intent_data: dict,       # Full structured output from the LLM
        db: Session,
        image_bytes: bytes = None,  # Original image if applicable
    ) -> InputResponse:
        """Process the classified input and return a response."""
        ...
