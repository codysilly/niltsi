const {test}=require('node:test');
const assert=require('node:assert/strict');
const reader=require('../notam-reader.js');
test('explicit text references retain exact offsets and never infer reciprocals',()=>{
 const text='🙂 RWY 18L and 36R; TWY A near RWY 9.';
 const refs=reader.references(text,['18L','36R','09','27']);
 assert.deepEqual(refs.map(r=>r.id),['18L','36R','09']);
 for(const ref of refs)assert.equal(text.slice(ref.start,ref.end),ref.raw);
 assert.deepEqual(reader.references('RWY 18L',['18L','36R']).map(r=>r.id),['18L']);
 assert.deepEqual(reader.references('18L lights; RWY 99; RWY 18LX; RWY 018L',['18L']),[]);
});
test('duplicates, raw times, missing text, explicit scope and unmatched notices are retained',()=>{
 reader.set({retrieved_utc:'old raw retrieval',airports:{KMEM:{notices:[{id:'same',text:'RWY 18L',effectiveEnd:'PERM'},{id:'same',text:'General report'},{id:'wide',text:'Airport report',scope:'airport-wide'},{id:'missing'}]}}});
 const records=reader.records('KMEM',['18L','36R']);assert.equal(records.length,4);assert.notEqual(records[0].key,records[1].key);
 const html=reader.block('KMEM',['18L','36R']);
 assert.match(html,/PERM/);assert.match(html,/Original text unavailable/);assert.match(html,/Completeness: UNKNOWN/);
 assert.match(html,/Unmatched \/ scope unknown \(2\)/);assert.match(html,/source-declared airport-wide \(1\)/);
 assert.match(reader.cell('KMEM','36R',['18L','36R']),/Read NOTAMs \(0\)/);
});
test('source strings are escaped; unavailable and retained-error states are explicit',()=>{
 reader.set({airports:{KMEM:{notices:[{text:'RWY 18L <img src=x onerror=alert(1)>',source:'<script>'}]}}},'Refresh failed');
 const html=reader.block('KMEM',['18L']);assert.ok(!html.includes('<img'));assert.match(html,/&lt;img/);assert.match(html,/retained snapshot/);
 reader.set(null);assert.match(reader.block('KMEM',[]),/NOTAM data unavailable/);
});
