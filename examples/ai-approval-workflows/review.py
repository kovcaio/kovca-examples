#!/usr/bin/env python3
"""Submit a case to a published Kovca approval workflow, or prepare a decision."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kovca_examples.approvals import main

if __name__ == "__main__":
    raise SystemExit(main())
