from __future__ import annotations

from .errors import ActionNotAllowed


class NotificationActionPolicy:
    """Deterministic side-effect boundary for Slice 0."""

    ALLOWED_ACTION = "draft"

    def require_allowed(self, requested_action: str) -> None:
        if requested_action != self.ALLOWED_ACTION:
            raise ActionNotAllowed(requested_action)
