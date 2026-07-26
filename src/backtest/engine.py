"""
engine.py — swing bot 回測引擎

逐日 replay：每個交易日，用「截至當日」的資料跑 regime + technicals + risk + PM，
模擬收盤成交（含費用），追蹤權益曲線與交易紀錄。

重點：
  - 無 lookahead：agents 只看 ≤ 當日的資料
  - HMM 每 regime_refit_days 天重 fit 一次（平衡準確度與速度）
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
from src.agents.regime_agent import fit_hmm, compute_features, decode_and_label
from src.agents.technicals_agent import technicals_signal
from src.agents.risk_manager import size_position
from src.portfolio.portfolio_manager import _exit_check
from src.state.state import default_state, mark_to_market, close_position, open_position
from src.utils.fees import trade_cost


class BacktestEngine:
    def __init__(self, cfg: Config, tickers=None, start=None, end=None):
        self.cfg = cfg
        self.tickers = tickers or cfg.backtest_universe
        self.start = start or cfg.backtest_start
        self.end = end or cfg.backtest_end
        self.state = default_state(cfg.capital_usd)
        self.equity_curve = []   # [{date, equity, cash, n_positions}]
        self.regime_log = []     # [{date, ticker, regime, trending, prob}]

    def run(self) -> dict:
        cfg = self.cfg
        print(f"回測 {self.start} ~ {self.end or '最新'}，標的 {len(self.tickers)} 檔...")
        print(f"  下載日線（批量）...")
        data = fetch_batch(self.tickers, start=self.start, end=self.end, period="2y")
        # 對齊所有標的的交易日索引
        valid = {t: df for t, df in data.items() if len(df) >= 60}
        if not valid:
            print("  ⚠️ 無足夠資料")
            return {}
        self.tickers = list(valid.keys())
        all_dates = sorted(set().union(*[set(df["datetime"]) for df in valid.values()]))
        print(f"  {len(self.tickers)} 檔有效，{len(all_dates)} 個交易日")

        # HMM 模型快取（每 ticker 一個，定期重 fit）
        hmm_cache = {}  # ticker -> (model, feats_df, labels, trending, last_fit_date_idx)

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
                # 輕量技術訊號給出場用（避免每天跑完整 ensemble 太慢）
                try:
                    tsig = technicals_signal(slices[t], cfg)
                    tech_sig = tsig["signal"]
                except Exception:
                    pass
                ex = _exit_check(pos, price, date_str, tech_sig, cfg)
                if ex is not None:
                    close_position(self.state, i, price, date_str, ex["reason"], prices)
                    # 填 holding_days
                    if self.state["trade_log"]:
                        try:
                            held = (datetime.strptime(date_str, "%Y-%m-%d") -
                                    datetime.strptime(self.state["trade_log"][-1]["entry_date"], "%Y-%m-%d")).days
                            self.state["trade_log"][-1]["holding_days"] = held
                        except Exception:
                            pass
                else:
                    i += 1

            # ---- 進場：每 N 天重 fit HMM，跑 regime + technicals ----
            for t in self.tickers:
                if t not in slices:
                    continue
                sub = slices[t]
                close = sub["close"]
                if t in {p["ticker"] for p in self.state["open_positions"]}:
                    continue  # 已持有

                # regime（定期重 fit）
                regime_info = self._regime_for(close, t, di, hmm_cache, cfg)
                if regime_info is None:
                    continue
                self.regime_log.append({"date": date_str, "ticker": t, **regime_info["brief"]})
                if regime_info["regime"] not in ("BULL",) or not regime_info["trending"]:
                    continue  # 只在 BULL 開新多單

                # technicals
                try:
                    tsig = technicals_signal(sub, cfg)
                except Exception:
                    continue
                tech_score = tsig.get("score", 0.0)
                regime_score = 1.0  # BULL
                net = cfg.regime_weight * regime_score + cfg.technicals_weight * tech_score
                if net <= cfg.entry_threshold:
                    continue

                # 風控 + 下單
                atr_val = tsig.get("atr", 0.0)
                n_open = len(self.state["open_positions"])
                sizing = size_position(self.state["equity"], self.state["cash"],
                                       prices[t], atr_val, "BULL", n_open, cfg)
                if not sizing.get("allow"):
                    continue
                pos = {
                    "ticker": t, "direction": "LONG", "shares": sizing["shares"],
                    "entry_price": prices[t], "entry_date": date_str,
                    "atr_at_entry": atr_val, "stop_price": sizing["stop_price"],
                    "take_profit": sizing["take_profit"], "highest_since_entry": prices[t],
                    "entry_regime": "BULL",
                    "entry_reasoning": f"net={net:.3f}, tech={tsig['signal']}",
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

    def _regime_for(self, close, ticker, date_idx, cache, cfg) -> dict | None:
        """取得某 ticker 在當日的 regime。定期重 fit HMM。"""
        feats = compute_features(close, cfg.hmm_vol_window)
        if len(feats) < cfg.hmm_min_obs:
            return None
        cached = cache.get(ticker)
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
            cache[ticker] = {
                "model": model, "labels": labels, "trending": trending,
                "last_fit_idx": date_idx, "posteriors": posteriors, "states": states,
            }
        else:
            c = cache[ticker]
            model = c["model"]
            labels = c["labels"]
            trending = c["trending"]
            # 用現有 model 預測當日
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
        return {
            "regime": regime_label, "trending": trending,
            "latest_prob": latest_prob,
            "brief": {"regime": regime_label, "trending": trending,
                      "prob": round(latest_prob, 3)},
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
