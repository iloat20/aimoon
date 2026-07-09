"""Re-export from common/timing.py for backward compatibility.

``logphase`` was moved to ``common/timing.py`` (audit P2.3) so that
``collectors/`` no longer crosses into ``ai/`` for this utility.
Existing ``from .timing import logphase`` in ``orchestrator.py`` keeps working.
"""

from __future__ import annotations

from aimoon.adapters.driven.common.timing import logphase

__all__ = ["logphase"]
