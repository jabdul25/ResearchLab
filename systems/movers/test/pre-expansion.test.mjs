import test from 'node:test';
import assert from 'node:assert/strict';
import { preExpansionRows } from '../src/pre-expansion.mjs';
test('enters next 15m candle and exits 96 candles later without future features',()=>{const c=Array.from({length:130},(_,i)=>({t:i*900000,o:100+i,c:i===16?120:100+i,h:i===16?121:101+i,l:99+i,v:i===16?500:100}));const r=preExpansionRows('XUSDT',c)[0];assert.equal(r.entryOpen,117);assert.equal(r.exitOpen,212);});
