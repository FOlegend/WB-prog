"""
data_validity.py — BS-3/BS-4/BS-6: input freshness & temporal-consistency contract

WHAT THIS IS
------------
A **data-validity** layer, not a strategy layer. It answers one question:

    "Can this Regime decision be trusted for the date it claims to be about?"

It computes that answer and records it. It does NOT change the regime
mathematics, does not gate historical replay, and does not pick a fallback
regime. Those are explicitly out of scope (task §1).

THE FIVE STATES (task §14)
--------------------------
    VALID                  PIT-correct, fresh enough, temporally consistent
    STALE                  at least one required input older than the policy max
    TEMPORALLY_INCONSISTENT each input may be fresh enough on its own, but their
                            effective dates do not satisfy the Regime contract
    MISSING                a required input is unavailable
    INVALID                the decision cannot be trusted (any of the above)

These are kept distinct rather than collapsed into None, because the operator
response differs: MISSING may be transient, STALE may be fixed by a refresh,
INVALID must stop the run.

TEMPORAL CONSISTENCY (task §6)
-------------------------------
The Regime composite is 0.5*HMM(SPY) + 0.5*breadth_percentile. Those two halves
are read from independent caches that can advance at different rates. This module
makes the pair explicit:

    regime_as_of / spy_tail_date / breadth_tail_date
    spy_age_days / breadth_age_days
    spy_matches_as_of / breadth_matches_as_of
    regime_input_temporal_consistent / regime_input_freshness_valid

and refuses to describe a mixed-vintage composite as current.

WHAT IT DELIBERATELY DOES NOT DO (task §8)
-----------------------------------------
* no silent substitution (SPY as a breadth proxy, a synthetic estimate, a
  default regime, a default multiplier)
* no automatic degradation of STALE into BULL/BEAR/SIDEWAYS
* no autonomous fallback decision

A stale input yields a state and a reason. What to do about it is the human's
call; this module only refuses to hide it.

HISTORICAL REPLAY IS UNAFFECTED (task §9)
-----------------------------------------
`enforce` is opt-in and is switched OFF for the backtest path. In historical
replay `breadth_age_days` is necessarily large relative to wall-clock today, but
the breadth tail is exactly the as_of date, so `breadth_matches_as_of` is True
and the replay is PIT-correct. Only LIVE runs, where as_of is "now" and the tail
is genuinely behind, can be invalid.
"""
from __future__ import annotations

import datetime
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.contracts.base import ContractError, Provenance, require_bool
from production.contracts.base import require_non_empty_str, require_session_date
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Controlled vocabulary (task §14)
# ---------------------------------------------------------------------------
VALIDITY_VALID = "VALID"
VALIDITY_STALE = "STALE"
VALIDITY_STALE_CONSTITUENTS = "STALE_CONSTITUENTS"
VALIDITY_TEMPORALLY_INCONSISTENT = "TEMPORALLY_INCONSISTENT"
VALIDITY_MISSING = "MISSING"
VALIDITY_INVALID = "INVALID"

VALIDITY_STATES = frozenset({
    VALIDITY_VALID, VALIDITY_STALE, VALIDITY_STALE_CONSTITUENTS,
    VALIDITY_TEMPORALLY_INCONSISTENT,
    VALIDITY_MISSING, VALIDITY_INVALID,
})

# reason codes
RSN_OK = "OK"
RSN_BREADTH_MISSING = "BREADTH_MISSING"
RSN_SPY_MISSING = "SPY_MISSING"
RSN_CONSTITUENTS_MISSING = "CONSTITUENTS_MISSING"
RSN_BREADTH_STALE = "BREADTH_STALE"
RSN_SPY_STALE = "SPY_STALE"
RSN_CONSTITUENTS_STALE = "CONSTITUENTS_STALE"
RSN_BREADTH_SPY_DATE_MISMATCH = "BREADTH_SPY_EFFECTIVE_DATE_MISMATCH"
RSN_CONSTITUENT_CARRY_FORWARD = "CONSTITUENT_MEMBERSHIP_CARRIED_FORWARD"
RSN_BREADTH_FUTURE = "BREADTH_TAIL_AFTER_AS_OF"
RSN_SPY_FUTURE = "SPY_TAIL_AFTER_AS_OF"

VALIDITY_REASON_CODES = frozenset({
    RSN_OK, RSN_BREADTH_MISSING, RSN_SPY_MISSING, RSN_CONSTITUENTS_MISSING,
    RSN_BREADTH_STALE, RSN_SPY_STALE, RSN_CONSTITUENTS_STALE,
    RSN_BREADTH_SPY_DATE_MISMATCH, RSN_CONSTITUENT_CARRY_FORWARD,
    RSN_BREADTH_FUTURE, RSN_SPY_FUTURE,
})


def _d(s: str) -> datetime.date:
    return datetime.date.fromisoformat(str(s)[:10])


@dataclass(frozen=True)
class InputRef:
    """One Regime input's temporal identity (task §5)."""
    name: str
    tail_date: str | None          # effective date of the last observation
    requested_as_of: str
    n_observations: int = 0
    source: str = "unspecified"
    # Task §10 — the price basis the observations are expressed in. A series is
    # only comparable to another if their basis agrees, and the repo's history
    # has already shown that a basis change (a dividend restatement, a split)
    # silently rewrites every earlier bar. Recorded, never acted on here.
    price_basis: str | None = None
    # For breadth, the OHLCV tail the breadth was actually built from. Breadth
    # is a DERIVED observation, so its own tail date is not sufficient to
    # reconstruct what it measured.
    ohlcv_tail_date: str | None = None

    @property
    def age_days(self) -> int | None:
        if self.tail_date is None:
            return None
        return (_d(self.requested_as_of) - _d(self.tail_date)).days

    @property
    def matches_as_of(self) -> bool | None:
        if self.tail_date is None:
            return None
        return _d(self.tail_date) == _d(self.requested_as_of)

    @property
    def is_future(self) -> bool:
        if self.tail_date is None:
            return False
        return _d(self.tail_date) > _d(self.requested_as_of)

    def as_dict(self) -> dict:
        return {"name": self.name, "tail_date": self.tail_date,
                "requested_as_of": self.requested_as_of,
                "n_observations": self.n_observations, "source": self.source,
                "age_days": self.age_days, "matches_as_of": self.matches_as_of,
                "price_basis": self.price_basis,
                "ohlcv_tail_date": self.ohlcv_tail_date}


@dataclass(frozen=True)
class FreshnessPolicy:
    """The approved freshness contract (task §7).

    Values are NOT chosen here — the constructor requires them explicitly so no
    default can silently become a production threshold. `max_age_days` is in
    CALENDAR days against the requested as_of.

    `temporal_skew_days` bounds how far apart the two Regime inputs' effective
    dates may be. Zero means "the same date"; the shipped proposal uses a small
    non-zero value because the two caches refresh independently.
    """
    max_age_days: int
    temporal_skew_days: int
    # Constituent membership is a DEPENDENCY OF breadth, not a separate Regime
    # input: `PitMembership.members_as_of` forward-fills the latest snapshot, so
    # an old snapshot silently makes recent breadth a membership guess. It
    # therefore needs its own bound rather than riding on the breadth tail.
    # `None` disables the constituent check (historical replay, where the
    # membership snapshot is contemporaneous with the observation by
    # construction).
    max_constituent_age_days: int | None = None
    policy_id: str = "unversioned"
    approved_by: str = "UNAPPROVED"
    on_failure: str = "RECORD_ONLY"     # RECORD_ONLY | BLOCK_DECISION
    rationale: str = ""

    def __post_init__(self):
        if not isinstance(self.max_age_days, int) or isinstance(self.max_age_days, bool):
            raise ContractError("FreshnessPolicy", "max_age_days",
                                "expected int calendar days")
        if self.max_age_days < 0:
            raise ContractError("FreshnessPolicy", "max_age_days",
                                f"expected >= 0, got {self.max_age_days}")
        if not isinstance(self.temporal_skew_days, int) or isinstance(
                self.temporal_skew_days, bool):
            raise ContractError("FreshnessPolicy", "temporal_skew_days",
                                "expected int calendar days")
        if self.temporal_skew_days < 0:
            raise ContractError("FreshnessPolicy", "temporal_skew_days",
                                f"expected >= 0, got {self.temporal_skew_days}")
        if self.max_constituent_age_days is not None:
            if not isinstance(self.max_constituent_age_days, int) or isinstance(
                    self.max_constituent_age_days, bool):
                raise ContractError("FreshnessPolicy", "max_constituent_age_days",
                                    "expected int calendar days or None")
            if self.max_constituent_age_days < 0:
                raise ContractError(
                    "FreshnessPolicy", "max_constituent_age_days",
                    f"expected >= 0, got {self.max_constituent_age_days}")
        if self.on_failure not in ("RECORD_ONLY", "BLOCK_DECISION"):
            raise ContractError("FreshnessPolicy", "on_failure",
                                f"expected RECORD_ONLY|BLOCK_DECISION, got "
                                f"{self.on_failure!r}")

    def as_dict(self) -> dict:
        return {"max_age_days": self.max_age_days,
                "temporal_skew_days": self.temporal_skew_days,
                "max_constituent_age_days": self.max_constituent_age_days,
                "policy_id": self.policy_id, "approved_by": self.approved_by,
                "on_failure": self.on_failure, "rationale": self.rationale}


@dataclass(frozen=True)
class RegimeInputValidity:
    """The full validity record for one Regime evaluation (task §13)."""
    regime_as_of: str
    spy: InputRef
    breadth: InputRef
    policy: FreshnessPolicy
    state: str
    constituents: InputRef | None = None
    reasons: tuple = ()
    provenance: Provenance | None = None

    # ---- derived, required by §13 ---------------------------------------
    @property
    def spy_age_days(self) -> int | None:
        return self.spy.age_days

    @property
    def breadth_age_days(self) -> int | None:
        return self.breadth.age_days

    @property
    def constituent_age_days(self) -> int | None:
        return self.constituents.age_days if self.constituents is not None else None

    @property
    def spy_matches_as_of(self) -> bool | None:
        return self.spy.matches_as_of

    @property
    def breadth_matches_as_of(self) -> bool | None:
        return self.breadth.matches_as_of

    @property
    def constituents_valid(self) -> bool:
        """True when no constituent dependency is declared or the declared
        bound is satisfied. With `max_constituent_age_days=None` there is
        nothing to check, which is the historical-replay case."""
        if self.constituents is None:
            return True
        cap = self.policy.max_constituent_age_days
        if cap is None:
            return True
        age = self.constituents.age_days
        return age is not None and age <= cap

    @property
    def regime_input_temporal_consistent(self) -> bool:
        """True only when both inputs exist and their effective dates are within
        the policy's skew bound. Two individually-fresh inputs from very
        different dates are NOT consistent."""
        a, b = self.spy.age_days, self.breadth.age_days
        if a is None or b is None:
            return False
        return abs(a - b) <= self.policy.temporal_skew_days

    @property
    def regime_input_freshness_valid(self) -> bool:
        if self.spy_age_days is None or self.breadth_age_days is None:
            return False
        return (self.spy_age_days <= self.policy.max_age_days
                and self.breadth_age_days <= self.policy.max_age_days)

    @property
    def decision_trustworthy(self) -> bool:
        return self.state == VALIDITY_VALID

    def validate(self, where: str = "RegimeInputValidity") -> "RegimeInputValidity":
        require_session_date(self.regime_as_of, where, "regime_as_of",
                             "RegimeInputValidity")
        if self.state not in VALIDITY_STATES:
            raise ContractError("RegimeInputValidity", "state",
                                f"expected one of {sorted(VALIDITY_STATES)}, "
                                f"got {self.state!r}")
        for r in self.reasons:
            if r not in VALIDITY_REASON_CODES:
                raise ContractError("RegimeInputValidity", "reasons",
                                    f"unknown reason code {r!r}")
        if self.provenance is not None:
            self.provenance.validate(where, "RegimeInputValidity")
        return self

    def as_dict(self) -> dict:
        return {
            "regime_as_of": self.regime_as_of,
            "spy_tail_date": self.spy.tail_date,
            "breadth_tail_date": self.breadth.tail_date,
            # task §10 — flat provenance so a record answers "what was this
            # built from?" without walking the nested structure
            "breadth_date": self.breadth.tail_date,
            "ohlcv_tail_date": (self.breadth.ohlcv_tail_date
                                or self.spy.tail_date),
            "price_basis": self.spy.price_basis,
            "constituent_snapshot_date": (self.constituents.tail_date
                                          if self.constituents else None),
            "spy_age_days": self.spy_age_days,
            "breadth_age_days": self.breadth_age_days,
            "constituent_age_days": self.constituent_age_days,
            "spy_matches_as_of": self.spy_matches_as_of,
            "breadth_matches_as_of": self.breadth_matches_as_of,
            "constituents_valid": self.constituents_valid,
            "regime_input_temporal_consistent":
                self.regime_input_temporal_consistent,
            "regime_input_freshness_valid":
                self.regime_input_freshness_valid,
            "state": self.state,
            "reasons": list(self.reasons),
            "decision_trustworthy": self.decision_trustworthy,
            "policy": self.policy.as_dict(),
            "inputs": {
                "spy": self.spy.as_dict(),
                "breadth": self.breadth.as_dict(),
                "constituents": (self.constituents.as_dict()
                                 if self.constituents else None),
            },
            "provenance": (self.provenance.as_dict()
                           if self.provenance is not None else None),
        }


def assess(*, regime_as_of: str, spy_df, breadth_df,
           policy: FreshnessPolicy, constituent_snapshot: str | None = None,
           constituent_source: str = "fja05680/sp500",
           price_basis: str | None = None,
           breadth_ohlcv_tail: str | None = None) -> RegimeInputValidity:
    """Classify one Regime evaluation's input validity. Pure inspection.

    `spy_df` / `breadth_df` are the frames the caller is ABOUT TO use; this
    function never modifies them and never substitutes for them.

    `constituent_snapshot` is the DATE of the PIT membership snapshot that the
    breadth series was actually built from — NOT the breadth tail. Supplying it
    is what lets the caller distinguish "breadth is current" from "breadth was
    computed from a membership list that is no longer current". `None` disables
    the constituent check.

    `price_basis` (task §10) is recorded verbatim on every input. It is
    provenance only: two series with different bases are not comparable even
    when their dates align, so this must be reconstructible from the record
    rather than assumed from the loader that happened to run.
    """
    reasons: list[str] = []

    # Resolve BOTH frame shapes: OHLCV carries a `datetime` column, breadth a
    # DatetimeIndex. An undeterminable date is treated as MISSING, never as an
    # epoch.
    spy_tail = effective_date(spy_df)
    br_tail = effective_date(breadth_df)

    spy = InputRef("SPY", spy_tail, regime_as_of,
                   n_observations=len(spy_df) if _has_rows(spy_df) else 0,
                   source=str(getattr(spy_df, "attrs", {}).get("source", "cache"))
                   if _has_rows(spy_df) else "unavailable",
                   price_basis=price_basis)
    br = InputRef("breadth", br_tail, regime_as_of,
                  n_observations=len(breadth_df) if _has_rows(breadth_df) else 0,
                  source=str(getattr(breadth_df, "attrs", {}).get("source", "cache"))
                  if _has_rows(breadth_df) else "unavailable",
                  price_basis=price_basis,
                  ohlcv_tail_date=breadth_ohlcv_tail)
    const = (InputRef("constituents", constituent_snapshot, regime_as_of,
                      source=constituent_source)
             if constituent_snapshot is not None else None)

    # ---- 1. presence ---------------------------------------------------
    if br_tail is None:
        reasons.append(RSN_BREADTH_MISSING)
    if spy_tail is None:
        reasons.append(RSN_SPY_MISSING)
    if const is not None and const.tail_date is None:
        reasons.append(RSN_CONSTITUENTS_MISSING)

    # ---- 2. look-ahead (would be a PIT violation) ----------------------
    if br_tail is not None and br.is_future:
        reasons.append(RSN_BREADTH_FUTURE)
    if spy_tail is not None and spy.is_future:
        reasons.append(RSN_SPY_FUTURE)

    # ---- 3. freshness --------------------------------------------------
    if br_tail is not None and br.age_days is not None and \
            br.age_days > policy.max_age_days:
        reasons.append(RSN_BREADTH_STALE)
    if spy_tail is not None and spy.age_days is not None and \
            spy.age_days > policy.max_age_days:
        reasons.append(RSN_SPY_STALE)

    # Constituent membership is a DEPENDENCY of breadth: a snapshot older than
    # the policy bound means the recent breadth rows were computed from a
    # carried-forward membership guess, however fresh the OHLCV is.
    constituents_stale = False
    if const is not None and policy.max_constituent_age_days is not None:
        if const.tail_date is None:
            reasons.append(RSN_CONSTITUENTS_MISSING)
        elif const.age_days is not None and \
                const.age_days > policy.max_constituent_age_days:
            constituents_stale = True
            reasons.append(RSN_CONSTITUENTS_STALE)
            if br_tail is not None and const.age_days is not None and \
                    const.age_days >= br.age_days:
                reasons.append(RSN_CONSTITUENT_CARRY_FORWARD)

    # ---- 4. temporal consistency --------------------------------------
    temporally_consistent = (
        spy_tail is not None and br_tail is not None
        and abs(spy.age_days - br.age_days) <= policy.temporal_skew_days)

    # ---- 5. state ------------------------------------------------------
    missing = {RSN_BREADTH_MISSING, RSN_SPY_MISSING, RSN_CONSTITUENTS_MISSING}
    future = {RSN_BREADTH_FUTURE, RSN_SPY_FUTURE}
    stale = {RSN_BREADTH_STALE, RSN_SPY_STALE}

    if missing & set(reasons):
        state = VALIDITY_MISSING
    elif future & set(reasons):
        # a look-ahead is worse than staleness: the decision is not merely old,
        # it is not trustworthy at all
        state = VALIDITY_INVALID
    elif constituents_stale:
        # distinct from plain STALE: the OHLCV may be current, but the universe
        # the breadth was measured over is not
        state = VALIDITY_STALE_CONSTITUENTS
    elif stale & set(reasons):
        state = VALIDITY_STALE
    elif not temporally_consistent:
        state = VALIDITY_TEMPORALLY_INCONSISTENT
        reasons.append(RSN_BREADTH_SPY_DATE_MISMATCH)
    else:
        state = VALIDITY_VALID
        reasons.append(RSN_OK)

    return RegimeInputValidity(
        regime_as_of=regime_as_of, spy=spy, breadth=br, policy=policy,
        state=state, constituents=const, reasons=tuple(reasons),
        provenance=Provenance.engine(
            "production.data_validity", "regime_input_validity",
            session_date=regime_as_of, state=state, reasons=list(reasons)),
    ).validate()


# ---------------------------------------------------------------------------
# effective-date extraction
# ---------------------------------------------------------------------------
# The two Regime inputs arrive with DIFFERENT frame shapes:
#   * OHLCV  (SPY / HMM input) — RangeIndex + a `datetime` COLUMN
#   * breadth                   — DatetimeIndex
# Reading the wrong one silently yields 1970-01-01 (an integer RangeIndex
# interpreted as a POSIX timestamp) and would make a current input look 20,000+
# days stale. `effective_date` therefore resolves BOTH shapes and refuses to
# guess.
_DATE_COLUMNS = ("datetime", "date", "index", "timestamp")


def effective_date(df) -> str | None:
    """The frame's effective (last observation) date as 'YYYY-MM-DD'.

    Resolution order:
      1. a DatetimeIndex / date-like index
      2. a recognised date COLUMN
      3. None — never a guess

    A frame whose index is a plain RangeIndex AND which carries no date column
    returns None rather than fabricating an epoch date.
    """
    if not _has_rows(df):
        return None
    import pandas as pd

    idx = getattr(df, "index", None)
    if idx is not None and not isinstance(idx, pd.RangeIndex):
        try:
            if pd.api.types.is_datetime64_any_dtype(idx):
                return pd.Timestamp(idx.max()).strftime("%Y-%m-%d")
        except Exception:
            pass
    for col in _DATE_COLUMNS:
        if col in getattr(df, "columns", ()):
            try:
                s = pd.to_datetime(df[col], errors="coerce").dropna()
                if len(s):
                    return pd.Timestamp(s.max()).strftime("%Y-%m-%d")
            except Exception:
                continue
    return None


def pd_index_max(df) -> str:
    """Backwards-compatible alias. Raises if the effective date is undeterminable,
    so a caller can never treat 'unknown' as a real date."""
    d = effective_date(df)
    if d is None:
        raise ValueError(
            "cannot determine the effective date: the frame has neither a "
            "datetime-like index nor a recognised date column")
    return d


def _has_rows(df) -> bool:
    try:
        return df is not None and len(df) > 0
    except Exception:
        return False


def human_report(v: RegimeInputValidity) -> str:
    """The operator-facing block (task §15). States the problem; never invents
    an answer and never presents an invalid decision as valid."""
    L = [f"Regime input validity : {v.state}"]
    if v.state != VALIDITY_VALID:
        L.append(f"  reason              : {', '.join(v.reasons)}")
    L.append(f"  regime_as_of        : {v.regime_as_of}")
    L.append(f"  SPY tail            : {v.spy.tail_date} "
             f"(age {v.spy_age_days}d)")
    L.append(f"  Breadth tail        : {v.breadth.tail_date} "
             f"(age {v.breadth_age_days}d)")
    if v.constituents is not None:
        L.append(f"  Constituent snapshot: {v.constituents.tail_date} "
                 f"(age {v.constituent_age_days}d, valid="
                 f"{v.constituents_valid})")
    # task §10 — a record must be able to answer "what was this built from?"
    if v.breadth.ohlcv_tail_date:
        L.append(f"  Breadth OHLCV tail  : {v.breadth.ohlcv_tail_date}")
    L.append(f"  Price basis         : {v.spy.price_basis or 'unrecorded'}")
    L.append(f"  temporally_consistent: {v.regime_input_temporal_consistent}")
    L.append(f"  freshness_valid     : {v.regime_input_freshness_valid}")
    L.append(f"  decision_trustworthy: {v.decision_trustworthy}")
    return "\n".join(L)
