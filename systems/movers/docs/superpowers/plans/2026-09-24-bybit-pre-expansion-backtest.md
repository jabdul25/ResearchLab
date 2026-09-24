# Bybit Pre-Expansion Pump Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test 15-minute pre-expansion accumulation and first-breakout entries across Bybit crypto perpetuals.

**Architecture:** Add 15-minute candle/OI client methods, create point-in-time feature rows with a 96-candle future exit, and write a standalone walk-forward runner and outputs.

**Spec:** `docs/superpowers/specs/2026-09-24-bybit-pre-expansion-design.md`

### Task 1: Test-first 15-minute feature layer

- [ ] Add tests that prove all range and OI inputs are prior to the signal and exit is exactly 96 candles ahead.
- [ ] Implement `preExpansionRows` with compression, proximity, turnover, OI, extension, and next-candle/96-candle trade fields.

### Task 2: Data and backtest runner

- [ ] Add cached 15-minute candles/OI retrieval, constrained concurrency, crypto universe filters, and missing-data ledger.
- [ ] Run a smoke universe before full collection.
- [ ] Select from development-only rows and write frozen holdout trades, metrics, report, and current watchlist.
