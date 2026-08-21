"""Version profile registry.

Importing this package registers every known release. Adding a new one means
dropping a module in here and importing it below.
"""

from __future__ import annotations

from . import vb17a, ve11c, xa30, xa60  # noqa: F401  (imported for the registration side effect)
from .base import REGISTRY, LayoutConfig, ProfileRegistry, VersionProfile

__all__ = [
    "REGISTRY",
    "LayoutConfig",
    "ProfileRegistry",
    "VersionProfile",
    "vb17a",
    "ve11c",
    "xa30",
    "xa60",
]
