// Execute the actual loader, reader and app with DOM stubs; transport is entirely injected.
const {test}=require('node:test');const assert=require('node:assert/strict');
const vm=require('node:vm');const {readFileSync}=require('node:fs');const {join}=require('node:path');
function load({notams,runways}={}){
 const calls=[],elements=new Map(),pending=[];
 const ctx=vm.createContext({URL,AbortController,setTimeout,clearTimeout,console:{info(){},error(){}},setInterval(){},localStorage:{getItem(){return null},setItem(){throw Error('No preferences should be written')}},
  document:{baseURI:'http://local.test/spatchin/index.html',querySelectorAll:()=>[],getElementById:id=>{if(!elements.has(id))elements.set(id,{addEventListener(){},innerHTML:'',textContent:''});return elements.get(id);}},
  fetch:(url,options)=>{calls.push(url);const path=new URL(url,'http://local.test/spatchin/').pathname;
   if(path.endsWith('/notams.json'))return Promise.resolve(notams||{ok:false,status:404});
   if(path.endsWith('/runways.json')&&runways)return Promise.resolve({ok:true,json:async()=>runways});
   return new Promise(resolve=>pending.push(resolve));
  }});
 for(const name of ['notam-reader.js','notam-snapshots.js','app.js'])vm.runInContext(readFileSync(join(__dirname,'..',name),'utf8'),ctx);
 return {ctx,calls,elements,run:s=>vm.runInContext(s,ctx)};
}
const valid=()=>({retrieved_utc:'2020-01-01T00:00:00Z',airports:{KMEM:{notices:[{text:'SYNTHETIC RWY 18L retained report'}]}}});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('dashboard startup requests one local NOTAM snapshot; ordinary rerenders never refetch',async()=>{
 const app=load({notams:{ok:true,json:async()=>valid()}});await tick();
 assert.equal(app.run('notamTransport.error'),null);assert.match(app.elements.get('cards').innerHTML,/retained report/);
 app.run('render();render();render()');assert.equal(app.calls.filter(u=>u.includes('notams.json')).length,1);
 assert.ok(app.calls.every(u=>new URL(u,'http://local.test').hostname==='local.test'));
 assert.match(app.elements.get('cards').innerHTML,/Availability not assessed/);
});
test('dashboard first-load failure is explicit and synthetic production data is refused',async()=>{
 for(const notams of [{ok:false,status:404},{ok:true,json:async()=>({...valid(),synthetic:true})}]){
  const app=load({notams});await tick();assert.equal(app.run('notamTransport.snapshot'),null);assert.match(app.elements.get('cards').innerHTML,/NOTAM data unavailable/);
 }
});
test('valid optional eight-end MEM snapshot renders without wind; invalid MEM is rejected',async()=>{
 const legacy=JSON.parse(readFileSync(join(__dirname,'../data/runways.json'),'utf8'));
 delete legacy.airports.MEM;delete legacy.airport_metadata;
 for(const mem of [undefined,Array.from({length:8},(_,i)=>({id:['09','27','18L','36R','18C','36C','18R','36L'][i],true_heading:i*40})),Array(8).fill({id:'09',true_heading:90}),[{id:'18L',true_heading:999}]]){
  const runways=JSON.parse(JSON.stringify(legacy));if(mem!==undefined)runways.airports.MEM=mem;
  const app=load({runways});await tick();
  const expected=mem&&new Set(mem.map(x=>x.id)).size===8?8:0;
  assert.equal(app.run('ends("MEM").length'),expected);
  if(expected)assert.equal((app.elements.get('cards').innerHTML.match(/data-reader-open=/g)||[]).length,8);
 }
});

test('published MEM inventory shows all eight ends without wind and labels chart-derived bearings',async()=>{
 const runways=JSON.parse(readFileSync(join(__dirname,'../data/runways.json'),'utf8'));
 const app=load({runways});await tick();
 assert.deepEqual(Array.from(app.run('ends("MEM")')),['09','18C','18L','18R','27','36C','36L','36R']);
 assert.equal(app.run('runwayHeading("MEM","09")'),91.3);
 assert.equal(app.run('runwayHeading("MEM","18L")'),178.3);
 assert.equal(app.run('runwayHeading("MEM","36R")'),358.3);
 assert.match(app.elements.get('cards').innerHTML,/Chart-derived approximate true bearings/);
 assert.match(app.elements.get('cards').innerHTML,/00253AD.PDF/);
 assert.equal((app.elements.get('cards').innerHTML.match(/data-reader-open=/g)||[]).length,8);
 assert.equal(app.run('runwayBearingLabel("ORD")'),'FAA NASR true alignment');
 assert.ok(!app.run('runwaySourceBlock("ORD")').includes('airport diagram'));
 const pair=runways.airports.MEM.find(e=>e.id==='27');assert.equal(pair.true_heading,271.3);
 // A later NASR publication replaces the temporary chart metadata naturally.
 app.run('delete runwayData.airport_metadata;render()');
 assert.equal(app.run('runwayBearingLabel("MEM")'),'FAA NASR true alignment');
 assert.ok(!app.elements.get('cards').innerHTML.includes('Chart-derived'));
});
