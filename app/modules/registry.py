"""
modules/registry.py — Single source of truth for module→handler mapping
=========================================================================
"""

from app.modules.memo import handle_memo
from app.modules.calendar import handle_calendar
from app.modules.task import handle_task
from app.modules.remember import handle_remember
from app.modules.journal import handle_journal

MODULE_HANDLERS = {
    "memo": handle_memo,
    "calendar": handle_calendar,
    "task": handle_task,
    "remember": handle_remember,
    "journal": handle_journal,
}


def get_handler(module_name: str):
    """Get handler for a module name, falling back to memo for unknown types."""
    return MODULE_HANDLERS.get(module_name, handle_memo)
