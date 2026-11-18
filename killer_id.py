"""Compatibility alias: the module moved to recaply/profiles/valorant/killer_id.py
(Valorant is now one profile among many). `import killer_id` keeps working."""

import sys

from recaply.profiles.valorant import killer_id as _real

sys.modules[__name__] = _real
