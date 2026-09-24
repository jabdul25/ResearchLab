import test from 'node:test';
import assert from 'node:assert/strict';
import { chooseRule, passesRule } from '../src/backtest.mjs';

test('does not select a rule from profitable holdout data', () => {
  const rows = [
    {split:'development',isBreakout10:true,isBreakout20:true,compression10:.05,turnoverMultiple10:3,trend5:0,oiChange2d:.1,netReturn:-.02},
    {split:'holdout',isBreakout10:true,isBreakout20:true,compression10:.05,turnoverMultiple10:3,trend5:0,oiChange2d:.1,netReturn:.2},
  ];
  assert.equal(chooseRule(rows, [{lookback:10,maxCompression:.1,minTurnover:2,minTrend:0,oi:false}], 1), null);
});
test('missing OI cannot satisfy an OI-required rule', () => assert.equal(passesRule({isBreakout10:true,compression10:.01,turnoverMultiple10:9,trend5:0,oiChange2d:null},{lookback:10,maxCompression:.1,minTurnover:2,minTrend:0,oi:true}), false));
