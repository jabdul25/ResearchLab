from __future__ import annotations
import pandas as pd

from src.analysis.candidates import admit_candidates, detect_breakout_signals

EXPANSION_THRESHOLDS = {15: .08, 60: .15, 240: .25}

def discover_events(frame: pd.DataFrame, interval: int) -> list[dict]:
    """Find non-overlapping upward expansions and comparable failed breakouts.

    A move is measured from the starting close to the highest high in the next
    twelve candles.  After an event, its peak window is skipped so a single pump
    cannot be counted many times. Failures reached half the required move, failed
    to qualify, and closed back below the breakout start by the end of the window.
    """
    if len(frame) < 13: return []
    threshold, rows, i = EXPANSION_THRESHOLDS[interval], [], 0
    frame = frame.sort_values("open_time").reset_index(drop=True)
    while i <= len(frame) - 13:
        window = frame.iloc[i + 1:i + 13]
        start_close, peak_idx = float(frame.at[i, "close"]), window["high"].astype(float).idxmax()
        peak = float(frame.at[peak_idx, "high"])
        move, duration = peak / start_close - 1, int(peak_idx - i)
        if move >= threshold:
            classification = "major" if move >= .50 else "large" if move >= .25 else "normal"
            rows.append(dict(event_kind="expansion", start_time=frame.at[i, "open_time"], peak_time=frame.at[peak_idx, "open_time"],
                             percentage_move=move, duration_candles=duration, classification=classification))
            i += max(1, duration)
        elif move >= threshold / 2 and float(window.iloc[-1]["close"]) <= start_close:
            rows.append(dict(event_kind="failure", start_time=frame.at[i, "open_time"], peak_time=frame.at[peak_idx, "open_time"],
                             percentage_move=move, duration_candles=duration, classification="failure"))
            i += 1
        else: i += 1
    return rows


def discover_breakout_candidates(frame, symbol, interval, settings):
    """Candidate-first discovery used by the research pipeline.

    ``discover_events`` remains only as a compatibility facade for the
    inherited release-candidate tests; new orchestration must use this function.
    """
    raw = detect_breakout_signals(frame, symbol, interval, settings)
    return admit_candidates(raw, settings.outcome_window_candles)
