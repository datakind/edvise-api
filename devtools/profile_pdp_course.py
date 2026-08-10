#!/usr/bin/env python3
"""Local harness to time/profile PDP course validation on a CSV.

Usage (from edvise-api root, venv active):

  PDP_COURSE_CSV=/path/to/course.csv python devtools/profile_pdp_course.py

Do not commit institution CSVs.
"""

import os
import sys
import time

from src.webapp.validation import _read_pdp_course_edvise

path = os.environ.get("PDP_COURSE_CSV") or (sys.argv[1] if len(sys.argv) > 1 else None)
if not path:
    raise SystemExit(
        "Usage: PDP_COURSE_CSV=/path/to.csv python devtools/profile_pdp_course.py"
    )

t0 = time.perf_counter()
df = _read_pdp_course_edvise(path)
print(f"rows={len(df)} cols={len(df.columns)} elapsed_s={time.perf_counter() - t0:.1f}")
