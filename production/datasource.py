"""
datasource.py — DataSource abstraction (Production V2 unified pipeline)

One deterministic data layer shared by LIVE briefing and the
production-equivalent BACKTEST. Every OHLCV request returns
    (DataFrame, provenance)
so the caller can record exactly which provider/file fed each dataset.

Providers
---------
CachedSource   : read-only historical cache (data/cache/equities/<T>.csv).
                 Point-in-time slices (`datetime <= as_of`). Used by the
                 backtest replay (no network, no look-ahead).
YFinanceSource : LIVE source. Cache-first, then yfinance download. Refreshes
                 the cache with the latest bar when as_of is newer than the
                 cache tail (so a live run also keeps history for the next
                 backtest). Optional Stooq recovery (labelled, never silent).
StooqSource    : secondary validation/recovery only (CSV endpoint). Never
                 the primary path.

Fail-loud semantics
-------------------
* A missing ticker returns (None, provenance{ok:False, error}) — never an
  empty-but-valid frame that could be mistaken for a real "no data" state.
* Providers are NEVER mixed for one ticker inside one call. If a fallback is
  used the provenance provider field says so (yfinance -> stooq is labelled).
* CachedSource never fabricates bars and never extends past the cache tail.
"""
from __future__ import annotations

import os
import sys
import abc
import io
from dataclasses import dataclass, field

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.data.data_fetcher import fetch_daily  # yfinance single-ticker fetch


class DataSourceError(Exception):
    """Raised when a data source cannot satisfy a request at all
    (no cache AND network failed AND no fallback)."""


# ---------------------------------------------------------------------------
# Provenance — attached to every dataset so DecisionRecord can audit sources
# ---------------------------------------------------------------------------
@dataclass
class Provenance:
    provider: str            # "cache" | "yfinance" | "stooq"
    ok: bool = True
    file: str | None = None  # cache file used (if any)
    rows: int = 0
    as_of_bound: str | None = None   # last datetime available in the returned df
    stale: bool = False      # data is older than requested as_of (no update)
    error: str | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "provider": self.provider, "ok": self.ok, "file": self.file,
            "rows": self.rows, "as_of_bound": self.as_of_bound,
            "stale": self.stale, "error": self.error, "note": self.note,
        }


# ---------------------------------------------------------------------------
# Abstract source
# ---------------------------------------------------------------------------
class DataSource(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def universe(self) -> list[str]:
        """Candidate ticker list (excluding benchmark)."""

    @property
    def benchmark(self) -> str:
        return "SPY"

    @abc.abstractmethod
    def get_ohlcv(self, ticker: str, as_of: str | None = None,
                  min_bars: int = 60) -> tuple[pd.DataFrame | None, Provenance]:
        """Return (df sliced to <= as_of, provenance). df is None on failure."""

    def load_many(self, tickers: list[str], as_of: str | None = None,
                  min_bars: int = 60) -> dict[str, pd.DataFrame]:
        """Batch convenience (default: loop get_ohlcv). Sources may override."""
        out: dict[str, pd.DataFrame] = {}
        for t in tickers:
            df, _ = self.get_ohlcv(t, as_of=as_of, min_bars=min_bars)
            if df is not None and len(df) > 0:
                out[t] = df
        return out

    # -- helpers shared by implementations ---------------------------------
    @staticmethod
    def _slice_asof(df: pd.DataFrame, as_of: str | None) -> pd.DataFrame:
        if as_of is None or "datetime" not in df.columns:
            return df.reset_index(drop=True)
        return df[pd.to_datetime(df["datetime"]) <= pd.Timestamp(as_of)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# CachedSource — read-only historical cache (backtest / point-in-time)
# ---------------------------------------------------------------------------
class CachedSource(DataSource):
    name = "cached"

    def __init__(self, cache_dir: str, universe_file_sp500: str | None = None,
                 universe_file_ndx100: str | None = None):
        self.cache_dir = cache_dir
        self._mem: dict[str, pd.DataFrame] = {}          # lazy in-memory cache
        self._universe: list[str] | None = None
        self._u_sp500 = universe_file_sp500
        self._u_ndx100 = universe_file_ndx100

    # -- universe ----------------------------------------------------------
    @staticmethod
    def _read_sp500_current(path: str) -> list[str]:
        df = pd.read_csv(path)
        col = "Symbol" if "Symbol" in df.columns else df.columns[0]
        return [str(s).strip().replace(".", "-")
                for s in df[col].tolist() if str(s).strip() not in ("", "nan")]

    @staticmethod
    def _read_ndx100_latest(path: str) -> list[str]:
        df = pd.read_csv(path)
        last = df.iloc[-1]["tickers"]
        return [t.strip() for t in str(last).split(",") if t.strip()]

    def universe(self) -> list[str]:
        if self._universe is None:
            tickers: list[str] = []
            if self._u_sp500 and os.path.exists(self._u_sp500):
                tickers += self._read_sp500_current(self._u_sp500)
            if self._u_ndx100 and os.path.exists(self._u_ndx100):
                tickers += self._read_ndx100_latest(self._u_ndx100)
            # fall back to whatever is actually cached
            if not tickers and os.path.isdir(self.cache_dir):
                tickers = [f[:-4] for f in os.listdir(self.cache_dir)
                           if f.endswith(".csv") and "-K1" not in f]
            tickers = sorted(set(tickers) - {self.benchmark})
            self._universe = tickers
        return self._universe

    # -- data --------------------------------------------------------------
    def _read(self, ticker: str) -> pd.DataFrame | None:
        if ticker in self._mem:
            return self._mem[ticker]
        p = os.path.join(self.cache_dir, f"{ticker}.csv")
        if not os.path.exists(p):
            return None
        try:
            df = pd.read_csv(p, parse_dates=["datetime"])
            if "datetime" not in df.columns or len(df) == 0:
                return None
            df = df.sort_values("datetime").reset_index(drop=True)
            self._mem[ticker] = df
            return df
        except Exception:
            return None

    def get_ohlcv(self, ticker: str, as_of: str | None = None,
                  min_bars: int = 60) -> tuple[pd.DataFrame | None, Provenance]:
        df = self._read(ticker)
        if df is None:
            return None, Provenance("cache", ok=False,
                                    error=f"no cache file {ticker}.csv")
        sub = self._slice_asof(df, as_of)
        bound = sub["datetime"].max().strftime("%Y-%m-%d") if len(sub) else None
        if len(sub) < min_bars:
            return None, Provenance(
                "cache", ok=False, file=f"{ticker}.csv", rows=len(sub),
                as_of_bound=bound,
                error=f"cache insufficient ({len(sub)} < {min_bars} bars)")
        return sub, Provenance("cache", file=f"{ticker}.csv", rows=len(sub),
                               as_of_bound=bound)


# ---------------------------------------------------------------------------
# StooqSource — secondary validation/recovery (CSV endpoint, no dependency)
# ---------------------------------------------------------------------------
class StooqSource(DataSource):
    """Recovery/validation source. yfinance stays the primary provider."""
    name = "stooq"

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def universe(self) -> list[str]:
        # Stooq is never used to pick the universe — only to recover a ticker.
        return []

    def get_ohlcv(self, ticker: str, as_of: str | None = None,
                  min_bars: int = 60) -> tuple[pd.DataFrame | None, Provenance]:
        import requests
        url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d"
        try:
            r = requests.get(url, timeout=self.timeout)
            if r.status_code != 200 or not r.text.strip() or r.text.startswith("Exceeded"):
                return None, Provenance("stooq", ok=False,
                                        error=f"stooq HTTP {r.status_code}")
            df = pd.read_csv(io.StringIO(r.text))
            if "Date" not in df.columns or len(df) == 0:
                return None, Provenance("stooq", ok=False, error="stooq empty")
            df = df.rename(columns={"Date": "datetime", "Open": "open",
                                    "High": "high", "Low": "low",
                                    "Close": "close", "Volume": "volume"})
            df["datetime"] = pd.to_datetime(df["datetime"])
            sub = self._slice_asof(df, as_of)
            bound = sub["datetime"].max().strftime("%Y-%m-%d") if len(sub) else None
            if len(sub) < min_bars:
                return None, Provenance(
                    "stooq", ok=False, rows=len(sub), as_of_bound=bound,
                    error=f"stooq insufficient ({len(sub)} < {min_bars} bars)")
            return sub, Provenance("stooq", rows=len(sub), as_of_bound=bound,
                                   note="secondary validation/recovery source")
        except Exception as exc:  # network / parse
            return None, Provenance("stooq", ok=False, error=str(exc)[:200])


# ---------------------------------------------------------------------------
# YFinanceSource — LIVE source (cache-first, network refresh, labelled stooq)
# ---------------------------------------------------------------------------
class YFinanceSource(DataSource):
    name = "yfinance"

    def __init__(self, cache_dir: str, universe_file_sp500: str | None = None,
                 universe_file_ndx100: str | None = None,
                 allow_stooq_fallback: bool = False,
                 stooq: StooqSource | None = None,
                 refresh_network: bool = True):
        self.cache_dir = cache_dir
        self._u_sp500 = universe_file_sp500
        self._u_ndx100 = universe_file_ndx100
        self._fallback = allow_stooq_fallback
        self._stooq = stooq or StooqSource()
        self._refresh = refresh_network
        self._mem: dict[str, pd.DataFrame] = {}

    def universe(self) -> list[str]:
        # Same membership files as CachedSource — live Wikipedia scrape stays
        # an option for a future wiring; membership files keep both sides
        # deterministic and identical.
        cs = CachedSource(self.cache_dir, self._u_sp500, self._u_ndx100)
        return cs.universe()

    # -- internals ---------------------------------------------------------
    def _read_cache(self, ticker: str) -> pd.DataFrame | None:
        p = os.path.join(self.cache_dir, f"{ticker}.csv")
        if not os.path.exists(p):
            return None
        try:
            df = pd.read_csv(p, parse_dates=["datetime"])
            if "datetime" not in df.columns or len(df) == 0:
                return None
            return df.sort_values("datetime").reset_index(drop=True)
        except Exception:
            return None

    def _write_cache(self, ticker: str, df: pd.DataFrame) -> None:
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            df.to_csv(os.path.join(self.cache_dir, f"{ticker}.csv"), index=False)
        except Exception:
            pass  # cache is best-effort for a live run

    def _download(self, ticker: str) -> pd.DataFrame | None:
        try:
            df = fetch_daily(ticker, period="2y")
            return df if df is not None and len(df) > 0 else None
        except Exception:
            return None

    def _needs_refresh(self, cached: pd.DataFrame | None, as_of: str | None) -> bool:
        if not self._refresh:
            return False
        if cached is None:
            return True
        if as_of is None:
            return True   # live "latest" request -> always try network first
        last = cached["datetime"].max()
        return pd.Timestamp(as_of) > last

    # -- get_ohlcv ---------------------------------------------------------
    def get_ohlcv(self, ticker: str, as_of: str | None = None,
                  min_bars: int = 60) -> tuple[pd.DataFrame | None, Provenance]:
        cached = self._read_cache(ticker)

        # 1) point-in-time request that the cache already covers -> cache only
        if (not self._needs_refresh(cached, as_of)) and cached is not None:
            sub = self._slice_asof(cached, as_of)
            bound = sub["datetime"].max().strftime("%Y-%m-%d") if len(sub) else None
            if len(sub) >= min_bars:
                return sub, Provenance("cache", file=f"{ticker}.csv",
                                       rows=len(sub), as_of_bound=bound)
            return None, Provenance(
                "cache", ok=False, file=f"{ticker}.csv", rows=len(sub),
                as_of_bound=bound,
                error=f"cache insufficient ({len(sub)} < {min_bars})")

        # 2) network refresh required
        if cached is not None:
            base = cached
        else:
            base = None
        dl = self._download(ticker) if self._refresh else None
        if dl is not None and len(dl) >= min_bars:
            # merge: keep the longer history, refresh tail
            if base is not None and len(base) > len(dl):
                merged = pd.concat([base, dl]).drop_duplicates(
                    subset="datetime").sort_values("datetime").reset_index(drop=True)
            else:
                merged = dl
            self._write_cache(ticker, merged)
            sub = self._slice_asof(merged, as_of)
            bound = sub["datetime"].max().strftime("%Y-%m-%d") if len(sub) else None
            return sub, Provenance("yfinance", file=f"{ticker}.csv",
                                   rows=len(sub), as_of_bound=bound)

        # 3) network failed -> stooq recovery (labelled)
        if self._fallback:
            sdf, sp = self._stooq.get_ohlcv(ticker, as_of=as_of, min_bars=min_bars)
            if sdf is not None:
                sp.note = "yfinance failed -> stooq recovery (labelled)"
                return sdf, sp

        # 4) stale cache acceptable? -> return it marked stale
        if base is not None:
            sub = self._slice_asof(base, as_of)
            bound = sub["datetime"].max().strftime("%Y-%m-%d") if len(sub) else None
            if len(sub) >= min_bars:
                return sub, Provenance("cache", file=f"{ticker}.csv",
                                       rows=len(sub), as_of_bound=bound,
                                       stale=True,
                                       note="network refresh failed; stale cache used")
        return None, Provenance(
            "yfinance", ok=False, error=f"no data for {ticker} "
                                        f"(cache+nor network nor stooq)")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_cached_source(cfg) -> CachedSource:
    """Backtest / offline source from a ProductionConfig."""
    return CachedSource(
        cfg.cache_dir,
        universe_file_sp500=_constituent(cfg, "sp500_current.csv"),
        universe_file_ndx100=_constituent(cfg, "nasdaq100_thuningxu.csv"),
    )


def build_live_source(cfg, allow_stooq_fallback: bool = False) -> YFinanceSource:
    """LIVE source from a ProductionConfig (yfinance primary)."""
    return YFinanceSource(
        cfg.cache_dir,
        universe_file_sp500=_constituent(cfg, "sp500_current.csv"),
        universe_file_ndx100=_constituent(cfg, "nasdaq100_thuningxu.csv"),
        allow_stooq_fallback=allow_stooq_fallback,
        refresh_network=True,
    )


def _constituent(cfg, fname: str) -> str:
    p = os.path.join(cfg.base_dir, "data", "constituents", fname)
    return p if os.path.exists(p) else None
