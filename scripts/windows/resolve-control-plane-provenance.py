from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from ordax_dev_agent.control_plane_provenance import main


if __name__ == "__main__":
    raise SystemExit(main())
