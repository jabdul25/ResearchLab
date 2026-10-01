from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ValidatedMarketState:
    name: str
    conditions: tuple[str, ...]
    surviving_features: tuple[str, ...]
    status: str = "RESEARCH_ONLY"
    interpretation: str = "Historically associated market conditions; no causal claim."

    @classmethod
    def from_validation(cls, validation: dict) -> "ValidatedMarketState":
        surviving = tuple(validation.get("surviving_features") or ())
        definitions = validation.get("train", {}).get("definitions", {})
        conditions = tuple(f"{feature}: {definitions[feature]['direction']} among winners" for feature in surviving)
        return cls("Expansion Continuation State", conditions, surviving)

    def as_dict(self) -> dict:
        result = asdict(self)
        result["conditions"] = list(self.conditions)
        result["surviving_features"] = list(self.surviving_features)
        return result


class CandidateSetup:
    """Compatibility facade returning the descriptive research-only state."""

    def generate(self, validation: dict, importance: list[dict]) -> dict:
        result = ValidatedMarketState.from_validation(validation).as_dict()
        result["feature_importance"] = importance
        return result
