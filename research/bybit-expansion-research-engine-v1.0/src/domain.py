from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping


class CandidateStatus(str, Enum):
    ADMITTED = "admitted"
    REJECTED_DUPLICATE = "rejected_duplicate"


class OutcomeClass(str, Enum):
    WINNER = "winner"
    REVERSAL = "reversal"
    FAILED_BREAKOUT = "failed_breakout"
    WEAK_EXPANSION = "weak_expansion"


class Regime(str, Enum):
    HIGH_VOLATILITY = "high_volatility"
    BULLISH_TREND = "bullish_trend"
    BEARISH_TREND = "bearish_trend"
    RANGE = "range"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BreakoutSignal:
    symbol: str
    interval_minutes: int
    breakout_open_time: datetime
    decision_time: datetime
    structure_lookback: int
    breakout_level: float
    breakout_close: float
    status: CandidateStatus
    duplicate_of_key: str | None = None
    rejection_reason: str | None = None
    duplicate_window_end: datetime | None = None

    @property
    def key(self) -> str:
        return (
            f"{self.symbol}:{self.interval_minutes}:"
            f"{self.breakout_open_time.isoformat()}:{self.structure_lookback}"
        )


@dataclass(frozen=True)
class LiquiditySnapshot:
    decision_time: datetime
    volume_24h: float
    turnover_24h: float
    expected_bars: int
    observed_bars: int
    eligible: bool
    reason: str | None = None


@dataclass(frozen=True)
class CandidateOutcome:
    window_start: datetime
    window_end: datetime
    observed_candles: int
    classification: OutcomeClass
    threshold: float
    threshold_reached: bool
    mfe: float
    mae: float
    maximum_extension: float
    final_vs_breakout_level: float
    giveback_fraction: float | None


@dataclass(frozen=True)
class TimelineSnapshot:
    horizon: str
    as_of: datetime
    features: Mapping[str, object]
    source_max_timestamps: Mapping[str, datetime | None]


@dataclass(frozen=True)
class FeatureSnapshot:
    decision_time: datetime
    regime: Regime
    features: Mapping[str, object]
    timeline: Mapping[str, TimelineSnapshot]
    source_max_timestamps: Mapping[str, datetime | None]
    input_fingerprint: str


@dataclass(frozen=True)
class SchemaProvenance:
    schema_version: str
    checksums: Mapping[str, str] = field(default_factory=dict)
