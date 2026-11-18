"""Test setup: run against a throwaway data/cache folder and the repo's modules."""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmp = tempfile.mkdtemp(prefix="recaply_tests_")
os.environ.setdefault("RECAPLY_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("RECAPLY_CACHE_DIR", os.path.join(_tmp, "cache"))
os.environ["RECAPLY_LLM"] = "none"            # tests never call paid APIs

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
