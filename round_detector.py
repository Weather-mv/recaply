"""Compatibility alias: the module moved to recaply/profiles/valorant/round_detector.py
(Valorant is now one profile among many). `import round_detector` keeps working."""

import sys

from recaply.profiles.valorant import round_detector as _real

sys.modules[__name__] = _real
