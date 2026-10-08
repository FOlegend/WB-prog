"""
reason_codes.py — controlled vocabulary for contract reason codes (Phase 0)

A reason code answers "why was this decision produced?" — not "what is the
number". Codes are plain strings backed by frozensets so validation is
deterministic and serialisation stays trivial.

Keep the vocabulary small. Add a code only when it expresses a NEW decision
reason (architecture decision 9).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Directions (architecture decision 4 — SHORT is mirrored, not enabled)
# ---------------------------------------------------------------------------
LONG = "LONG"
SHORT = "SHORT"
DIRECTIONS = frozenset({LONG, SHORT})

SIDE_LONG = 1
SIDE_SHORT = -1


def direction_sign(direction: str) -> int:
    """+1 for LONG, -1 for SHORT. Caller must have validated `direction`."""
    return SIDE_LONG if direction == LONG else SIDE_SHORT


# ---------------------------------------------------------------------------
# Stop Engine — calculation / state reasons
# ---------------------------------------------------------------------------
INITIAL_ATR = "INITIAL_ATR"                    # initial stop from ATR
TRAILING_ATR = "TRAILING_ATR"                  # candidate improved the stop
NEW_HIGH = "NEW_HIGH"                          # improvement caused by a new anchor extreme
STOP_UNCHANGED = "STOP_UNCHANGED"              # armed evaluation produced no improvement
TRAILING_NOT_ARMED = "TRAILING_NOT_ARMED"      # trigger distance not reached yet

# Reserved for a future structure-stop architecture decision (NOT implemented).
STRUCTURE_STOP = "STRUCTURE_STOP"
STRUCTURE_BREAK = "STRUCTURE_BREAK"
STRUCTURE_LEVEL_MISSING = "STRUCTURE_LEVEL_MISSING"

STOP_CALC_REASON_CODES = frozenset({
    INITIAL_ATR, TRAILING_ATR, NEW_HIGH, STOP_UNCHANGED, TRAILING_NOT_ARMED,
})

STOP_REASON_CODES = frozenset(STOP_CALC_REASON_CODES | {
    STRUCTURE_STOP, STRUCTURE_BREAK, STRUCTURE_LEVEL_MISSING,
})

# ---------------------------------------------------------------------------
# Stop Engine — error / validation reasons
# ---------------------------------------------------------------------------
ATR_UNAVAILABLE = "ATR_UNAVAILABLE"
INVALID_ATR = "INVALID_ATR"
INVALID_MULTIPLE = "INVALID_MULTIPLE"
INVALID_PRICE = "INVALID_PRICE"
INVALID_DIRECTION = "INVALID_DIRECTION"

STOP_ERROR_CODES = frozenset({
    ATR_UNAVAILABLE, INVALID_ATR, INVALID_MULTIPLE, INVALID_PRICE,
    INVALID_DIRECTION,
})

# ---------------------------------------------------------------------------
# Risk Decision (Phase 0: vocabulary reserved; engine arrives in a later phase)
# ---------------------------------------------------------------------------
RISK_APPROVED = "RISK_APPROVED"
RISK_RESIZED = "RISK_RESIZED"
RISK_REJECTED = "RISK_REJECTED"
RISK_UNSPECIFIED = "UNSPECIFIED"               # placeholder until the Risk Engine phase

RISK_REASON_CODES = frozenset({
    RISK_APPROVED, RISK_RESIZED, RISK_REJECTED, RISK_UNSPECIFIED,
})

RISK_STATUS_APPROVE = "APPROVE"
RISK_STATUS_RESIZE = "RESIZE"
RISK_STATUS_REJECT = "REJECT"
RISK_STATUSES = frozenset({
    RISK_STATUS_APPROVE, RISK_STATUS_RESIZE, RISK_STATUS_REJECT,
})

# ---------------------------------------------------------------------------
# Order Intent
# ---------------------------------------------------------------------------
ORDER_ENTRY_APPROVED = "ENTRY_APPROVED"
ORDER_ENTRY_RESIZED = "ENTRY_RESIZED"
ORDER_UNSPECIFIED = "UNSPECIFIED"

ORDER_REASON_CODES = frozenset({
    ORDER_ENTRY_APPROVED, ORDER_ENTRY_RESIZED, ORDER_UNSPECIFIED,
})

# ---------------------------------------------------------------------------
# Execution conventions (frozen Setup v1 convention = next-open)
# ---------------------------------------------------------------------------
NEXT_OPEN = "NEXT_OPEN"
EXECUTION_TYPES = frozenset({NEXT_OPEN})

# Reference-price sources (architecture decision 10 — never conflate these)
REFERENCE_SOURCE_FILL = "FILL"          # actual fill price (real initial risk)
REFERENCE_SOURCE_EXPECTED = "EXPECTED"  # pre-trade expected/reference price
REFERENCE_SOURCE_SIGNAL = "SIGNAL"      # signal-bar close (D session close)
REFERENCE_PRICE_SOURCES = frozenset({
    REFERENCE_SOURCE_FILL, REFERENCE_SOURCE_EXPECTED, REFERENCE_SOURCE_SIGNAL,
})

# Stop methods (Phase 1 supports ATR only)
STOP_METHOD_ATR = "ATR"
STOP_METHODS = frozenset({STOP_METHOD_ATR})

# ---------------------------------------------------------------------------
# Exit Engine (Phase 2) — LEGACY-COMPATIBLE vocabulary
# ---------------------------------------------------------------------------
# Reason codes are deliberately identical to the legacy strings so that ledger /
# backtest reconciliation needs no mapping table (architecture decision 2).
EXIT_STOP_LOSS = "STOP_LOSS"
EXIT_TAKE_PROFIT = "TAKE_PROFIT"
EXIT_TRAILING_STOP = "TRAILING_STOP"
EXIT_TIME_STOP = "TIME_STOP"
EXIT_SIGNAL_EXIT = "SIGNAL_EXIT"

EXIT_REASON_CODES = frozenset({
    EXIT_STOP_LOSS, EXIT_TAKE_PROFIT, EXIT_TRAILING_STOP,
    EXIT_TIME_STOP, EXIT_SIGNAL_EXIT,
})

# Fill models are ALSO the legacy vocabulary (architecture decision 4):
#   GAP      -> filled at the bar open (gap through the level)
#   STOP     -> filled at the stop level (intraday breach)
#   TARGET   -> filled at the target level
#   TRAILING -> filled at the trailing stop level (intraday breach)
#   CLOSE    -> filled at the bar close (end-of-day decisions)
FILL_GAP = "GAP"
FILL_STOP = "STOP"
FILL_TARGET = "TARGET"
FILL_TRAILING = "TRAILING"
FILL_CLOSE = "CLOSE"

FILL_MODELS = frozenset({FILL_GAP, FILL_STOP, FILL_TARGET, FILL_TRAILING,
                         FILL_CLOSE})

# which fill models each exit reason may use (mirrors the legacy if/else paths)
EXIT_REASON_FILL_MODELS = {
    EXIT_STOP_LOSS: frozenset({FILL_GAP, FILL_STOP}),
    EXIT_TAKE_PROFIT: frozenset({FILL_TARGET}),
    EXIT_TRAILING_STOP: frozenset({FILL_GAP, FILL_TRAILING}),
    EXIT_TIME_STOP: frozenset({FILL_CLOSE}),
    EXIT_SIGNAL_EXIT: frozenset({FILL_CLOSE}),
}

# technical signal values (injected by the caller — never computed here)
TECH_SIGNAL_BEARISH = "bearish"
TECH_SIGNAL_NEUTRAL = "neutral"
TECH_SIGNAL_BULLISH = "bullish"
TECH_SIGNALS = frozenset({TECH_SIGNAL_BEARISH, TECH_SIGNAL_NEUTRAL,
                          TECH_SIGNAL_BULLISH})

# ---------------------------------------------------------------------------
# Exit-engine wiring modes (Phase 3 — the ONLY new production wiring flag)
# ---------------------------------------------------------------------------
# legacy : the legacy decision is the only source of truth (DEFAULT)
# shadow : legacy still decides; the new engine is evaluated and recorded only
# new    : the new engine's ExitDecision drives the production action
EXIT_ENGINE_LEGACY = "legacy"
EXIT_ENGINE_SHADOW = "shadow"
EXIT_ENGINE_NEW = "new"

EXIT_ENGINE_MODES = frozenset({EXIT_ENGINE_LEGACY, EXIT_ENGINE_SHADOW,
                               EXIT_ENGINE_NEW})

# ---------------------------------------------------------------------------
# Shadow comparison — divergence classification (Phase 3)
# ---------------------------------------------------------------------------
# A divergence is NEVER classified as harmless automatically. Anything the
# comparator cannot explain is reported as-is and flagged for human review.
DIV_SHOULD_EXIT_MISMATCH = "SHOULD_EXIT_MISMATCH"
DIV_REASON_MISMATCH = "REASON_MISMATCH"
DIV_PRICE_MISMATCH = "PRICE_MISMATCH"
DIV_FILL_MODEL_MISMATCH = "FILL_MODEL_MISMATCH"
DIV_STOP_REFERENCE_MISMATCH = "STOP_REFERENCE_MISMATCH"
DIV_TARGET_REFERENCE_MISMATCH = "TARGET_REFERENCE_MISMATCH"
DIV_INPUT_VALIDATION_MISMATCH = "INPUT_VALIDATION_MISMATCH"
DIV_ENGINE_EXCEPTION = "ENGINE_EXCEPTION"

DIVERGENCE_TYPES = frozenset({
    DIV_SHOULD_EXIT_MISMATCH, DIV_REASON_MISMATCH, DIV_PRICE_MISMATCH,
    DIV_FILL_MODEL_MISMATCH, DIV_STOP_REFERENCE_MISMATCH,
    DIV_TARGET_REFERENCE_MISMATCH, DIV_INPUT_VALIDATION_MISMATCH,
    DIV_ENGINE_EXCEPTION,
})

# shadow record outcome status
SHADOW_AGREED = "AGREED"
SHADOW_DIVERGED = "DIVERGED"
SHADOW_ERROR = "ERROR"
