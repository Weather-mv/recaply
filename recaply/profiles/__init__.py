"""Content profiles (see base.py). Importing this package registers the built-ins."""

from recaply.profiles import builtin  # noqa: F401  (registers the built-in profiles)
from recaply.profiles.base import Detection, Profile, all_profiles, get_profile, register
from recaply.profiles.detect import detect_profile

__all__ = ["Profile", "Detection", "register", "get_profile", "all_profiles", "detect_profile"]
