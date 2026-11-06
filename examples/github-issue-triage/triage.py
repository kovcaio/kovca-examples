#!/usr/bin/env python3
"""Forward a GitHub issues payload to a published Kovca triage workflow."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kovca_examples.github_triage import main

if __name__ == "__main__":
    raise SystemExit(main())
