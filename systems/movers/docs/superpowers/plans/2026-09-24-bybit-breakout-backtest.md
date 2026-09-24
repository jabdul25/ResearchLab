# Bybit 24-Hour Breakout Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, net-of-cost, out-of-sample long breakout backtest and current Bybit watchlist.

**Architecture:** A dependency-free Node.js CLI discovers the live linear-perpetual universe, caches public Bybit daily candles and OI, constructs point-in-time feature rows, chooses a small signal grid on development-only rows, and evaluates the frozen rule in holdout. It emits row-level trades, a machine summary, and a readable report.

**Tech Stack:** Node.js 22+, native `fetch`, `node:test`, JSON/CSV/Markdown outputs.

**Spec:** `docs/superpowers/specs/2026-09-24-bybit-breakout-edge-design.md`

## Global Constraints

- Use completed UTC daily candles only; entry is next open and exit is following open.
- Deduct default 5.5 bps fee and 10 bps slippage per side.
- Discover only `Trading`, USDT-settled linear perpetuals; disclose current-universe survivorship limitation.
- Cache raw public data; retry bounded 429/5xx failures and continue per-symbol failures.
- No edge claim unless holdout passes every condition stated in the spec.

## Review Focus

- A signal must not read entry or exit candle data while being generated.
- Final incomplete daily candle must never appear in a feature or trade row.
- A missing OI observation must not meet the OI-confirmed condition.
- Candidate selection must use only development rows even when symbols list at different times.
- Costs must be applied to both entry and exit, with slippage adverse to a long trade.

---

### Task 1: Pure data and feature primitives

**Files:**
- Create: `src/core.mjs`
- Create: `test/core.test.mjs`

**Interfaces:**
- Produces `completedCandles(rows, nowMs)`, `featureRows(symbol, candles, oiByDay)`, `netLongReturn(entry, exit, feeBps, slippageBps)`, and `quantile(values, q)`.

- [ ] **Step 1: Write failing anti-leakage and cost tests**

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { completedCandles, netLongReturn } from '../src/core.mjs';
test('removes current unfinished candle', () => assert.equal(completedCandles([{t:0},{t:86_400_000}], 100_000).length, 1));
test('deducts costs twice from long return', () => assert.ok(netLongReturn(100, 101, 5.5, 10) < 0.01));
```

- [ ] **Step 2: Run `node --test test/core.test.mjs` and verify it fails because the module is absent.**
- [ ] **Step 3: Implement the named pure functions with prior-window-only calculations.**
- [ ] **Step 4: Add tests for 10-day breakout, absent OI, and next-two-open availability; run `node --test test/core.test.mjs` and verify PASS.**
- [ ] **Step 5: Commit `src/core.mjs` and `test/core.test.mjs` with `feat: add point-in-time breakout features`.**

### Task 2: Bybit client, cache, and universe coverage

**Files:**
- Create: `src/bybit.mjs`
- Create: `test/bybit.test.mjs`

**Interfaces:**
- Consumes `fetch` and cache directory.
- Produces `discoverUniverse()`, `getDailyCandles(symbol, days)`, `getDailyOi(symbol, days)`, each returning normalized arrays or a recorded error.

- [ ] **Step 1: Write failing tests with mocked fetch for paginated instruments, reverse-sorted kline input, and cache hit bypassing fetch.**
- [ ] **Step 2: Run `node --test test/bybit.test.mjs` and verify module-not-found failure.**
- [ ] **Step 3: Implement request retry, JSON cache keying, instrument pagination, and response normalization.**
- [ ] **Step 4: Run `node --test test/bybit.test.mjs` and verify PASS.**
- [ ] **Step 5: Commit `src/bybit.mjs` and `test/bybit.test.mjs` with `feat: add cached Bybit public data client`.**

### Task 3: Walk-forward selection and reporting

**Files:**
- Create: `src/backtest.mjs`
- Create: `src/report.mjs`
- Create: `test/backtest.test.mjs`

**Interfaces:**
- Consumes normalized rows and config.
- Produces `{selectedRule, development, holdout, trades, watchlist}` and CSV/JSON/Markdown writers.

- [ ] **Step 1: Write failing tests proving a profitable holdout cannot select a rule when development is unprofitable, and that OI-null rows cannot pass OI rules.**
- [ ] **Step 2: Run `node --test test/backtest.test.mjs` and verify module-not-found failure.**
- [ ] **Step 3: Implement predeclared rule grid, 60/40 per-symbol chronological split, development selection, frozen holdout evaluation, bootstrap mean interval, baselines, concentration checks, and report writers.**
- [ ] **Step 4: Add a test that report eligibility is false when fewer than 100 holdout trades exist; run `node --test test/backtest.test.mjs` and verify PASS.**
- [ ] **Step 5: Commit `src/backtest.mjs`, `src/report.mjs`, and tests with `feat: add walk-forward evaluation and reports`.**

### Task 4: CLI and full-universe run

**Files:**
- Create: `run-backtest.mjs`
- Create: `README.md`
- Create: `output/.gitkeep`

**Interfaces:**
- Consumes `node run-backtest.mjs [--days N] [--refresh] [--fee-bps N] [--slippage-bps N]`.
- Produces `output/trades.csv`, `output/summary.json`, `output/report.md`.

- [ ] **Step 1: Write a CLI parsing test for default and overridden fee/slippage values.**
- [ ] **Step 2: Run the relevant `node --test` command and verify it fails before CLI implementation.**
- [ ] **Step 3: Implement progress output, per-symbol error ledger, data download concurrency limit, output creation, and README invocation.**
- [ ] **Step 4: Run all unit tests with `node --test`; run a 10-symbol smoke test; then run the complete discovered universe with 730 days of history.**
- [ ] **Step 5: Inspect outputs for coverage, missing data, leakage fields, and OOS eligibility; commit source/docs but do not commit bulk raw cache.**
