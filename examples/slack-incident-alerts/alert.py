#!/usr/bin/env python3
"""Forward a Slack message to a published Kovca incident workflow."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kovca_examples.slack_incidents import main

if __name__ == "__main__":
    raise SystemExit(main())
