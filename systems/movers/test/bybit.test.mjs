import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeKlines, selectUniverse } from '../src/bybit.mjs';

test('normalizes reverse-sorted Bybit klines oldest first', () => {
  assert.deepEqual(normalizeKlines([['2','2','3','1','2','4','5'],['1','1','2','0','1','3','4']]).map(x => x.t), [1, 2]);
});
test('selects only trading USDT linear perpetuals', () => {
  assert.deepEqual(selectUniverse([{symbol:'AUSDT',status:'Trading',settleCoin:'USDT',contractType:'LinearPerpetual'},{symbol:'B',status:'Trading',settleCoin:'USDC'}]).map(x=>x.symbol), ['AUSDT']);
});
