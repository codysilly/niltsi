'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const reader=require('../notam-reader');
const catalog=require('../airport-catalog');
test('approved catalog preserves all 130 owner-supplied codes without inventing metadata',()=>{
 assert.equal(catalog.length,130);assert.equal(new Set(catalog).size,130);
 assert.ok(['MEM','ORD','LAX','JFK','IND','EWR','DJT'].every(c=>catalog.includes(c)));
 assert.ok(catalog.every(c=>/^[A-Z]{3}$/.test(c)));
});
test('supplied literal examples, separators, boundaries and repeated references',()=>{
 const ends=['18','36','18R','36L','36R','18L'];
 const match=t=>reader.references(t,ends).map(r=>r.id);
 assert.deepEqual(match('!MEM 11/207 MEM RWY 36L ALS U/S 2611161400-2611162300'),['36L']);
 assert.deepEqual(match('!MEM 11/214 MEM TWY M BTN TWY M7 AND TWY M9, TWY M8 BTN RWY 18R/36L AND TWY N CLSD 2611141300-2611142300'),['18R','36L']);
 assert.deepEqual(match('rwys 18r, 36l AND 18L'),['18R','36L','18L']);
 for(const t of ['RWY 018R','RWY 36LX','RWY 36L_foo','XRWY 36L','RWY 99','118R RWY END'])assert.deepEqual(match(t),[],t);
 reader.set({airports:{KMEM:{notices:[{number:'11/9',text:'RWY 18R; RWY 18R'},{number:'11/9',text:'RWY 18R'}]}}});
 assert.match(reader.cell('KMEM','18R',ends),/Read NOTAMs \(2\)/);
 assert.match(reader.cell('KMEM','36L',ends),/No matching references; coverage UNKNOWN/);
 assert.equal(reader.records('KMEM',ends).length,2);
});
test('numeric month, explicit year, distinct international family, missing IDs and stable ties',()=>{
 const list=[{number:'11/214'},{number:'11/9'},{number:'11/207'},{number:'11/27'},{number:'2/100'},{number:'A8237/26'},{number:'A9/26'},{id:'misc10'},{id:'misc2'},{text:'missing'}, {number:'11/9',text:'stable tie'}, {number:'12/900',year:2025},{number:'1/1',year:2026}];
 const sorted=list.slice().sort(reader.compare);
 assert.deepEqual(sorted.map(n=>n.number||n.id||'missing'),['12/900','1/1','2/100','11/9','11/9','11/27','11/207','11/214','A9/26','A8237/26','misc2','misc10','missing']);
 assert.equal(sorted[3],list[1]);assert.equal(sorted[4],list[10]);
 const neverGuess=[{number:'11/9',retrieved_utc:'2020-01-01',effectiveStart:'2040-01-01'},{number:'11/8',retrieved_utc:'2030-01-01'}];
 assert.equal(neverGuess.sort(reader.compare)[0].number,'11/8');
 for(const a of list)for(const b of list)for(const c of list)if(reader.compare(a,b)<=0&&reader.compare(b,c)<=0)assert.ok(reader.compare(a,c)<=0);
});
test('zero verification requires explicit evidence; empty/missing/unknown are distinct',()=>{
 reader.set(null);assert.match(reader.availability('KMEM'),/Unavailable/);
 const entry={notices:[],completeness:'UNKNOWN'};reader.set({airports:{KMEM:entry}});
 assert.match(reader.availability('KMEM'),/UNKNOWN/);
 entry.zero_verified=true;entry.completeness='VERIFIED';assert.match(reader.availability('KMEM'),/UNKNOWN/);
 entry.verification_reference='SYNTHETIC review evidence';assert.match(reader.availability('KMEM'),/Explicitly verified zero/);
 entry.notices.push({text:'record'});assert.match(reader.availability('KMEM'),/UNKNOWN/);
});
