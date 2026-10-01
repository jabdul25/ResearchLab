class CandidateSetup:
    """Produces a descriptive state only. It deliberately contains no execution rules."""
    LABELS = {
        "ema20_above_ema50": "4H directional strength (EMA20 above EMA50)",
        "compression": "15m/1h controlled pullback or compression",
        "higher_low": "buyer defence via a higher pullback low",
        "momentum_repair": "momentum repair",
        "volume_expansion": "participation / volume expansion",
        "oi_expansion": "open-interest expansion",
        "structure_confirmation": "structure confirmation",
    }
    def generate(self, validation, importance):
        surviving = validation.get("surviving_features", [])
        return {"name": "Expansion Continuation State", "conditions": [self.LABELS[x] for x in surviving],
                "surviving_features": surviving, "feature_importance": importance,
                "status": "RESEARCH_ONLY"}
