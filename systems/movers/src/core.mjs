export const DAY_MS = 86_400_000;

export function completedCandles(rows, nowMs) {
  return rows.filter((row) => Number.isFinite(row.t) && row.t + DAY_MS <= nowMs);
}

export function quantile(values, q) {
  const valid = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!valid.length) return null;
  const position = (valid.length - 1) * Math.min(1, Math.max(0, q));
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  return valid[lower] + (valid[upper] - valid[lower]) * (position - lower);
}

export function netLongReturn(entryOpen, exitOpen, feeBps, slippageBps) {
  const sideCost = (feeBps + slippageBps) / 10_000;
  return (exitOpen * (1 - sideCost)) / (entryOpen * (1 + sideCost)) - 1;
}

export function featureRows(symbol, candles, oiByDay = new Map()) {
  const rows = [];
  for (let i = 20; i + 2 < candles.length; i += 1) {
    const prior10 = candles.slice(i - 10, i);
    const prior20 = candles.slice(i - 20, i);
    const current = candles[i];
    const entry = candles[i + 1];
    const exit = candles[i + 2];
    if (![current, entry, exit].every((c) => Number.isFinite(c.o) && Number.isFinite(c.c) && Number.isFinite(c.h) && Number.isFinite(c.l) && Number.isFinite(c.v))) continue;
    const high10 = Math.max(...prior10.map((c) => c.h));
    const high20 = Math.max(...prior20.map((c) => c.h));
    const low10 = Math.min(...prior10.map((c) => c.l));
    const medianTurnover10 = quantile(prior10.map((c) => c.v), 0.5);
    const oiNow = oiByDay.get(current.t);
    const oiTwoDaysAgo = oiByDay.get(candles[i - 2].t);
    rows.push({
      symbol, signalTime: current.t, entryTime: entry.t, exitTime: exit.t,
      entryOpen: entry.o, exitOpen: exit.o, close: current.c,
      priorHigh10: high10, priorHigh20: high20,
      compression10: Math.max(...prior10.map((c) => c.h)) / low10 - 1,
      turnoverMultiple10: medianTurnover10 > 0 ? current.v / medianTurnover10 : null,
      trend5: current.c / candles[i - 5].c - 1,
      oiChange2d: Number.isFinite(oiNow) && Number.isFinite(oiTwoDaysAgo) && oiTwoDaysAgo > 0 ? oiNow / oiTwoDaysAgo - 1 : null,
      isBreakout10: current.c > high10,
      isBreakout20: current.c > high20,
    });
  }
  return rows;
}
