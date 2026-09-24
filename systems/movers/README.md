# Bybit breakout research

Run `node run-backtest.mjs --days 730`. It discovers current USDT linear perpetuals, caches daily candles, selects a breakout rule using only each symbol's first 60% of data, and evaluates the final 40% net of configured costs. Outputs are in `output/`.

Results are subject to current-universe survivorship bias and are research only.
