"""
report.py — 回測 HTML 報告產生器（自包含、可離線開啟）

用 matplotlib 產圖存 PNG，base64 嵌入 HTML。
包含：權益曲線、回撤、交易 P/L 分佈、regime 時間線、績效指標表、交易紀錄表。
"""
from __future__ import annotations
import base64
import io
from pathlib import Path
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# macOS CJK 字體設定（避免中文方塊）
plt.rcParams["font.sans-serif"] = ["PingFang TC", "PingFang SC", "Heiti TC",
                                    "Arial Unicode MS", "STHeiti", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _plot_equity(eq_df, starting) -> str:
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(pd.to_datetime(eq_df["date"]), eq_df["equity"], color="#d62728", lw=1.4, label="Equity")
    ax.axhline(starting, color="#7f7f7f", ls="--", lw=1, label=f"Start ${starting:,.0f}")
    ax.fill_between(pd.to_datetime(eq_df["date"]), starting, eq_df["equity"],
                    where=eq_df["equity"] >= starting, color="#d62728", alpha=0.12)
    ax.fill_between(pd.to_datetime(eq_df["date"]), starting, eq_df["equity"],
                    where=eq_df["equity"] < starting, color="#2ca02c", alpha=0.12)
    ax.set_title("Equity Curve (漲紅跌綠)", fontsize=12)
    ax.set_ylabel("USD")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)
    return _fig_to_b64(fig)


def _plot_drawdown(eq_df) -> str:
    eq = eq_df["equity"]
    rolling_max = eq.cummax()
    dd = (eq - rolling_max) / rolling_max * 100
    fig, ax = plt.subplots(figsize=(11, 3))
    ax.fill_between(pd.to_datetime(eq_df["date"]), dd, 0, color="#2ca02c", alpha=0.4)
    ax.set_title("Drawdown (%)", fontsize=12)
    ax.set_ylabel("%")
    ax.grid(alpha=0.3)
    return _fig_to_b64(fig)


def _plot_trade_pnl(trades) -> str:
    if not trades:
        return ""
    pnl = [t["net_pnl"] for t in trades]
    colors = ["#d62728" if p > 0 else "#2ca02c" for p in pnl]
    fig, ax = plt.subplots(figsize=(11, 3.5))
    ax.bar(range(len(pnl)), pnl, color=colors, width=0.8)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Per-Trade Net P/L (漲紅跌綠)", fontsize=12)
    ax.set_ylabel("USD")
    ax.set_xlabel("Trade #")
    ax.grid(alpha=0.3)
    return _fig_to_b64(fig)


def _plot_regime_timeline(regime_log) -> str:
    if not regime_log:
        return ""
    df = pd.DataFrame(regime_log)
    df["date"] = pd.to_datetime(df["date"])
    # 只取每檔第一個 ticker 的 regime 序列做代表（或多數投票）
    t0 = df["ticker"].iloc[0]
    sub = df[df["ticker"] == t0].sort_values("date")
    if sub.empty:
        return ""
    color_map = {"BULL": "#d62728", "BEAR": "#2ca02c", "SIDEWAYS": "#7f7f7f",
                 "RANGE_BOUND": "#bcbcbc"}
    fig, ax = plt.subplots(figsize=(11, 2.5))
    for i in range(len(sub)):
        r = sub.iloc[i]
        ax.axvspan(r["date"], r["date"], color=color_map.get(r["regime"], "#7f7f7f"))
    # 用散點表達 regime 隨時間
    for regime, c in color_map.items():
        s = sub[sub["regime"] == regime]
        if not s.empty:
            ax.scatter(s["date"], [1] * len(s), color=c, s=8, label=regime, alpha=0.7)
    ax.set_title(f"Regime Timeline ({t0}, sample)", fontsize=12)
    ax.set_yticks([])
    ax.legend(loc="upper right", fontsize=8, ncol=4)
    ax.grid(alpha=0.3)
    return _fig_to_b64(fig)


def _metrics_table(m: dict) -> str:
    rows = [
        ("總交易數", f"{m['total_trades']}"),
        ("勝率", f"{m['win_rate']*100:.1f}%"),
        ("總淨利", f"${m['total_pnl']:,.2f}"),
        ("總報酬率", f"{m['total_return_pct']:+.2f}%"),
        ("獲利因子 PF", f"{m['profit_factor']}"),
        ("平均獲利", f"${m['avg_win']:,.2f}"),
        ("平均虧損", f"${m['avg_loss']:,.2f}"),
        ("期望值/筆", f"${m['expectancy']:,.2f}"),
        ("Sharpe", f"{m['sharpe']}"),
        ("最大回撤", f"{m['max_drawdown_pct']:.2f}%"),
        ("起始權益", f"${m['starting_equity']:,.2f}"),
        ("最終權益", f"${m['final_equity']:,.2f}"),
    ]
    html = '<table class="metrics"><tbody>'
    for k, v in rows:
        color = "#d62728" if ("+" in str(v) and "%" in str(v)) else ("#2ca02c" if ("-" in str(v) and "%" in str(v)) else "#222")
        html += f'<tr><td class="k">{k}</td><td class="v" style="color:{color}">{v}</td></tr>'
    html += '</tbody></table>'
    return html


def _trades_table(trades) -> str:
    if not trades:
        return "<p>無交易紀錄。</p>"
    html = '<table class="trades"><thead><tr>'
    cols = ["ticker", "shares", "entry_price", "exit_price", "entry_date", "exit_date",
            "holding_days", "net_pnl", "return_pct", "exit_reason", "entry_regime"]
    labels = ["標的", "股數", "進場", "出場", "進場日", "出場日", "持有天", "淨利", "報酬%", "出場原因", "進場regime"]
    for l in labels:
        html += f"<th>{l}</th>"
    html += "</tr></thead><tbody>"
    for t in trades:
        pnl = t.get("net_pnl", 0)
        color = "#d62728" if pnl > 0 else "#2ca02c"
        html += '<tr style="color:' + color + '">'
        for c in cols:
            v = t.get(c, "")
            if isinstance(v, float):
                v = f"{v:.2f}"
            html += f"<td>{v}</td>"
        html += "</tr>"
    html += "</tbody></table>"
    return html


def generate_html(summary: dict, metrics: dict, cfg, out_path: str) -> str:
    eq_df = pd.DataFrame(summary.get("equity_curve", []))
    trades = summary.get("trade_log", [])
    regime_log = summary.get("regime_log", [])
    starting = summary["starting_equity"]

    eq_b64 = _plot_equity(eq_df, starting) if not eq_df.empty else ""
    dd_b64 = _plot_drawdown(eq_df) if not eq_df.empty else ""
    pnl_b64 = _plot_trade_pnl(trades)
    reg_b64 = _plot_regime_timeline(regime_log)

    tickers = ", ".join(summary.get("tickers", []))
    period = f"{eq_df['date'].iloc[0]} ~ {eq_df['date'].iloc[-1]}" if not eq_df.empty else "—"

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>Swing Bot 回測報告</title>
<style>
  body {{ font-family: -apple-system, "PingFang TC", "Microsoft JhengHei", sans-serif;
         background:#fafafa; color:#222; margin:0; padding:24px; max-width:1100px; }}
  h1 {{ color:#d62728; border-bottom:2px solid #d62728; padding-bottom:8px; }}
  h2 {{ margin-top:32px; color:#333; }}
  .meta {{ color:#666; font-size:13px; margin-bottom:16px; }}
  img.chart {{ width:100%; border:1px solid #e0e0e0; border-radius:6px; margin:8px 0 20px; background:#fff; }}
  .metrics {{ border-collapse:collapse; width:100%; max-width:500px; }}
  .metrics td {{ padding:8px 12px; border-bottom:1px solid #eee; }}
  .metrics .k {{ color:#666; width:45%; }}
  .metrics .v {{ font-weight:bold; font-size:15px; }}
  .trades {{ border-collapse:collapse; width:100%; font-size:12px; }}
  .trades th {{ background:#333; color:#fff; padding:6px; text-align:left; }}
  .trades td {{ padding:5px 6px; border-bottom:1px solid #eee; }}
  .trades tbody tr:nth-child(even) {{ background:#f5f5f5; }}
  .summary-box {{ background:#fff; border:1px solid #e0e0e0; border-radius:8px; padding:16px; margin:12px 0; }}
</style></head><body>
<h1>Swing Trading Bot — 回測報告</h1>
<div class="meta">回測區間：{period}　|　標的：{tickers}　|　本金：${starting:,.0f} USD</div>

<h2>績效指標</h2>
<div class="summary-box">{_metrics_table(metrics)}</div>

<h2>權益曲線</h2>
{f'<img class="chart" src="data:image/png;base64,{eq_b64}">' if eq_b64 else '<p>無資料</p>'}

<h2>回撤</h2>
{f'<img class="chart" src="data:image/png;base64,{dd_b64}">' if dd_b64 else '<p>無資料</p>'}

<h2>每筆交易淨利</h2>
{f'<img class="chart" src="data:image/png;base64,{pnl_b64}">' if pnl_b64 else '<p>無交易</p>'}

<h2>Regime 時間線（樣本）</h2>
{f'<img class="chart" src="data:image/png;base64,{reg_b64}">' if reg_b64 else '<p>無 regime 紀錄</p>'}

<h2>交易紀錄</h2>
{_trades_table(trades)}

<div class="meta" style="margin-top:32px;">
  報告由 WB swing bot 產生。配色採中文市場慣例：漲紅跌綠。<br>
  回測含 Alpaca 費用模型（SEC/TAF/滑點），收盤成交，無 lookahead。
</div>
</body></html>"""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html, encoding="utf-8")
    return out_path
