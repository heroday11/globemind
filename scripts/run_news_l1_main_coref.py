#!/usr/bin/env python3
"""Run the saved main L1 pipeline.

This is a thin entrypoint for the production L1 v2 story-clustering pipeline.
It writes run_id ``fast_l1_v2`` unless the caller explicitly passes a different
``--run-id``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_news_l1_fast_coref_experimental import main

PIPELINE_VERSION = "fast_l1_v2"


if __name__ == "__main__":
    if "--run-id" not in sys.argv:
        sys.argv.extend(["--run-id", PIPELINE_VERSION])
    main()
