"""
fees.py — Alpaca 美股交易成本模型（2026-04 Brokerage Fee Schedule）

只模擬成本，不下單。買進：佣金0 + 滑點；賣出：佣金0 + SEC費 + FINRA TAF + 滑點。
"""
from __future__ import annotations


def trade_cost(shares: int, price: float, is_sell: bool, cfg) -> float:
    """單邊交易成本（USD）。"""
    if shares <= 0 or price <= 0:
        return 0.0
    notional = shares * price
    cost = 0.0
    cost += cfg.commission_per_share * shares
    cost += cfg.slippage_pct * notional
    if is_sell:
        cost += cfg.sec_fee_per_dollar * notional
        taf = min(cfg.finra_taf_per_share * shares, cfg.finra_taf_cap)
        cost += taf
    return cost
