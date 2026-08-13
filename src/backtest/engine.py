"""
engine.py — swing bot 回測引擎（v3: market regime decoupled）

逐日 replay：每個交易日，用「截至當日」的資料跑 regime + technicals + risk + PM，
模擬收盤成交（含費用），追蹤權益曲線與交易紀錄。

v3 結構解耦：
  - Regime score 只在 SPY（市場指數）上計算一次 → 全局 position_size_mult
  - 個股只負責 technicals 進出場訊號（Momentum/Breakout）
  - HMM 從每支股票各跑一次 → 全回測只跑 SPY（速度大幅提升）

重點：
  - 無 lookahead：agents 只看 ≤ 當日的資料
  - HMM 每 regime_refit_days 天重 fit 一次（只在 SPY 上）
  - 模擬收盤成交，含 Alpaca 費用
  - 輸出 equity curve、trade log、metrics
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
from copy import deepcopy

from config import Config
from src.data.data_fetcher import fetch_batch
from src.agents.regime_agent import (fit_hmm, compute_features, decode_and_label,
                                      RegimeResult, regime_score_engine)
from src.agents.technicals_agent import technicals_signal
from src.agents.risk_manager import size_position
from src.portfolio.portfolio_manager import _exit_check
from src.state.state import default_state, mark_to_market, close_position, open_position
from src.utils.fees import trade_cost


class BacktestEngine:
    def __init__(self, cfg: Config, tickers=None, start=None, end=None):
        self.cfg = cfg
        self.tickers = tickers or cfg.backtest_universe
        self.market_index = getattr(cfg, "regime_market_index", "SPY")
        self.start = start or cfg.backtest_start
        self.end = end or cfg.backtest_end
        self.state = default_state(cfg.capital_usd)
        self.equity_curve = []   # [{date, equity, cash, n_positions}]
        self.regime_log = []     # [{date, market_index, regime, score, strategy, size_mult}]

    def run(self) -> dict:
        cfg = self.cfg
        market_idx = self.market_index
        print(f"回測 {self.start} ~ {self.end or '最新'}，標的 {len(self.tickers)} 檔...")
        print(f"  市場指數（regime）：{market_idx}")
        print(f"  下載日線（批量，含 {market_idx}）...")

        # Fetch market index + universe together
        all_fetch = [market_idx] + self.tickers
        data = fetch_batch(all_fetch, start=self.start, end=self.end, period="2y")

        # Separate market index data from universe
        market_data = data.get(market_idx, pd.DataFrame())
        if len(market_data) < 60:
            print(f"  ⚠️ {market_idx} 資料不足（{len(market_data)} 根），無法計算 regime")
            return {}

        # Universe data
        valid = {t: df for t, df in data.items() if t != market_idx and len(df) >= 60}
        if not valid:
            print("  ⚠️ 無足夠資料")
            return {}
        self.tickers = list(valid.keys())
        all_dates = sorted(set().union(*[set(df["datetime"]) for df in valid.values()]))
        print(f"  {len(self.tickers)} 檔有效 + {market_idx}（regime），{len(all_dates)} 個交易日")

        # HMM 模型快取（v3: 只有一個 entry — market index）
        hmm_cache = {}  # market_idx -> (model, feats_df, labels, trending, last_fit_idx)

        for di, date in enumerate(all_dates):
            date_str = date.strftime("%Y-%m-%d")
            prices = {}
            slices = {}
            for t in self.tickers:
                df = valid[t]
                sub = df[df["datetime"] <= date]
                if len(sub) < 60:
                    continue
                prices[t] = float(sub["close"].iloc[-1])
                slices[t] = sub

            if not slices:
                continue

            # mark-to-market
            mark_to_market(self.state, prices)

            # ---- v3: Market regime（只在 SPY 上計算，每日一次）----
            spy_sub = market_data[market_data["datetime"] <= date]
            market_info = self._market_regime_for(spy_sub, di, hmm_cache, cfg)
            if market_info is None:
                # SPY data insufficient — safety: no new positions
                global_size_mult = 0.0
                global_regime_s = -1.0
                global_strategy = "cash"
                global_score = 0.0
            else:
                global_size_mult = market_info["position_size_mult"]
                global_regime_s = market_info["score"]  # [-1, 1]
                global_strategy = market_info["strategy"]
                global_score = market_info["regime_score"]
                self.regime_log.append({
                    "date": date_str, "market_index": market_idx,
                    "regime": market_info["regime"],
                    "score": global_score, "strategy": global_strategy,
                    "size_mult": global_size_mult,
                    "components": market_info.get("components", {}),
                })

            # ---- 處理未平倉部位出場（收盤價成交）----
            i = 0
            while i < len(self.state["open_positions"]):
                pos = self.state["open_positions"][i]
                t = pos["ticker"]
                if t not in slices:
                    i += 1
                    continue
                price = prices[t]
                tech_sig = "neutral"
                try:
                    tsig = technicals_signal(slices[t], cfg)
                    tech_sig = tsig["signal"]
                except Exception:
                    pass
                ex = _exit_check(pos, price, date_str, tech_sig, cfg)
                if ex is not None:
                    close_position(self.state, i, price, date_str, ex["reason"], prices)
                    if self.state["trade_log"]:
                        try:
                            held = (datetime.strptime(date_str, "%Y-%m-%d") -
                                    datetime.strptime(self.state["trade_log"][-1]["entry_date"], "%Y-%m-%d")).days
                            self.state["trade_log"][-1]["holding_days"] = held
                        except Exception:
                            pass
                else:
                    i += 1

            # ---- v3: 進場（使用全局 size_mult，個股只跑 technicals）----
            # If market regime says cash → skip all entries
            if global_size_mult <= 0:
                # 記錄權益曲線
                self.equity_curve.append({
                    "date": date_str, "equity": round(self.state["equity"], 2),
                    "cash": round(self.state["cash"], 2),
                    "n_positions": len(self.state["open_positions"]),
                })
                continue

            for t in self.tickers:
                if t not in slices:
                    continue
                sub = slices[t]
                if t in {p["ticker"] for p in self.state["open_positions"]}:
                    continue  # 已持有

                # v3: 只跑 technicals（regime 已由 SPY 全局決定）
                try:
                    tsig = technicals_signal(sub, cfg)
                except Exception:
                    continue
                tech_score = tsig.get("score", 0.0)

                # Net score: 全局 regime_s + 個股 tech_score
                net = cfg.regime_weight * global_regime_s + cfg.technicals_weight * tech_score
                if net <= cfg.entry_threshold:
                    continue

                # 風控 + 下單（全局 size_mult）
                atr_val = tsig.get("atr", 0.0)
                n_open = len(self.state["open_positions"])
                sizing = size_position(self.state["equity"], self.state["cash"],
                                       prices[t], atr_val, global_size_mult, n_open, cfg)
                if not sizing.get("allow"):
                    continue
                pos = {
                    "ticker": t, "direction": "LONG", "shares": sizing["shares"],
                    "entry_price": prices[t], "entry_date": date_str,
                    "atr_at_entry": atr_val, "stop_price": sizing["stop_price"],
                    "take_profit": sizing["take_profit"], "highest_since_entry": prices[t],
                    "entry_regime": market_info["regime"] if market_info else "UNKNOWN",
                    "entry_regime_score": global_score,
                    "entry_market_strategy": global_strategy,
                    "entry_size_mult": global_size_mult,  # v3.2.2 audit field
                    "entry_reasoning": f"net={net:.3f}, tech={tsig['signal']}, "
                                       f"market_score={global_score:.0f}, size_mult={global_size_mult:.2f}",
                }
                open_position(self.state, pos, prices[t], cfg)

            # 記錄權益曲線
            self.equity_curve.append({
                "date": date_str, "equity": round(self.state["equity"], 2),
                "cash": round(self.state["cash"], 2),
                "n_positions": len(self.state["open_positions"]),
            })

        # 回測結束：用最後一日收盤價平掉所有殘留部位
        last_prices = {t: float(valid[t]["close"].iloc[-1]) for t in self.tickers if t in valid}
        last_date = all_dates[-1].strftime("%Y-%m-%d") if all_dates else date_str
        i = 0
        while i < len(self.state["open_positions"]):
            pos = self.state["open_positions"][i]
            px = last_prices.get(pos["ticker"], pos["entry_price"])
            close_position(self.state, i, px, last_date, "BACKTEST_END", last_prices)

        return self._summary()

    def _market_regime_for(self, spy_df: pd.DataFrame, date_idx: int,
                           cache: dict, cfg) -> dict | None:
        """在市場指數（SPY）上計算 regime score。每日一次，HMM 定期重 fit。

        v3: 取代舊的 _regime_for()。只處理 SPY，不處理個股。
        """
        close = spy_df["close"].astype(float)
        feats = compute_features(close, cfg.hmm_vol_window)
        if len(feats) < cfg.hmm_min_obs:
            return None

        market_idx = self.market_index

        # --- HMM (cached, expensive — only one entry: market index) ---
        cached = cache.get(market_idx)
        need_fit = (cached is None) or ((date_idx - cached["last_fit_idx"]) >= cfg.regime_refit_days)
        if need_fit:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    model = fit_hmm(feats.values, cfg)
                    states, stats, labels, trending, spread = decode_and_label(model, feats, cfg)
                except Exception:
                    return None
            posteriors = model.predict_proba(feats.values)
            cache[market_idx] = {
                "model": model, "labels": labels, "trending": trending,
                "last_fit_idx": date_idx, "posteriors": posteriors, "states": states,
            }
        else:
            c = cache[market_idx]
            model = c["model"]
            labels = c["labels"]
            trending = c["trending"]
            try:
                states = model.predict(feats.values)
                posteriors = model.predict_proba(feats.values)
            except Exception:
                return None

        latest_state = int(states[-1])
        latest_prob = float(posteriors[-1, latest_state])
        regime_label = labels.get(latest_state, "SIDEWAYS")
        if not trending:
            regime_label = "RANGE_BOUND"

        # Build RegimeResult for the score engine
        hmm_result = RegimeResult(
            regime=regime_label, trending=trending, latest_prob=latest_prob,
            switch_confidence=1.0 - latest_prob, labels=labels, stats={},
            spread=0.0, latest_state=latest_state, n_states=model.n_components,
        )

        # --- Composite score (all 5 components on SPY data) ---
        sr = regime_score_engine(spy_df, cfg, hmm_result=hmm_result, ticker=market_idx)

        score_normalized = (sr["regime_score"] - 50.0) / 50.0

        return {
            "regime": regime_label, "trending": trending,
            "latest_prob": latest_prob,
            "regime_score": sr["regime_score"],
            "score": round(score_normalized, 3),
            "position_size_mult": sr["position_size_mult"],
            "strategy": sr["strategy"],
            "components": sr["components"],
            "vetoes": sr.get("vetoes", []),
        }

    def _summary(self) -> dict:
        return {
            "equity_curve": self.equity_curve,
            "trade_log": self.state["trade_log"],
            "regime_log": self.regime_log,
            "final_equity": self.state["equity"],
            "starting_equity": self.cfg.capital_usd,
            "tickers": self.tickers,
        }


def compute_metrics(summary: dict, cfg: Config) -> dict:
    """計算績效指標。"""
    trades = summary.get("trade_log", [])
    eq = summary.get("equity_curve", [])
    if not trades or not eq:
        return {"total_trades": len(trades), "total_pnl": 0.0}

    pnls = [t["net_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_pnl = sum(pnls)
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    win_rate = len(wins) / len(trades) if trades else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    avg_win = np.mean(wins) if wins else 0.0
    avg_loss = np.mean(losses) if losses else 0.0
    expectancy = np.mean(pnls) if pnls else 0.0

    # 權益曲線相關
    eq_df = pd.DataFrame(eq)
    eq_df["ret"] = eq_df["equity"].pct_change()
    rets = eq_df["ret"].dropna()
    sharpe = float(np.sqrt(252) * rets.mean() / rets.std()) if len(rets) > 1 and rets.std() > 0 else 0.0
    rolling_max = eq_df["equity"].cummax()
    dd = (eq_df["equity"] - rolling_max) / rolling_max
    max_dd = float(dd.min() * 100) if len(dd) > 0 else 0.0
    total_return = (summary["final_equity"] / summary["starting_equity"] - 1) * 100

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 3),
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_return, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "final_equity": round(summary["final_equity"], 2),
        "starting_equity": round(summary["starting_equity"], 2),
    }
