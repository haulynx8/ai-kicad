"""Provider interface for read-only engineering review."""

from __future__ import annotations

from typing import Any, Protocol

from ai_kicad.review.schema import ReviewReport


class ReviewProvider(Protocol):
    def review(self, review_input: dict[str, Any]) -> ReviewReport:
        """Return findings grounded in the supplied verified context."""


class ReviewProviderError(RuntimeError):
    """Raised when an AI provider cannot return a valid review."""
