import test from 'node:test';
import assert from 'node:assert/strict';
import { completedCandles, netLongReturn } from '../src/core.mjs';
import { featureRows } from '../src/core.mjs';

test('removes the current unfinished candle', () => {
  assert.equal(completedCandles([{ t: 0 }, { t: 86_400_000 }], 86_500_000).length, 1);
});

test('deducts costs twice from a long return', () => {
  assert.ok(netLongReturn(100, 101, 5.5, 10) < 0.01);
});

test('marks a close above the prior ten-day high as a breakout', () => {
  const candles = Array.from({ length: 23 }, (_, i) => ({ t: i * 86_400_000, o: 100, c: i === 20 ? 111 : 100, h: i < 20 ? 110 : 112, l: 90, v: 100 }));
  assert.equal(featureRows('XUSDT', candles)[0].isBreakout10, true);
});

test('does not treat missing open interest as confirmation', () => {
  const candles = Array.from({ length: 23 }, (_, i) => ({ t: i * 86_400_000, o: 100, c: 100, h: 110, l: 90, v: 100 }));
  assert.equal(featureRows('XUSDT', candles)[0].oiChange2d, null);
});

test('omits a signal with no next two opens', () => {
  const candles = Array.from({ length: 22 }, (_, i) => ({ t: i * 86_400_000, o: 100, c: 100, h: 110, l: 90, v: 100 }));
  assert.equal(featureRows('XUSDT', candles).length, 0);
});
