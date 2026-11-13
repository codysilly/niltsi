const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {chromium}=require('playwright');

for(const width of [1440,390])test(`collection warning survives loader failure and recovery at ${width}px`,async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true});
 const context=await browser.newContext({viewport:{width,height:1000}});
 const page=await context.newPage(),external=[],errors=[];
 await context.route('**/*',route=>{external.push(route.request().url());return route.abort();});
 page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.setContent('<main id="reader"></main>');
  await page.addStyleTag({content:readFileSync(join(__dirname,'../styles.css'),'utf8')});
  for(const file of ['notam-reader.js','notam-snapshots.js'])await page.addScriptTag({content:readFileSync(join(__dirname,'..',file),'utf8')});
  const result=await page.evaluate(async()=>{
   const warning='Fixed snapshot <unsafe>. Airport coverage UNKNOWN. No automatic updates.';
   const text='SYNTHETIC RWY 18L\r\ntext retained\rverbatim';
   const snapshot={retrieved_utc:'2020-01-01T00:00:00Z',airports:{KMEM:{warning,completeness:'UNKNOWN',notices:[{text,source:'NMS-API snapshot'}]}}};
   let failed=false;
   const loader=NotamSnapshots.create({baseURL:'https://snapshot.invalid/',fetchFn:async()=>({ok:!failed,status:failed?503:200,json:async()=>snapshot}),onChange:state=>{
    NotamReader.set(state.snapshot,state.error);
    document.getElementById('reader').innerHTML=NotamReader.block('KMEM',['18L','36R']);
   }});
   const inspect=()=>({visible:document.getElementById('reader').innerText,text:document.querySelector('#reader-KMEM-all .reader-raw')?.textContent,injected:document.querySelectorAll('unsafe').length});
   await loader.refresh();const loaded=inspect();
   failed=true;await loader.refresh();const retained=inspect();
   failed=false;await loader.refresh();const recovered=inspect();
   return {loaded,retained,recovered,warning,text,overflow:document.documentElement.scrollWidth>innerWidth};
  });
  for(const stage of [result.loaded,result.retained,result.recovered]){
   assert.ok(stage.visible.includes(result.warning));
   assert.ok(stage.visible.includes('Completeness: UNKNOWN'));
   assert.ok(stage.visible.includes('Retrieved: 2020-01-01T00:00:00Z'));
   assert.equal(stage.text,result.text);
   assert.equal(stage.injected,0);
  }
  assert.match(result.retained.visible,/refresh failed \(HTTP 503\)/);
  assert.match(result.retained.visible,/Showing retained snapshot/);
  assert.doesNotMatch(result.loaded.visible,/refresh failed/);
  assert.doesNotMatch(result.recovered.visible,/refresh failed|Showing retained snapshot/);
  assert.equal(result.overflow,false);
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
 }finally{await context.close();await browser.close();}
});
