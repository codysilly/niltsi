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

// Documentation-derived grammar; not a captured live NOTAM.
test('prefix runway-end references require a complete designation and exact RWY END context',()=>{
 const text='🙂 27 RWY END ID LGT U/S; RWY 09';
 const refs=reader.references(text,['09','27']);
 assert.deepEqual(refs.map(r=>r.id),['27','09']);
 for(const r of refs)assert.equal(text.slice(r.start,r.end),r.raw);
 for(const text of ['127 RWY END','027 RWY END','27X RWY END','27 lights near RWY','27 RWY ENDING','9 aircraft RWY END'])assert.deepEqual(reader.references(text,['09','27']),[]);
 assert.deepEqual(reader.references('27 RWY END',['09']),[]);
});
test('formatted representations are escaped, indexed distinctly and never replace missing condition text',()=>{
 reader.set({airports:{KCLT:{notices:[{text:null,formatted_texts:[{type:'LOCAL_FORMAT',field:'simpleText',text:'RWY 27 <script>'}],warnings:['Condition text missing']}]}}});
 const n=reader.records('KCLT',['09','27'])[0];
 assert.equal(n.references[0].representation,'formatted_texts[0].text');
 assert.equal(n.formatted_texts[0].text.slice(n.references[0].start,n.references[0].end),'27');
 const html=reader.block('KCLT',['09','27']);
 assert.match(html,/Original text unavailable/);assert.match(html,/LOCAL_FORMAT/);assert.match(html,/Condition text missing/);
 assert.ok(!html.includes('<script>'));assert.match(html,/&lt;script&gt;/);
 assert.match(reader.cell('KCLT','27',['09','27']),/Read NOTAMs \(1\)/);
});
