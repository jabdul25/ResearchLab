import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
const BASE = 'https://api.bybit.com/v5/market';
export const normalizeKlines = rows => rows.map(x => ({t:+x[0],o:+x[1],h:+x[2],l:+x[3],c:+x[4],v:+x[6]})).sort((a,b)=>a.t-b.t);
export const selectUniverse = rows => rows.filter(x => x.status === 'Trading' && x.settleCoin === 'USDT' && x.contractType === 'LinearPerpetual');
async function request(path, params) { const u = new URL(BASE + path); Object.entries(params).forEach(([k,v])=>u.searchParams.set(k,v)); for(let i=0;i<4;i++){ try { const r=await fetch(u); const j=await r.json(); if(r.ok && !j.retCode) return j.result; if(r.status!==429 && r.status<500) throw Error(j.retMsg); } catch(e){ if(i===3) throw e; } await new Promise(r=>setTimeout(r, 300*(i+1))); } }
export async function discoverUniverse(){ let cursor='', all=[]; do { const r=await request('/instruments-info',{category:'linear',limit:'1000',cursor}); all.push(...r.list); cursor=r.nextPageCursor||''; } while(cursor); return selectUniverse(all); }
async function cached(cacheDir,key,refresh,fn){ await mkdir(cacheDir,{recursive:true}); const file=join(cacheDir,key); if(!refresh) try{return JSON.parse(await readFile(file,'utf8'));}catch{} const x=await fn(); await writeFile(file,JSON.stringify(x)); return x; }
export async function getDailyCandles(symbol, days, cacheDir, refresh=false){ const list=await cached(cacheDir,`${symbol}-kline-${days}.json`,refresh,()=>request('/kline',{category:'linear',symbol,interval:'D',limit:Math.min(days,1000)}).then(x=>x.list)); return normalizeKlines(list); }
export async function getDailyOi(symbol, days, cacheDir, refresh=false){ const list=await cached(cacheDir,`${symbol}-oi-${days}.json`,refresh,()=>request('/open-interest',{category:'linear',symbol,intervalTime:'1d',limit:Math.min(days,200)}).then(x=>x.list)); return new Map(list.map(x=>[+x.timestamp,+x.openInterest])); }
