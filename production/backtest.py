"""
backtest.py — Production-equivalent backtest (replays run_daily)

Replays the canonical pipeline (production.pipeline.run_daily) day-by-day over
historical dates with ONLY information available at each as-of date.

  What would the CURRENT production system have decided each day,
  using only data available at that time?

Guarantees
----------
* Same decision pipeline as live: Data → Regime v1 → Screener → Setup v1 →
  Risk → Entry/Exit → Portfolio → DecisionRecord. There is NO second,
  independent backtest implementation — the engine is a thin executor on top
  of the DecisionRecords produced by run_daily().
* Point-in-time: every OHLCV read is sliced to `datetime <= as_of` by the
  CachedSource. No future bar is ever visible.
* Execution conventions (identical to the validated assumptions):
    - signals generated on day D's close;
    - SELL proposals fill on D at the gap-aware exit price from the D bar
      (STOP/TARGET/GAP use intraday prices, CLOSE at close);
    - BUY proposals queue and execute at D+1 OPEN with re-validation:
      still in bucket, gap <= cfg.max_entry_gap_pct vs signal close,
      extension <= cfg.max_extension_from_pivot_pct vs prior_high20,
      position re-sized at the actual open price.
* Transaction costs: buy slippage cfg.slippage_pct at entry; the validated
  sell cost model (slippage + SEC + FINRA) on exit (src.state.close_position).

Screening frequency
-------------------
The live system re-screens the full universe every day. Re-screening ~600
tickers per day × 1,900 days is compute-heavy, so the replay screens once per
rebalance period (monthly, last trading day) using the SAME screen function
(screen_from_source, point-in-time). The resulting bucket is the screener's
decision for that month and is fed into run_daily every day. This mirrors the
legacy dynamic backtest calendar while keeping run_daily the sole decision
maker for setups/entries/exits/portfolio.

Run:
  python production/backtest.py --start 2024-01-01 --end 2025-07-31
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig
from production.datasource import build_cached_source, CachedSource
from production.observability import parse_setup_components
from production.pipeline import run_daily
from production.screener.screener import screen_from_source
from src.state.state import default_state, open_position, close_position, \
    mark_to_market, default_position

BREADTH_LAST = "2025-07-31"   # PIT breadth cache tail (see regime_dual_engine/data)


def _entry_research_context(*, o, sizing, open_px, gap, ext, equity,
                            cash_after, cfg) -> dict:
    """R7 §3 — the entry-phase research context for one filled BUY.

    Every field is either an EXACT value the execution path already computed, or
    an explicit "unavailable" marker. Nothing is estimated, back-filled or
    proxied, and nothing here is read by any decision rule — the dictionary is
    merged onto the trade record only after the position has been opened (and
    again only after it has been closed).

    Provenance
    -----------
    signal_close          exact  — setup_signal's last close on the signal date
    next_open_gap_pct     exact  — (next open − signal close) / signal close
    entry_atr / atr_pct   exact  — atr_at_entry carried on the position
    risk_budget_usd       exact  — equity × risk_per_trade × eff multiplier
    stop_distance_usd     exact  — atr × stop_atr_mult (the frozen R unit)
    entry_phase_*         exact  — the sizing inputs, captured at the fill
    extension_*           exact  — but `prior_high20` is None under Setup v1
                                    Pullback-Only, so the frozen extension
                                    filter can never fire; the null is
                                    reported, not replaced by a look-alike
    score_components      exact  — parsed from the engine's own entry_reason
    """
    atr = o.get("atr") or 0.0
    quality = o.get("quality_mult", 1.0) or 0.0
    size_mult = o.get("size_mult", 1.0) or 0.0
    eff = size_mult * quality
    risk_budget = equity * cfg.risk_per_trade * eff
    stop_distance = atr * cfg.stop_atr_mult
    return {
        "signal_close": o.get("signal_close"),
        "entry_open": round(open_px, 4),
        "next_open_gap_pct": round(gap, 6) if gap is not None else None,
        "max_entry_gap_pct": cfg.max_entry_gap_pct,
        "entry_atr": atr,
        "entry_atr_pct_of_price": (round(100.0 * atr / open_px, 4)
                                   if open_px else None),
        "prior_high20": o.get("prior_high20"),
        "extension_from_pivot_pct": (round(ext, 6) if ext is not None else None),
        "extension_filter_status": (
            "evaluated" if ext is not None else
            "unavailable — prior_high20 is None (Setup v1 is Pullback-Only), so the "
            "frozen max_extension_from_pivot_pct guard is INERT and did not evaluate "
            "this entry (P6); no substitute value is supplied"),
        "entry_phase_equity": round(equity, 2),
        "entry_phase_cash_after_fill": round(cash_after, 2),
        "entry_regime_size_mult": size_mult,
        "entry_setup_quality_mult": quality,
        "entry_eff_size_mult": round(eff, 6),
        "risk_budget_usd": round(risk_budget, 2),
        "stop_distance_usd": round(stop_distance, 4),
        "r_unit_usd": round(stop_distance, 4),
        "shares_requested_before_flooring": (
            int(risk_budget // stop_distance) if stop_distance > 0 else 0),
        "shares_final": sizing.get("shares"),
        "position_value": sizing.get("position_value"),
        "score_components": parse_setup_components(o.get("reason"),
                                                   o.get("setup_type")),
    }


class ProductionBacktest:
    """Replay engine: executes DecisionRecords from run_daily day by day."""

    def __init__(self, cfg: ProductionConfig, source: CachedSource,
                 start: str, end: str, verbose: bool = False):
        self.cfg = cfg
        self.source = source
        self.start = start
        if pd.Timestamp(end) > pd.Timestamp(BREADTH_LAST):
            print(f"  ⚠️ breadth cache ends {BREADTH_LAST}; "
                  f"clamping end {end} -> {BREADTH_LAST}")
            end = BREADTH_LAST
        self.end = end
        self.verbose = verbose

    # ------------------------------------------------------------------
    # calendar + monthly bucket (screener decision for each period)
    # ------------------------------------------------------------------
    def _trading_dates(self) -> list[pd.Timestamp]:
        spy, _ = self.source.get_ohlcv(self.source.benchmark,
                                       as_of=self.end, min_bars=1)
        dates = [d for d in spy["datetime"].tolist()
                 if pd.Timestamp(self.start) <= d <= pd.Timestamp(self.end)]
        return sorted(dates)

    @staticmethod
    def _monthly_rebalance_dates(dates: list) -> list:
        """Last trading day of each calendar month present in `dates`."""
        df = pd.DataFrame({"d": dates})
        df["ym"] = df["d"].dt.to_period("M")
        return df.groupby("ym")["d"].last().sort_values().tolist()

    def _screens(self, rb_dates: list, cache_file: str) -> dict:
        """Screen each rebalance date once (PIT). {rb_str: screen_result}.

        A JSON cache (deterministic PIT -> safe to reuse) is read when it
        already covers ALL required rebalance dates; otherwise the missing
        dates are screened and the cache is extended.
        """
        rb_set = {d.strftime("%Y-%m-%d") for d in rb_dates}
        screens: dict[str, dict] = {}
        if cache_file and os.path.exists(cache_file):
            try:
                with open(cache_file) as f:
                    cached = json.load(f)
                if set(cached.keys()) >= rb_set:
                    print(f"  reuse bucket cache: {cache_file}")
                    return {k: cached[k] for k in sorted(rb_set)}
                screens = cached
                print(f"  bucket cache partial ({len(cached)}/{len(rb_set)}); "
                      f"screening missing dates")
            except Exception:
                screens = {}
        for i, rd in enumerate(sorted(rb_dates)):
            rd_s = rd.strftime("%Y-%m-%d")
            if rd_s in screens:
                continue
            res = screen_from_source(self.source, self.cfg, as_of=rd_s,
                                     apply_mcap=False)
            screens[rd_s] = {"status": res["status"], "error": res["error"],
                             "tickers": res["tickers"],
                             "n_universe_checked": res["n_universe_checked"],
                             "warnings": res["warnings"]}
            if self.verbose:
                print(f"  screen {i+1}/{len(rb_set)} {rd_s}: "
                      f"{res['status']} n={len(res['tickers'])}")
        if cache_file:
            try:
                os.makedirs(os.path.dirname(cache_file), exist_ok=True)
                with open(cache_file, "w") as f:
                    json.dump(screens, f)
            except Exception:
                pass
        return {k: screens[k] for k in sorted(rb_set)}

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def run(self, bucket_cache_file: str | None = None) -> dict:
        cfg = self.cfg
        dates = self._trading_dates()
        if len(dates) < 60:
            return {"error": "insufficient trading dates",
                    "start": self.start, "end": self.end}
        rb_dates = self._monthly_rebalance_dates(dates)
        screens = self._screens(rb_dates, bucket_cache_file or "")
        rb_str = [d.strftime("%Y-%m-%d") for d in rb_dates]

        state = default_state(cfg.capital_usd)
        pending: dict[str, dict] = {}      # BUY proposals queued for next open
        entry_ctx: dict[str, dict] = {}    # R7: per-entry research context
        equity_curve: list[dict] = []
        skipped: list[dict] = []
        regime_log: list[dict] = []
        shadow_log: list[dict] = []        # Phase 3: per-session shadow coverage
        day_records: list[str] = []        # ledger file names written (if any)
        decisions: list[dict] = []         # DecisionRecords (for tests/audit)

        def active_bucket(d: pd.Timestamp) -> dict:
            i = bisect.bisect_right(rb_dates, d) - 1
            if i < 0:
                return {"status": "EMPTY", "tickers": []}
            return screens.get(rb_str[i], {"status": "EMPTY", "tickers": []})

        for di, date in enumerate(dates):
            date_s = date.strftime("%Y-%m-%d")
            bucket = active_bucket(date)
            bucket_tickers = [c["ticker"] for c in bucket.get("tickers", [])]

            # ---- 0) execute yesterday's BUY proposals at TODAY's OPEN ----
            self._execute_pending(state, pending, bucket_tickers, date, skipped,
                                  entry_ctx)

            # ---- 1) canonical decision for today ----
            rec = run_daily(date_s, state, self.source, cfg,
                            screen_mode="bucket",
                            candidate_tickers=bucket_tickers,
                            screen_result=bucket if bucket.get("status") else None)
            decisions.append(rec)

            # ---- 2) execute proposed SELLs (fill today, gap-aware price) ----
            for s in rec["exits"]["proposed"]:
                idx = next((i for i, p in enumerate(state["open_positions"])
                            if p["ticker"] == s["ticker"]), None)
                if idx is None:
                    continue
                trade = close_position(state, idx, float(s["exit_price"]), date_s,
                                       s["reason"], {}, fill_model=s["fill_model"])
                # R7 §3 (P1) — holding_days was declared in the state contract
                # but never filled by any caller, so it is null on every trade.
                # Calendar days, matching the frozen TIME_STOP convention.
                # Additive on the trade record; no decision path is touched.
                trade["holding_days"] = (
                    datetime.strptime(date_s, "%Y-%m-%d")
                    - datetime.strptime(trade["entry_date"], "%Y-%m-%d")).days
                # R7 §3 — attach the entry-phase research context that the
                # position carried. Read-only merge onto the finished trade
                # record; the position is already closed and cannot be affected.
                _ctx = entry_ctx.pop(s["ticker"], None)
                if _ctx:
                    for _k, _v in _ctx.items():
                        trade.setdefault(_k, _v)
            # positions closed today are removed from pending (stale)
            sold = {s["ticker"] for s in rec["exits"]["proposed"]}
            for t in sold:
                pending.pop(t, None)

            # ---- 3) queue today's BUY proposals for TOMORROW's open ----
            for o in rec["entries"]["proposed"]:
                pending[o["ticker"]] = {**o, "signal_date": date_s,
                                        "bucket_date": date_s}

            # ---- 4) mark to market + equity curve ----
            prices = {}
            held = [p["ticker"] for p in state["open_positions"]]
            for t in held:
                df, _ = self.source.get_ohlcv(t, as_of=date_s, min_bars=1)
                if df is not None and len(df):
                    prices[t] = float(df["close"].iloc[-1])
            mark_to_market(state, prices)
            equity_curve.append({"date": date_s,
                                 "equity": round(state["equity"], 2),
                                 "cash": round(state["cash"], 2),
                                 "n_positions": len(state["open_positions"])})
            if rec["regime"]["output"]:
                regime_log.append({"date": date_s,
                                   **{k: rec["regime"]["output"][k]
                                      for k in ("regime_label",
                                                "composite_score",
                                                "position_size_mult",
                                                "strategy_mode")},
                                   # R7 §3 (P4) — the veto flags explain WHY the
                                   # multiplier moved on a given day. Purely
                                   # additive to the log; never read back.
                                   "veto_flags": rec["regime"]["output"].get(
                                       "veto_flags", [])})
            # ---- Phase 3: shadow coverage (observation only, no side effect) ----
            shadow_summary = rec["exits"].get("shadow_summary")
            if shadow_summary:
                shadow_log.append({"date": date_s, **shadow_summary,
                                   "n_held": len(state["open_positions"])})

        state["last_run_date"] = dates[-1].strftime("%Y-%m-%d")
        summary = self._summary(equity_curve, state, skipped, decisions,
                                regime_log)
        screens_out = {rd_s: {"status": s.get("status"),
                              "tickers": [c["ticker"]
                                          for c in s.get("tickers", [])]}
                       for rd_s, s in screens.items()}
        return {"summary": summary, "equity_curve": equity_curve,
                "trade_log": state["trade_log"], "skipped": skipped,
                "regime_log": regime_log, "screens": screens_out,
                "shadow_log": shadow_log,
                "n_decisions": len(decisions),
                "start": self.start, "end": self.end}

    # ------------------------------------------------------------------
    def _execute_pending(self, state: dict, pending: dict, bucket: list[str],
                         date: pd.Timestamp, skipped: list,
                         entry_ctx: dict | None = None) -> None:
        cfg = self.cfg
        date_s = date.strftime("%Y-%m-%d")
        if entry_ctx is None:
            entry_ctx = {}
        held = {p["ticker"] for p in state["open_positions"]}
        bucket_set = set(bucket)
        for t in sorted(pending.keys()):
            o = pending[t]
            if t not in bucket_set or t in held:
                del pending[t]
                continue
            df, _ = self.source.get_ohlcv(t, as_of=date_s, min_bars=1)
            if df is None or len(df) == 0:
                skipped.append({"ticker": t, "entry_date": date_s,
                                "skip_reason": "NO_OPEN_PRICE"})
                del pending[t]
                continue
            bar = df.iloc[-1]
            open_px = float(bar["open"])
            if open_px <= 0:
                skipped.append({"ticker": t, "entry_date": date_s,
                                "skip_reason": "NO_OPEN_PRICE"})
                del pending[t]
                continue

            # next-open guardrails (frozen Setup v1 conventions)
            sig_close = o.get("signal_close")
            prior_high20 = o.get("prior_high20")
            gap = ((open_px - sig_close) / sig_close) if sig_close else None
            ext = ((open_px - prior_high20) / prior_high20) if prior_high20 else None
            if gap is not None and gap > cfg.max_entry_gap_pct:
                skipped.append({"ticker": t, "signal_date": o.get("signal_date"),
                                "entry_date": date_s, "skip_reason": "GAP_TOO_HIGH",
                                "next_open_gap_pct": round(gap, 4)})
                del pending[t]
                continue
            if ext is not None and ext > cfg.max_extension_from_pivot_pct:
                skipped.append({"ticker": t, "signal_date": o.get("signal_date"),
                                "entry_date": date_s,
                                "skip_reason": "EXTENDED_FROM_PIVOT",
                                "extension_pct": round(ext, 4)})
                del pending[t]
                continue

            # re-size at actual open price (same risk model as live)
            eff = o.get("size_mult", 1.0) * o.get("quality_mult", 1.0)
            from src.agents.risk_manager import size_position
            sizing = size_position(state["equity"], state["cash"], open_px,
                                   o.get("atr") or 0.0, eff,
                                   len(state["open_positions"]), cfg)
            if not sizing.get("allow"):
                skipped.append({"ticker": t, "signal_date": o.get("signal_date"),
                                "entry_date": date_s, "skip_reason": "SIZING_DENIED",
                                "detail": sizing.get("reasoning")})
                del pending[t]
                continue

            pos = default_position(
                t, sizing["shares"], open_px, date_s,
                o.get("atr") or 0.0, sizing["stop_price"],
                sizing["take_profit"], o.get("regime_label", "UNKNOWN"),
                o.get("reason", ""))
            pos.update({
                "entry_fill_model": "NEXT_OPEN",
                "signal_date": o.get("signal_date"),
                "bucket_date": o.get("bucket_date"),
                "setup_type": o.get("setup_type"),
                "setup_score": o.get("setup_score"),
                "entry_regime_score": o.get("composite_score"),
                "entry_size_mult": o.get("size_mult"),
                "next_open_gap_pct": round(gap, 4) if gap is not None else None,
                "extension_from_pivot_pct": round(ext, 4) if ext is not None else None,
            })
            open_position(state, pos, open_px, cfg)
            # R7 §3 — entry-phase context, merged onto the trade record when the
            # position closes. Written AFTER open_position so it cannot be
            # mistaken for execution input, and never read by any decision rule.
            entry_ctx[t] = _entry_research_context(
                o=o, sizing=sizing, open_px=open_px, gap=gap, ext=ext,
                equity=state["equity"], cash_after=state["cash"], cfg=cfg)
            del pending[t]

    # ------------------------------------------------------------------
    def _summary(self, curve: list, state: dict, skipped: list,
                 decisions: list, regime_log: list) -> dict:
        if not curve:
            return {}
        eq = pd.Series([c["equity"] for c in curve],
                       index=[c["date"] for c in curve])
        start_v = eq.iloc[0]
        end_v = eq.iloc[-1]
        ret = end_v / start_v - 1.0
        days = len(eq)
        years = max(days / 252.0, 1e-9)
        cagr = (end_v / start_v) ** (1.0 / years) - 1.0 if start_v > 0 else 0.0
        daily_ret = eq.pct_change().dropna()
        sharpe = (daily_ret.mean() / daily_ret.std() * math.sqrt(252)
                  if len(daily_ret) > 1 and daily_ret.std() > 0 else 0.0)
        peak = eq.cummax()
        dd = (eq / peak - 1.0)
        max_dd = float(dd.min())

        trades = state["trade_log"]
        wins = [t for t in trades if t.get("net_pnl", 0) > 0]
        losses = [t for t in trades if t.get("net_pnl", 0) <= 0]
        gross_w = sum(t.get("net_pnl", 0) for t in wins)
        gross_l = abs(sum(t.get("net_pnl", 0) for t in losses))
        pf = (gross_w / gross_l) if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0)
        exposure_days = sum(1 for c in curve if c["n_positions"] > 0)

        reg = pd.DataFrame(regime_log) if regime_log else None
        return {
            "start": curve[0]["date"], "end": curve[-1]["date"],
            "n_days": days,
            "start_equity": round(float(start_v), 2),
            "end_equity": round(float(end_v), 2),
            "return_pct": round(ret * 100.0, 2),
            "cagr_pct": round(cagr * 100.0, 2),
            "sharpe": round(float(sharpe), 3),
            "max_dd_pct": round(max_dd * 100.0, 2),
            "n_trades": len(trades),
            "win_rate_pct": round(len(wins) / len(trades) * 100.0, 1) if trades else 0.0,
            "profit_factor": round(float(pf), 2) if pf != float("inf") else None,
            "avg_net_pnl": round(float(np.mean([t.get("net_pnl", 0)
                                                for t in trades])), 2) if trades else 0.0,
            "exposure_days_pct": round(exposure_days / days * 100.0, 1),
            "n_skipped_entries": len(skipped),
            "n_failed_screens": sum(1 for d in decisions
                                    if d["screener"]["status"] == "FAILURE"),
            "regime_label_counts": (reg["regime_label"].value_counts().to_dict()
                                    if reg is not None else {}),
            "cash_end": round(state.get("cash", 0.0), 2),
            "open_positions_end": len(state["open_positions"]),
        }


# ---------------------------------------------------------------------------
def _fmt_report(summary: dict) -> str:
    if not summary:
        return "(empty run)"
    rows = [f"  {'Return':<22}{summary['return_pct']:>9.2f} %",
            f"  {'CAGR':<22}{summary['cagr_pct']:>9.2f} %",
            f"  {'Sharpe':<22}{summary['sharpe']:>9.3f}",
            f"  {'MaxDD':<22}{summary['max_dd_pct']:>9.2f} %",
            f"  {'Trades':<22}{summary['n_trades']:>9}",
            f"  {'Win rate':<22}{summary['win_rate_pct']:>9.1f} %",
            f"  {'Profit factor':<22}{str(summary['profit_factor']):>9}",
            f"  {'Avg net PnL':<22}{summary['avg_net_pnl']:>9.2f}",
            f"  {'Exposure days':<22}{summary['exposure_days_pct']:>9.1f} %",
            f"  {'Skipped entries':<22}{summary['n_skipped_entries']:>9}",
            f"  {'Failed screens':<22}{summary['n_failed_screens']:>9}",
            f"  {'Equity end':<22}{summary['end_equity']:>9.2f}"]
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser(description="Production-equivalent backtest")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-07-31")
    ap.add_argument("--out", default="",
                    help="output JSON path (default reports/production_bt_<s>_<e>.json)")
    ap.add_argument("--no-bucket-cache", action="store_true")
    args = ap.parse_args()

    cfg = ProductionConfig()
    source = build_cached_source(cfg)
    bt = ProductionBacktest(cfg, source, args.start, args.end, verbose=True)
    cache_file = None if args.no_bucket_cache else os.path.join(
        cfg.reports_dir,
        f"production_buckets_{args.start}_{args.end}_top{cfg.screener_top_n}.json")
    print(f"=== Production-equivalent backtest {args.start} .. {args.end} ===")
    res = bt.run(bucket_cache_file=cache_file)
    if "error" in res:
        print("ERROR:", res["error"])
        return
    print("\n=== Summary ===")
    print(_fmt_report(res["summary"]))
    out = args.out or os.path.join(
        cfg.reports_dir, f"production_bt_{args.start}_{args.end}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(res, f, indent=2, default=str)
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
