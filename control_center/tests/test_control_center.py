"""test_control_center.py — headless smoke test for every Control Center page.

Uses Streamlit's official AppTest to execute the app exactly as the browser
would, asserting that no page raises an exception.  Run:

    python control_center/tests/test_control_center.py
    # or:  pytest control_center/tests/test_control_center.py -q
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "control_center.py")

from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = ["Dashboard", "Module Registry", "Dependency Explorer", "Production Architecture",
         "Research Architecture", "Duplicate Audit", "Migration Board", "Config Explorer",
         "Copy Divergence", "Decisions"]


def run_all() -> int:
    failures = 0
    for page in PAGES:
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        if at.exception:
            print(f"  [FAIL] initial run: {at.exception}")
            failures += 1
            continue
        # navigate
        at.sidebar.radio[0].set_value(page).run()
        if at.exception:
            print(f"  [FAIL] {page}: {at.exception}")
            failures += 1
        else:
            n_df = len(at.dataframe)
            print(f"  [ OK ] {page}  (dataframes rendered: {n_df})")
    print(f"\n{len(PAGES) - failures}/{len(PAGES)} pages OK")
    return failures


if __name__ == "__main__":
    print("=== Control Center smoke test (AppTest, headless) ===")
    sys.exit(1 if run_all() else 0)
