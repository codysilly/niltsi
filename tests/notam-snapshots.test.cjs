const {test}=require('node:test');
const assert=require('node:assert/strict');
const snapshots=require('../notam-snapshots.js');
const fixture=(time='2026-11-11T12:00:00Z')=>({retrieved_utc:time,airports:{KMEM:{completeness:'UNKNOWN',notices:[{id:'duplicate',text:'RWY 18L',effectiveEnd:'PERM'},{id:'duplicate',text:null,formatted_texts:[{type:'LOCAL_FORMAT',field:'simpleText',text:'RWY 36R'}]}]}}});
const ok=data=>({ok:true,json:async()=>data});
const config={baseURL:'https://example.test/spatchin/index.html',now:()=>Date.parse('2026-11-12T12:00:00Z')};
test('strict envelope and representation validation preserves duplicates and raw notice times',()=>{
 const data=fixture();assert.equal(snapshots.validate(data),data);assert.equal(data.airports.KMEM.notices.length,2);assert.equal(data.airports.KMEM.notices[0].effectiveEnd,'PERM');
 for(const data of [null,[],{}, {...fixture(),retrieved_utc:'unparseable'}, {...fixture(),retrieved_utc:'2026-11-11'}, {...fixture(),synthetic:'false'}, {...fixture(),airports:{KMEM:{notices:[{formatted_texts:[null]}]}}}, {...fixture(),airports:{KMEM:{notices:[{text:4}]}}}, {...fixture(),airports:{KMEM:{notices:[{warnings:'error'}]}}}])assert.throws(()=>snapshots.validate(data));
});
test('synthetic data is rejected in production, accepted only by an explicit preview option',()=>{
 const data={...fixture(),synthetic:true};assert.throws(()=>snapshots.validate(data),/Synthetic/);assert.equal(snapshots.validate(data,{allowSynthetic:true}),data);
});
test('startup refresh uses the fixed same-origin snapshot with redirects disabled and single-flight',async()=>{
 let finish,calls=0;const events=[];
 const store=snapshots.create({...config,onChange:s=>events.push(s),fetchFn:(url,options)=>{calls++;assert.equal(url,'https://example.test/spatchin/data/notams.json');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');return new Promise(r=>finish=r);}});
 const first=store.refresh(),second=store.refresh();assert.equal(first,second);assert.equal(store.read().refreshing,true);
 await Promise.resolve();finish(ok(fixture()));await first;
 assert.equal(calls,1);assert.equal(events.length,2);assert.equal(store.read().error,null);assert.equal(store.read().snapshot.airports.KMEM.notices.length,2);
 assert.throws(()=>snapshots.create({...config,baseURL:'file:///tmp/index.html',fetchFn:()=>{}}),/HTTP/);
});
test('HTTP, JSON, and schema failures retain the last accepted snapshot, recovery clears the error',async()=>{
 const original=fixture();const queue=[ok(original),{ok:false,status:503},{ok:true,json:async()=>{throw Error('private server body');}},ok({airports:{}}),ok(fixture('2026-11-12T10:00:00Z'))];
 const store=snapshots.create({...config,fetchFn:async()=>queue.shift()});await store.refresh();
 for(let i=0;i<3;i++){await store.refresh();assert.equal(store.read().snapshot,original);assert.ok(store.read().error);assert.ok(!store.read().error.includes('private'));}
 await store.refresh();assert.equal(store.read().error,null);assert.equal(store.read().snapshot.retrieved_utc,'2026-11-12T10:00:00Z');
});
test('first-load failure remains unavailable and successful empty collections keep coverage unknown',async()=>{
 let response={ok:false,status:404};const store=snapshots.create({...config,fetchFn:async()=>response});await store.refresh();assert.equal(store.read().snapshot,null);assert.match(store.read().error,/404/);
 response=ok({retrieved_utc:'2026-11-11T12:00:00Z',airports:{KMEM:{notices:[]}}});await store.refresh();assert.deepEqual(store.read().snapshot.airports.KMEM.notices,[]);
});
test('older snapshots are rejected without replacing accepted data',async()=>{
 let data=fixture();const store=snapshots.create({...config,fetchFn:async()=>ok(data)});await store.refresh();const previous=store.read().snapshot;
 data=fixture('2026-11-10T12:00:00Z');await store.refresh();assert.equal(store.read().snapshot,previous);assert.match(store.read().error,/Older/);
});
test('future retrieval times are explicitly warned without inventing an age threshold',async()=>{
 const store=snapshots.create({...config,fetchFn:async()=>ok(fixture('2027-01-01T00:00:00Z'))});await store.refresh();assert.match(store.read().error,/Future/);assert.equal(store.read().snapshot,null);
 const old=snapshots.create({...config,fetchFn:async()=>ok(fixture('2020-01-01T00:00:00Z'))});await old.refresh();assert.equal(old.read().error,null);
});
test('timeout releases single-flight, aborts transport, and ignores a late response',async()=>{
 let finish,signal;const store=snapshots.create({...config,timeoutMs:10,fetchFn:(_,options)=>{signal=options.signal;return new Promise(r=>finish=r);}});
 await store.refresh();assert.equal(signal.aborted,true);assert.equal(store.read().refreshing,false);assert.equal(store.read().snapshot,null);assert.match(store.read().error,/timed out/);
 finish(ok(fixture()));await Promise.resolve();await Promise.resolve();assert.equal(store.read().snapshot,null);
});
