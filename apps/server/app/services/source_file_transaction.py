"""Remove newly written source originals when their owning transaction rolls back."""

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
_KEY = "uncommitted_source_originals"


def track(session, root, relative):
    from app.services.builtin_style_media_service import protected_path
    if protected_path(relative):
        return
    session.info.setdefault(_KEY, []).append((root.resolve(), relative))


@event.listens_for(Session, "after_commit")
def committed(session):
    if not session.in_nested_transaction():
        session.info.pop(_KEY, None)


@event.listens_for(Session, "after_transaction_end")
def ended(session, transaction):
    if transaction.parent is not None:
        return
    for root, relative in session.info.pop(_KEY, []):
        from app.services.builtin_style_media_service import protected_path
        if protected_path(relative):
            continue
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            continue
        try:
            target.unlink(missing_ok=True)
        except OSError:
            logger.warning("Uncommitted source original cleanup deferred")
