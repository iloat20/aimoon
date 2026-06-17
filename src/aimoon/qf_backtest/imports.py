"""Check QF-Lib availability and provide compatibility helpers."""

from __future__ import annotations

import logging

# Must patch matplotlib BEFORE qf-lib imports its analysis modules
try:
    import matplotlib.cm

    if not hasattr(matplotlib.cm, "get_cmap"):

        def _get_cmap(name, lut=None):
            return matplotlib.colormaps[name]

        matplotlib.cm.get_cmap = _get_cmap
except ImportError:
    pass

logger = logging.getLogger(__name__)

try:
    import qf_lib  # noqa: F401

    QF_AVAILABLE = True
except ImportError:
    QF_AVAILABLE = False
    logger.debug("qf-lib not installed -- QF-Lib backtesting disabled")
