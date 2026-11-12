'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const feed=require('../notams.js');
const now=Date.parse('2026-11-11T00:05:00Z');
const entry=(notams=[])=>({state:'VERIFIED',verified:true,complete:true,error:null,attempted_utc:new Date(now).toISOString(),last_success_utc:new Date(now).toISOString(),notams});
const notice=(overrides={})=>({id:'SYNTHETIC-1',airport:'KORD',text:'RWY 04L/22R CLSD',effective_start_utc:'2026-11-10T00:00:00Z',effective_end_utc:'2026-11-12T00:00:00Z',retrieved_utc:new Date(now).toISOString(),end_kind:'fixed',schedule:null,lifecycle:'ACTIVE',classification:'closure',reason:'EXPLICIT_WHOLE_RUNWAY_CLOSURE',runway_ends:['04L','22R'],...overrides});
const snapshot=e=>({schema_version:1,generated_utc:new Date(now).toISOString(),airports:{KORD:e,KLAX:entry(),KJFK:entry()}});

test('initial UNKNOWN snapshot validates and all airport states remain UNKNOWN',()=>{
 // Do not depend on the mutable published feed remaining in its initial state.
 const data=snapshot(entry());
 for(const key of Object.keys(data.airports))data.airports[key]={state:'UNKNOWN',verified:false,complete:false,error:'CONTRACT_UNVERIFIED',attempted_utc:null,last_success_utc:null,notams:[]};
 feed.validate(data,now);
 for(const e of Object.values(data.airports))assert.equal(feed.status(e,'04L',false,now).state,'UNKNOWN');
});
test('observed FAA notices retain duplicate IDs, unknown validity and UNKNOWN status',()=>{
 const raw=JSON.parse(fs.readFileSync('tests/fixtures/nms_observed_shape.json','utf8')).data.geojson[0];
 const n=notice({record_key:'SYNTHETIC-record-0',lifecycle:'UNKNOWN',end_kind:'unverified',classification:'unresolved',runway_ends:[],effective_start_utc:null,effective_end_utc:null,original_feature:raw,reason:'FAA_SEMANTICS_UNVERIFIED'});
 const e={...entry([n,{...n,record_key:'SYNTHETIC-record-1'}]),state:'UNKNOWN',verified:false,complete:false,error:'CONTRACT_UNVERIFIED',last_success_utc:null};
 feed.validate(snapshot(e),now);
 assert.equal(feed.status(e,'04L',false,now).state,'UNKNOWN');
 assert.equal(feed.phase(n,now),'unverified lifecycle / validity');
 assert.equal(e.notams[0].original_feature.properties.coreNOTAMData.notamTranslation.length,3);
 assert.throws(()=>feed.validate(snapshot({...e,verified:true,complete:true,state:'VERIFIED',error:null,last_success_utc:e.attempted_utc}),now));
 assert.throws(()=>feed.validate(snapshot({...e,observed_notams:'bad'}),now));
});
test('verified reciprocal closure affects both ends and leaves other ends visible',()=>{
 const e=entry([notice()]);
 assert.equal(feed.status(e,'04L',false,now).state,'CLOSED');
 assert.equal(feed.status(e,'22R',false,now).state,'CLOSED');
 assert.equal(feed.status(e,'04R',false,now).state,'NO CLOSURE IDENTIFIED');
});
test('empty complete verified result never claims operationally open',()=>{
 assert.equal(feed.status(entry(),'04L',false,now).state,'NO CLOSURE IDENTIFIED');
});
test('no notices with incomplete coverage means UNKNOWN',()=>{
 assert.equal(feed.status({...entry(),complete:false},'04L',false,now).state,'UNKNOWN');
});
test('single-end restriction does not become reciprocal closure',()=>{
 const e=entry([notice({classification:'restriction',runway_ends:['04L']})]);
 assert.equal(feed.status(e,'04L',false,now).state,'NOTAM RESTRICTION');
 assert.equal(feed.status(e,'22R',false,now).state,'NO CLOSURE IDENTIFIED');
});
test('future and expired closures do not assert current closure; exact time boundaries',()=>{
 const n=notice();
 assert.equal(feed.status(entry([n]),'04L',false,Date.parse(n.effective_start_utc)-1).state,'UNKNOWN'); // retrieval is in future
 const future=notice({effective_start_utc:'2026-11-12T00:00:00Z',effective_end_utc:'2026-11-13T00:00:00Z'});
 assert.equal(feed.phase(future,now),'future');
 assert.equal(feed.status(entry([future]),'04L',false,now).state,'NO CLOSURE IDENTIFIED');
 const expired=notice({effective_end_utc:new Date(now).toISOString()});
 assert.equal(feed.phase(expired,now),'expired');
 assert.equal(feed.status(entry([expired]),'04L',false,now).state,'NO CLOSURE IDENTIFIED');
 const starts=notice({effective_start_utc:new Date(now).toISOString()});
 assert.equal(feed.status(entry([starts]),'04L',false,now).state,'CLOSED');
});
test('permanent notice stays active',()=>{
 assert.equal(feed.status(entry([notice({end_kind:'permanent',effective_end_utc:null})]),'04L',false,now).state,'CLOSED');
});
test('estimated expiration does not silently clear uncertainty',()=>{
 const n=notice({end_kind:'estimated',effective_end_utc:'2026-11-11T00:00:00Z',classification:'unresolved'});
 assert.equal(feed.status(entry([n]),'04L',false,now).state,'UNKNOWN');
});
test('unresolved schedule and conditional language prevent a closure claim',()=>{
 const n=notice({classification:'unresolved',schedule:'DLY 0100-0200'});
 assert.equal(feed.status(entry([n]),'04L',false,now).state,'UNKNOWN');
 assert.equal(feed.status(entry([n]),'04R',false,now).state,'NO CLOSURE IDENTIFIED');
});
test('unassociated active uncertainty affects every end',()=>{
 const n=notice({classification:'unresolved',runway_ends:[]});
 for(const id of ['04L','22R','09R'])assert.equal(feed.status(entry([n]),id,false,now).state,'UNKNOWN');
});
test('freshness crosses 15 minute boundary and clock skew is rejected',()=>{
 const e=entry([notice()]);
 assert.equal(feed.status(e,'04L',false,now+feed.STALE_MS-1).state,'CLOSED');
 const stale=feed.status(e,'04L',false,now+feed.STALE_MS);
 assert.equal(stale.state,'UNKNOWN');assert.equal(stale.lastReportedClosure,true);
 assert.equal(feed.status(e,'04L',false,now-300001).state,'UNKNOWN');
});
test('browser failure, API failure and unverified coverage retain evidence but mean UNKNOWN',()=>{
 const e=entry([notice()]);
 assert.equal(feed.status(e,'04L',true,now).state,'UNKNOWN');
 assert.equal(feed.status({...e,error:'HTTP_FAILURE'},'04L',false,now).state,'UNKNOWN');
 assert.equal(feed.status({...e,verified:false},'04L',false,now).state,'UNKNOWN');
});
test('cancelled and replaced notices are not current restrictions',()=>{
 for(const lifecycle of ['CANCELLED','REPLACED'])assert.equal(feed.status(entry([notice({lifecycle})]),'04L',false,now).state,'NO CLOSURE IDENTIFIED');
});
test('schema validation rejects malformed validity, coverage and unsafe interpretations',()=>{
 for(const n of [notice({effective_end_utc:null}),notice({effective_start_utc:'bad'}),notice({schedule:'DLY'}),notice({end_kind:'estimated'}),notice({runway_ends:[]})]){
  assert.throws(()=>feed.validate(snapshot(entry([n])),now));
 }
 assert.throws(()=>feed.validate(snapshot({...entry(),last_success_utc:null}),now));
 assert.throws(()=>feed.validate(snapshot(entry([notice(),notice()])),now));
 assert.throws(()=>feed.validate(snapshot({...entry(),attempted_utc:'2027-01-01T00:00:00Z'}),now));
});
test('frontend retains all 32 runway rows without wind and escapes NOTAM text',()=>{
 const elements=new Map();
 const element=id=>{
  if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',addEventListener(){}});
  return elements.get(id);
 };
 const context=vm.createContext({document:{getElementById:element,querySelectorAll:()=>[]},localStorage:{getItem:()=>null},setInterval(){},fetch:async()=>{throw Error('offline');},console:{info(){},error(){}},Date});
 vm.runInContext(fs.readFileSync('notams.js','utf8'),context);
 vm.runInContext(fs.readFileSync('app.js','utf8'),context);
 // Use current time so the snapshot is fresh in this integration check.
 const current=new Date().toISOString();
 const n=notice({text:'<script>alert("synthetic")</script>',retrieved_utc:current,effective_start_utc:'2020-01-01T00:00:00Z',effective_end_utc:'2099-01-01T00:00:00Z'});
 const data=snapshot(entry([n]));data.generated_utc=current;
 for(const e of Object.values(data.airports)){e.last_success_utc=current;e.attempted_utc=current;}
 vm.runInContext(`notamData=NotamFeed.validate(${JSON.stringify(data)}); render();`,context);
 const html=element('cards').innerHTML;
 assert.equal((html.match(/<tr><td/g)||[]).length,32);
 assert.equal((html.match(/class="notam-status closed"/g)||[]).length,2);
 assert.ok(html.includes('NO CLOSURE IDENTIFIED'));
 assert.ok(html.includes('&lt;script&gt;'));
 assert.ok(!html.includes('<script>alert'));
 assert.ok(html.includes('METAR WIND'));
 assert.ok(html.includes('Full ATIS information'));
 assert.ok(fs.readFileSync('index.html','utf8').includes('Niltsi v0.9'));
 vm.runInContext(`metars.ORD=parseMetar({wdir:40,wspd:10,wgst:20,obsTime:1794358260,rawOb:'SYNTHETIC METAR'}); atisData={airports:{KORD:[{type:'combined',code:'A',time:'0000',updatedAt:${JSON.stringify(current)},datis:'LANDING RWY 4L. DEPS EXP RWYS 22R.'}]}}; atisRetrieved=${JSON.stringify(current)}; render();`,context);
 const withWind=element('cards').innerHTML;
 assert.equal((withWind.match(/<tr><td/g)||[]).length,32);
 assert.equal((withWind.match(/class="notam-status closed"/g)||[]).length,2);
 assert.ok(withWind.includes('headwind'));
 assert.ok(withWind.includes('class="gust"'));
 assert.ok(withWind.includes('SYNTHETIC METAR'));
 assert.equal(vm.runInContext(`JSON.stringify(advertised('LANDING RWY 4L.','arr'))`,context),'["04L"]');
 const observed=notice({id:'SYNTHETIC-OBSERVED',record_key:'SYNTHETIC-OBSERVED-0',retrieved_utc:current,lifecycle:'UNKNOWN',end_kind:'unverified',classification:'unresolved',runway_ends:[],effective_start_utc:null,effective_end_utc:null,original_feature:{translation:{formattedText:'<script>source-test</script>'}},source_provenance:{query_airport:'KORD'},reason:'FAA_SEMANTICS_UNVERIFIED'});
 data.airports.KORD={...data.airports.KORD,state:'UNKNOWN',verified:false,complete:false,error:'CONTRACT_UNVERIFIED',last_success_utc:null,notams:[observed]};
 vm.runInContext(`notamData=NotamFeed.validate(${JSON.stringify(data)});render();`,context);
 const unverifiedHTML=element('cards').innerHTML;
 assert.ok(unverifiedHTML.includes('Preserved original notice and provenance'));
 assert.ok(unverifiedHTML.includes('&lt;script&gt;source-test&lt;/script&gt;'));
 assert.ok(!unverifiedHTML.includes('<script>source-test'));
 assert.equal((unverifiedHTML.match(/<tr><td/g)||[]).length,32);
 assert.equal((unverifiedHTML.match(/class="notam-status closed"/g)||[]).length,0);
});
