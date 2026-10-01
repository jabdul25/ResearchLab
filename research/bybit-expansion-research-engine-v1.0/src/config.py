from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str
    minimum_turnover_24h: float = 500_000.0
    history_months: int = 12
    reports_dir: Path = Path("reports")
    request_pause_seconds: float = 0.08
    request_retries: int = 5
    structure_lookback: int = 20
    outcome_window_candles: int = 12
    duplicate_window_candles: int = 12
    reversal_min_threshold_fraction: float = 0.50
    reversal_giveback_fraction: float = 0.70
    liquidity_source_interval_minutes: int = 15
    expansion_thresholds: dict[int, float] = field(
        default_factory=lambda: {15: 0.08, 60: 0.15, 240: 0.25}
    )
    ema_slope_lookback_bars: int = 6
    volatility_percentile_lookback_bars: int = 90
    high_volatility_percentile: float = 0.80
    minimum_winners_per_split: int = 20
    minimum_failures_per_split: int = 20

    def __post_init__(self) -> None:
        if not self.database_url:
            raise ValueError("database_url is required")
        positive = {
            "minimum_turnover_24h": self.minimum_turnover_24h,
            "history_months": self.history_months,
            "request_retries": self.request_retries,
            "structure_lookback": self.structure_lookback,
            "outcome_window_candles": self.outcome_window_candles,
            "duplicate_window_candles": self.duplicate_window_candles,
            "ema_slope_lookback_bars": self.ema_slope_lookback_bars,
            "volatility_percentile_lookback_bars": self.volatility_percentile_lookback_bars,
            "minimum_winners_per_split": self.minimum_winners_per_split,
            "minimum_failures_per_split": self.minimum_failures_per_split,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if set(self.expansion_thresholds) != {15, 60, 240}:
            raise ValueError("expansion_thresholds must define 15, 60, and 240 minutes")
        if any(value <= 0 for value in self.expansion_thresholds.values()):
            raise ValueError("expansion_thresholds must be positive")
        for name, value in {
            "reversal_min_threshold_fraction": self.reversal_min_threshold_fraction,
            "reversal_giveback_fraction": self.reversal_giveback_fraction,
            "high_volatility_percentile": self.high_volatility_percentile,
        }.items():
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1]")

    @property
    def min_turnover_24h(self) -> float:
        """Compatibility alias for the release-candidate collector."""
        return self.minimum_turnover_24h

    def with_overrides(self, **changes: Any) -> "Settings":
        return replace(self, **changes)

    def as_manifest(self) -> dict[str, object]:
        return {
            "market": {"exchange": "bybit", "category": "linear", "quote": "USDT"},
            "collection": {
                "history_months": self.history_months,
                "request_pause_seconds": self.request_pause_seconds,
                "request_retries": self.request_retries,
                "intervals_minutes": [15, 60, 240],
            },
            "candidates": {
                "structure_lookback": self.structure_lookback,
                "outcome_window_candles": self.outcome_window_candles,
                "duplicate_window_candles": self.duplicate_window_candles,
                "reversal_min_threshold_fraction": self.reversal_min_threshold_fraction,
                "reversal_giveback_fraction": self.reversal_giveback_fraction,
            },
            "liquidity": {
                "minimum_turnover_24h": self.minimum_turnover_24h,
                "source_interval_minutes": self.liquidity_source_interval_minutes,
            },
            "events": {
                "thresholds": {
                    str(interval): threshold
                    for interval, threshold in sorted(self.expansion_thresholds.items())
                }
            },
            "features": {
                "ema_slope_lookback_bars": self.ema_slope_lookback_bars,
                "volatility_percentile_lookback_bars": self.volatility_percentile_lookback_bars,
                "high_volatility_percentile": self.high_volatility_percentile,
                "moving_average": "EMA",
            },
            "analysis": {
                "minimum_winners_per_split": self.minimum_winners_per_split,
                "minimum_failures_per_split": self.minimum_failures_per_split,
                "walk_forward_months": [6, 3, 3],
            },
        }

    def configuration_hash(self) -> str:
        payload = json.dumps(self.as_manifest(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def load(cls, config_path: str = "config/system.yaml") -> "Settings":
        load_dotenv()
        path = Path(config_path)
        data = yaml.safe_load(path.read_text()) if path.exists() else {}
        data = data or {}
        url = os.getenv("DATABASE_URL", data.get("database_url", ""))
        if not url:
            raise RuntimeError("DATABASE_URL is required; add it to .env")
        collection = data.get("collection", {})
        candidates = data.get("candidates", {})
        liquidity = data.get("liquidity", {})
        features = data.get("features", {})
        analysis = data.get("analysis", {})
        raw_thresholds = data.get("events", {}).get(
            "thresholds", {15: 0.08, 60: 0.15, 240: 0.25}
        )
        thresholds = {int(key): float(value) for key, value in raw_thresholds.items()}
        return cls(
            database_url=url,
            minimum_turnover_24h=float(
                liquidity.get(
                    "minimum_turnover_24h",
                    data.get("filters", {}).get("min_turnover_24h", 500_000),
                )
            ),
            history_months=int(collection.get("history_months", 12)),
            reports_dir=Path(data.get("reports", {}).get("directory", "reports")),
            request_pause_seconds=float(collection.get("request_pause_seconds", 0.08)),
            request_retries=int(collection.get("request_retries", 5)),
            structure_lookback=int(candidates.get("structure_lookback", 20)),
            outcome_window_candles=int(candidates.get("outcome_window_candles", 12)),
            duplicate_window_candles=int(candidates.get("duplicate_window_candles", 12)),
            reversal_min_threshold_fraction=float(
                candidates.get("reversal_min_threshold_fraction", 0.50)
            ),
            reversal_giveback_fraction=float(
                candidates.get("reversal_giveback_fraction", 0.70)
            ),
            liquidity_source_interval_minutes=int(
                liquidity.get("source_interval_minutes", 15)
            ),
            expansion_thresholds=thresholds,
            ema_slope_lookback_bars=int(features.get("ema_slope_lookback_bars", 6)),
            volatility_percentile_lookback_bars=int(
                features.get("volatility_percentile_lookback_bars", 90)
            ),
            high_volatility_percentile=float(
                features.get("high_volatility_percentile", 0.80)
            ),
            minimum_winners_per_split=int(
                analysis.get("minimum_winners_per_split", 20)
            ),
            minimum_failures_per_split=int(
                analysis.get("minimum_failures_per_split", 20)
            ),
        )
