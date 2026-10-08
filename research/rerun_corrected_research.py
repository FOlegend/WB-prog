"""
research/rerun_corrected_research.py — re-derive Phase-4/5 research on the PIT-corrected regime.

After the point-in-time fix (regime_dual_engine/breadth_data.slice_to_end), every
historical result that consumed the leaky breadth engine must be re-derived. This
runner calls the EXISTING research scripts unchanged and only redirects their
output paths, so the old (leaky) artifacts remain on disk as audit evidence and
the corrected ones land beside them.

Nothing else is modified: no parameter, no strategy rule, no production path.

  D1-D6  production/tests/phase4_diagnostics.py
  V1-V4  production/tests/phase4_verify.py
  Step 0 production/tests/phase5_step0_marginal_cohort.py

Outputs (all with the `pitcorrected_` marker):
  reports/phase4_diag_data_pitcorrected_2026-10-01.json
  reports/phase4_verify_pitcorrected_2026-10-01.json
  reports/phase5_step0_marginal_cohort_pitcorrected_2026-10-01.json

Run:
  python research/rerun_corrected_research.py            # all three
  python research/rerun_corrected_research.py --only d1  # one step
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.tests import phase4_diagnostics as D1
from production.tests import phase4_verify as V
from production.tests import phase5_step0_marginal_cohort as S0
from production.tests import phase5_step0b_lever_cohorts as SB

_STEP0_CORRECTED = "reports/phase5_step0_marginal_cohort_pitcorrected_2026-10-01.json"

STEPS = {
    "d1": {"mod": D1,
           "out": "reports/phase4_diag_data_pitcorrected_2026-10-01.json",
           "label": "D1-D6"},
    "verify": {"mod": V,
               "out": "reports/phase4_verify_pitcorrected_2026-10-01.json",
               "label": "V1-V4"},
    "step0": {"mod": S0, "out": _STEP0_CORRECTED, "label": "Step 0"},
    # Step 0b consumes Step 0's output -> point its input at the corrected file
    "step0b": {"mod": SB,
               "out": "reports/phase5_step0b_lever_cohorts_pitcorrected_2026-10-01.json",
               "label": "Step 0b lever cohorts",
               "inputs": {"STEP0": _STEP0_CORRECTED}},
}
ORDER = ["d1", "verify", "step0", "step0b"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", choices=[""] + ORDER)
    args = ap.parse_args()
    todo = [args.only] if args.only else ORDER

    for key in todo:
        step = STEPS[key]
        mod, rel, label = step["mod"], step["out"], step["label"]
        mod.OUT = os.path.join(_REPO_ROOT, rel)
        for attr, in_rel in (step.get("inputs") or {}).items():
            setattr(mod, attr, os.path.join(_REPO_ROOT, in_rel))
        print(f"\n########## {label} (PIT-corrected) -> {rel}", flush=True)
        try:
            rc = mod.main()
        except Exception as exc:                       # noqa: BLE001
            print(f"########## {label} EXCEPTION {type(exc).__name__}: {exc}",
                  flush=True)
            rc = 1
        print(f"########## {label} rc={rc}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
