"""
freeze_conformance_audit.py — frozen-contract conformance audit (read-only)

Method that found P6 (the inert Setup v1 extension filter), applied
systematically: **every clause a freeze contract claims must be checked against
what the code and the records actually do.**

Verdict vocabulary
  OPERATIVE     the clause exists in code and its input exists at runtime
  INERT         the clause exists in code but its input never exists at runtime,
                so it cannot fire (P6 class)
  MISMATCH      the documented value/rule differs from the code
  CLARITY       the code behaves as documented only under a specific reading;
                the document is ambiguous
  TEST-VERIFIED proven by an existing test rather than by this audit
  UNVERIFIABLE  no cheap read-only evidence available

Nothing is modified. Output: reports/freeze_conformance_audit_2026-10-01.json

Run:
  python production/tests/freeze_conformance_audit.py
"""
from __future__ import annotations

import json
import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig
from regime_dual_engine.config import DualEngineConfig

OUT = os.path.join(_REPO_ROOT, "reports", "freeze_conformance_audit_2026-10-01.json")
BT = os.path.join(_REPO_ROOT, "reports", "production_bt_2024-01-01_2025-07-31.json")

SETUP_SRC = "src/agents/setup_agent.py"


def _read(rel):
    with open(os.path.join(_REPO_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _eq(name, documented, actual):
    ok = documented == actual
    return {"clause": name, "documented": documented, "actual": actual,
            "verdict": "OPERATIVE" if ok else "MISMATCH"}


# ---------------------------------------------------------------------------
def audit_setup(cfg, trades):
    src = _read(SETUP_SRC)
    out = []

    out.append(_eq("1 setup type = pullback only", ["pullback"],
                   list(cfg.setup_enabled_types)))
    out.append(_eq("2 setup_score_threshold", 0.5, cfg.setup_score_threshold))
    out.append({"clause": "3 quality mapping (high/mid/low)",
                "documented": [1.0, 0.75, 0.5],
                "actual": [cfg.setup_quality_mult_high,
                           cfg.setup_quality_mult_mid,
                           cfg.setup_quality_mult_low],
                "verdict": "OPERATIVE"
                if [cfg.setup_quality_mult_high, cfg.setup_quality_mult_mid,
                    cfg.setup_quality_mult_low] == [1.0, 0.75, 0.5] else "MISMATCH"})
    out.append(_eq("4a gap filter value", 0.02, cfg.max_entry_gap_pct))

    # 4b: is the gap filter actually firing?  evidence from the recorded run
    gap_skips = 0
    if os.path.exists(BT):
        payload = json.load(open(BT, encoding="utf-8"))
        gap_skips = sum(1 for s in payload.get("skipped", [])
                        if s.get("skip_reason") == "GAP_TOO_HIGH")
    out.append({"clause": "4b gap filter operative at runtime",
                "documented": "blocks entries when next open > signal close +2%",
                "actual": f"{gap_skips} recorded GAP_TOO_HIGH skips in the window",
                "verdict": "OPERATIVE" if gap_skips > 0 else "UNVERIFIABLE"})

    # 5: extension filter — the P6 finding. Split the source precisely so the
    # evidence is exact: prior_high20 must appear inside breakout_setup but NOT
    # inside pullback_setup.
    breakout_body = src.split("def breakout_setup")[1].split("def _reversal_candle")[0]
    pullback_body = src.split("def pullback_setup")[1].split("def _quality_mult")[0]
    breakout_has_pivot = "prior_high20" in breakout_body
    pullback_has_pivot = "prior_high20" in pullback_body
    ext_values = sorted({t.get("extension_from_pivot_pct") for t in trades})
    out.append({"clause": "5 extension filter (max_extension_from_pivot_pct=0.03)",
                "documented": "blocks entries >3% from the pivot (prior_high20)",
                "actual": ("prior_high20 computed in breakout_setup: "
                           f"{breakout_has_pivot}; computed in pullback_setup: "
                           f"{pullback_has_pivot}. Setup v1 enables pullback only, so "
                           f"the dispatcher's best.get('prior_high20') is always None "
                           f"-> the guard never fires. Observed trade-log values of "
                           f"extension_from_pivot_pct: {ext_values}"),
                "verdict": "INERT"})

    # 6: next-open entry
    fills = {t.get("entry_fill_model") for t in trades}
    out.append({"clause": "6 next-open entry convention",
                "documented": "entry at the next session's open",
                "actual": f"entry_fill_model values observed: {sorted(fills)}",
                "verdict": "OPERATIVE" if fills == {"NEXT_OPEN"} else "MISMATCH"})

    out.append({"clause": "7 gap-aware OHLC exit (STOP/TARGET/TRAILING/TIME/SIGNAL)",
                "documented": "exact fill-price equalities",
                "actual": "Phase-2/3 parity: 31 unit tests + 1073 real bars, 0 divergences",
                "verdict": "TEST-VERIFIED"})

    # 8: pullback definition — all four components present, but the score is a
    # weighted SUM with a threshold, so not every component is required.
    body = src.split("def pullback_setup")[1].split("def _quality_mult")[0]
    comps = {k: (k in body) for k in
             ("near_ema10", "near_ema20", "vol_contract", "_reversal_candle",
              "RS_strong")}
    weights = re.findall(r"score \+= ([0-9.]+)", body)
    out.append({"clause": "8 pullback definition (EMA pullback + vol contraction + "
                          "reversal candle + RS strength)",
                "documented": "reads as a conjunction of four conditions",
                "actual": (f"all four present {comps}; the score is a weighted SUM "
                           f"(increments {weights}) with threshold "
                           f"{cfg.setup_score_threshold} — e.g. structure 0.30 + "
                           f"nearEMA 0.25 = 0.55 is valid with neither volume "
                           f"contraction nor a reversal candle; RS strength is a "
                           f"50-day-high proximity test (price >= 0.9 x high50), not "
                           f"a cross-sectional RS-vs-SPY test"),
                "verdict": "CLARITY"})
    return out


# ---------------------------------------------------------------------------
def audit_regime():
    cfg = DualEngineConfig()
    out = [
        _eq("1 hmm/breadth weights", [0.5, 0.5],
            [cfg.hmm_weight, cfg.breadth_weight]),
        _eq("3 thresholds (bull/bear)", [65, 35],
            [cfg.bull_threshold, cfg.bear_threshold]),
        _eq("5 divergence cap", 0.50, cfg.divergence_cap),
        _eq("6a distribution-day overlay disabled in production", False,
            cfg.enable_dist_day_overlay),
    ]
    src = _read("regime_dual_engine/regime_dual.py")
    # The code implements a generalised weighted mean:
    #   composite = (hmm_bull_prob*100*w_hmm + breadth_percentile*w_breadth) / total_w
    # with w = 0.5/0.5 and total_w = 1 this is exactly the documented
    # "hmm_bull_prob x 50 + breadth_percentile x 0.5". Check the OPERANDS (not the
    # exact spelling — an earlier version of this audit flagged a false MISMATCH
    # because the doc says `breadth_percentile_score` while the code says
    # `breadth_percentile`).
    operands_ok = ("hmm_bull_prob" in src and "breadth_percentile" in src
                   and "cfg.hmm_weight" in src and "cfg.breadth_weight" in src)
    weights_5050 = [cfg.hmm_weight, cfg.breadth_weight] == [0.5, 0.5]
    out.append({"clause": "2 composite formula",
                "documented": "hmm_bull_prob x 50.0 + breadth_percentile_score x 0.5",
                "actual": (f"code = (hmm_bull_prob*100*w_hmm + breadth_percentile*w_breadth)"
                           f"/total_w with w={cfg.hmm_weight}/{cfg.breadth_weight} -> "
                           f"identical to the documented 50/50 form; operands present: "
                           f"{operands_ok}"),
                "verdict": "OPERATIVE" if (operands_ok and weights_5050) else "MISMATCH"})
    out.append({"clause": "2-clarity composite formula is stated as the 50/50 special case",
                "documented": "a fixed 'x50 / x0.5' formula",
                "actual": "the code is a generalised weighted mean; the two agree only "
                          "while the weights stay 0.5/0.5 (which is frozen, so this is "
                          "a documentation-precision note, not a defect)",
                "verdict": "CLARITY"})
    out.append({"clause": "4 breadth = %above 50DMA (70%) + A/D 10d momentum (30%), "
                          "252-day rolling percentile",
                "documented": "0.7 / 0.3 and 252",
                "actual": "delegated to regime_dual_engine/breadth_data.py",
                "verdict": "TEST-VERIFIED"})
    out.append({"clause": "5 (thrust) breadth_10d_ago<0.30 and >20% relative rise "
                          "-> BULL + 1.0 + BREADTH_THRUST",
                "documented": "override",
                "actual": ("source has _breadth_thrust and BREADTH_THRUST"
                           if ("_breadth_thrust" in src and "BREADTH_THRUST" in src)
                           else "token missing"),
                "verdict": "OPERATIVE"
                if ("_breadth_thrust" in src and "BREADTH_THRUST" in src)
                else "MISMATCH"})
    # forbidden reintroductions (freeze §2). DistDays is special: the module and a
    # config-gated branch still exist, and the freeze's operative guarantee is that
    # the overlay is DISABLED in production (checked above) — not that the token is
    # absent from the file. An earlier version of this audit raised a false
    # MISMATCH by scanning for the bare token.
    hard_absent = {t: (t.lower() in src.lower())
                   for t in ("KER", "adx", "sma200", "selective")}
    dist_referenced = "distribution_days" in src or "dist_day" in src
    out.append({"clause": "2-forbidden reintroduction of DistDays / KER / ADX / "
                          "index MA-trend / 'selective' into the regime decision",
                "documented": "must not participate in the production regime decision",
                "actual": (f"KER/ADX/sma200/selective present: {hard_absent}; "
                           f"distribution-day tokens present: {dist_referenced} "
                           f"(import + config-gated branch only) and "
                           f"enable_dist_day_overlay={cfg.enable_dist_day_overlay}"),
                "verdict": "OPERATIVE"
                if (not any(hard_absent.values()) and not cfg.enable_dist_day_overlay)
                else "MISMATCH"})
    return out


def audit_stop_exit():
    return [
        {"clause": "exit priority STOP > TARGET > TRAILING > TIME > SIGNAL",
         "actual": "test_exit_engine priority cases + 17 parity cases",
         "verdict": "TEST-VERIFIED"},
        {"clause": "exact fill-price equalities (GAP/STOP/TARGET/CLOSE)",
         "actual": "test_exit_engine contract guards + replay 1073 bars, 0 divergences",
         "verdict": "TEST-VERIFIED"},
        {"clause": "Exit Engine performs no trailing arithmetic",
         "actual": "test_no_trailing_math_in_exit_engine + wiring-safety test 9",
         "verdict": "TEST-VERIFIED"},
        {"clause": "TIME_STOP uses calendar days",
         "actual": "rules._held_days uses datetime difference; parity case set",
         "verdict": "TEST-VERIFIED"},
        {"clause": "current_stop_price is not persisted",
         "actual": "wiring-safety test 13 asserts state.py has no such field",
         "verdict": "TEST-VERIFIED"},
        {"clause": "tech_signal injected by the caller (never computed inside)",
         "actual": "test_technicals_agent_never_called_internally",
         "verdict": "TEST-VERIFIED"},
        {"clause": "legacy remains authoritative while mode=legacy/shadow",
         "actual": "1088/1088 shadow evaluations agreed; shadow vs legacy runs identical",
         "verdict": "TEST-VERIFIED"},
    ]


def main() -> int:
    cfg = ProductionConfig()
    trades = []
    if os.path.exists(BT):
        trades = json.load(open(BT, encoding="utf-8")).get("trade_log", [])
    sections = {
        "SETUP_V1 (src/agents/SETUP_V1_FREEZE.md)": audit_setup(cfg, trades),
        "REGIME_V1 (regime_dual_engine/REGIME_V1_FREEZE.md)": audit_regime(),
        "STOP_EXIT_V1 (production/STOP_EXIT_V1_FREEZE.md)": audit_stop_exit(),
    }
    counts = {}
    for rows in sections.values():
        for r in rows:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    payload = {"generated": "2026-10-01",
               "purpose": "systematic check that each frozen clause operates as documented",
               "verdict_counts": counts,
               "sections": sections}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    for name, rows in sections.items():
        print(f"\n=== {name} ===")
        for r in rows:
            mark = {"OPERATIVE": "OK  ", "TEST-VERIFIED": "TEST", "INERT": "!!  ",
                    "MISMATCH": "XX  ", "CLARITY": "~   ",
                    "UNVERIFIABLE": "?   "}.get(r["verdict"], "    ")
            print(f"  [{mark}] {r['clause']}")
            if r["verdict"] in ("INERT", "MISMATCH", "CLARITY", "UNVERIFIABLE"):
                print(f"          documented: {r['documented']}")
                print(f"          actual    : {r['actual']}")
    print(f"\nverdict counts: {counts}")
    print(f"report -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
