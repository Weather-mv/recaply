import os
import sys

# run from the repo root: the shared modules (config, common, pipeline) live there
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recaply.cli import main  # noqa: E402

raise SystemExit(main())
