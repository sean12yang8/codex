import test from 'node:test';
import assert from 'node:assert/strict';
import {previewDiscount} from '../src/discountPolicy.ts';

test('a new current factor does not override the old historical day',()=>{
 const p=previewDiscount([{day:'2026-11-09',factor:'0.85'}],'1','2026-11-10','2026-11-01','2026-11-09','2026-11-10');
 assert.equal(p.valid,true);assert.equal(p.changedDays,8);assert.equal(p.otherFactors,true);
 assert.equal(p.rows.length,2);
 assert.deepEqual([p.rows[0].from,p.rows[0].to,p.rows[0].before.factor,p.rows[0].after.factor,p.rows[0].after.mode],['2026-11-01','2026-11-08','0.85','1','fallback']);
 assert.deepEqual([p.rows[1].from,p.rows[1].after.factor,p.rows[1].after.mode],['2026-11-09','0.85','historical']);
});

test('future versions do not affect the current fallback',()=>{
 const p=previewDiscount([{day:'2026-11-09',factor:'0.85'},{day:'2026-11-10',factor:'1'}],'0.9','2026-11-11','2026-11-01','2026-11-09','2026-11-10');
 assert.equal(p.changedDays,0);assert.equal(p.currentAfter.factor,'1');
});

test('backdated insertion ends at the next existing version',()=>{
 const p=previewDiscount([{day:'2026-11-09',factor:'0.85'},{day:'2026-11-10',factor:'1'}],'0.5','2026-11-01','2026-11-01','2026-11-10','2026-11-10');
 assert.equal(p.until,'2026-11-08');assert.equal(p.changedDays,8);
 assert.deepEqual(p.rows.map(r=>r.after.factor),['0.5','0.85','1']);
});

test('same-day versions conflict, and zero is a valid factor',()=>{
 const versions=[{day:'2026-11-09',factor:'0.85'}];
 const p=previewDiscount(versions,'1','2026-11-09','2026-11-01','2026-11-09','2026-11-10');
 assert.equal(p.valid,false);assert.equal(p.conflict?.factor,'0.85');assert.equal(p.changedDays,0);
 assert.equal(previewDiscount(versions,'0','2026-11-10','2026-11-01','2026-11-09','2026-11-10').valid,true);
 assert.deepEqual(versions,[{day:'2026-11-09',factor:'0.85'}]);
});

test('invalid and empty UTC periods never synthesize dates',()=>{
 assert.equal(previewDiscount([],'','2026-11-01','2026-11-01','2026-11-09').valid,false);
 assert.equal(previewDiscount([],'1','2026-02-30','2026-11-01','2026-11-09').valid,false);
 assert.deepEqual(previewDiscount([],'1','2026-11-01','2026-11-01','2026-10-31').rows,[]);
});
