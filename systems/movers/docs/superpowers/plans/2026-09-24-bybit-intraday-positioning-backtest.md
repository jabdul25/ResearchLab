# Bybit Intraday Positioning Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test 1-hour positioning-confirmed breakouts held for 24 hours across Bybit crypto perpetuals.

**Architecture:** Extend the public-data client with paginated 1h candles, OI, funding, and long/short ratio feeds. Normalize to point-in-time hourly rows, select a predeclared rule grid on development data only, and report frozen holdout outcomes separately from the daily study.

**Tech Stack:** Node.js native fetch and node:test.

**Spec:** `docs/superpowers/specs/2026-09-24-bybit-intraday-positioning-design.md`

## Global Constraints

- Enter one hour after the signal close; exit 24 hours later; costs apply twice.
- Restrict to crypto contracts and state all exclusions.
- Missing OI, funding, or account ratio cannot pass a rule requiring it.
- Use paginated historical responses and cached raw results.
- Select only on 60% development data; evaluate a frozen choice on 40% holdout data.

## Review Focus

- Timestamp alignment across hourly feeds.
- Pagination gaps and API error ledger.
- No future hourly candle leakage into features or exit.
- Correct 24-hour exit index.
- Separate daily and intraday outputs.

### Task 1: Intraday source normalization

**Files:** `src/bybit.mjs`, `test/bybit.test.mjs`

- [ ] Test hourly kline, paginated OI, funding, and ratio normalizers with reverse-order API payloads.
- [ ] Run tests red, implement cache/pagination/normalization, run green, commit.

### Task 2: Point-in-time intraday features

**Files:** `src/intraday.mjs`, `test/intraday.test.mjs`

- [ ] Test prior-six-hour-only range/turnover/OI features and `t+1`→`t+25` trade timing.
- [ ] Run red, implement, run green, commit.

### Task 3: Frozen-rule evaluator and outputs

**Files:** `src/intraday-backtest.mjs`, `run-intraday-backtest.mjs`, `test/intraday-backtest.test.mjs`

- [ ] Test no selection from holdout and missing-data rejection.
- [ ] Run red, implement rule grid, metrics, concentration/month criteria, separate reports, run green.
- [ ] Execute a small smoke universe, then the complete discovered crypto universe; inspect coverage and commit source/report.
