const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const {mkdirSync}=require('node:fs');
const base='http://127.0.0.1:8767';
let server,browser;
before(async()=>{
 server=spawn('python3',['-B','-m','http.server','8767','--bind','127.0.0.1'],{stdio:'ignore'});
 for(let i=0;i<50;i++){try{if((await fetch(base)).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true});
});
after(async()=>{await browser?.close();server?.kill();});
test('source and provider text retain CRLF, CR, LF and escaped markup in the DOM',async()=>{
 const page=await browser.newPage();
 try{
  await page.route('**/*',route=>route.abort());
  await page.setContent('<main id="reader"></main>');
  await page.addScriptTag({content:require('node:fs').readFileSync(require('node:path').join(__dirname,'../notam-reader.js'),'utf8')});
  const result=await page.evaluate(()=>{
   const text='SYNTHETIC RWY 18L\r\nline two\rline three\n<img src=x onerror=alert(1)>&';
   const translation='SYNTHETIC provider\r\nRWY 36R\r<unsafe>';
   NotamReader.set({retrieved_utc:'2020-01-01T12:34:56Z',airports:{KMEM:{completeness:'UNKNOWN',notices:[{id:'SYNTHETIC',text,formatted_texts:[{text:translation}]}]}}});
   document.getElementById('reader').innerHTML=NotamReader.block('KMEM',['18L','36R']);
   const paragraphs=document.querySelectorAll('#reader-KMEM-all .reader-raw');
   const visible=document.querySelector('.notam-reader').innerText;
   return {original:paragraphs[0].textContent===text,translation:paragraphs[1].textContent===translation,injected:document.querySelectorAll('img,unsafe').length,
    retrieval:visible.includes('Retrieved: 2020-01-01T12:34:56Z'),unknown:visible.includes('Completeness: UNKNOWN'),
    scope:visible.includes('Snapshot records only; complete airport coverage has not been established. No closure interpretation is performed.'),
    snapshotLabel:visible.includes('All notices in this snapshot (1)')};
  });
  assert.deepEqual(result,{original:true,translation:true,injected:0,retrieval:true,unknown:true,scope:true,snapshotLabel:true});
 }finally{await page.close();}
});
for(const width of [1440,390])test(`synthetic reader, focus, SCAN and unavailable states at ${width}px`,async()=>{
 const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),external=[],errors=[];
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin===base)return route.continue();external.push(route.request().url());return route.abort();});
 page.on('pageerror',e=>errors.push(e.message));
 try{
 await page.goto(base+'/preview/');
 await page.waitForSelector('.rw-table > tbody > tr.runway-row');
 assert.equal(await page.locator('.rw-table > tbody > tr.runway-row').count(),8);
 assert.match(await page.locator('.synthetic-banner').innerText(),/SYNTHETIC — NO LIVE DATA/);
 assert.equal(await page.evaluate(()=>localStorage.getItem('spatchin-airports')),null);
 const b=page.locator('[data-reader-open="KMEM-18L"]');assert.match(await b.innerText(),/\(2\)/);
 await b.click();assert.equal(await page.locator('#reader-KMEM-18L').getAttribute('hidden'),null);
 await page.evaluate(()=>{render();});
 assert.equal(await page.evaluate(()=>document.activeElement.dataset.readerOpen),'KMEM-18L');
 assert.equal(await page.locator('#reader-KMEM-18L').getAttribute('hidden'),null);
 const meta=page.locator('#reader-KMEM-18L .reader-notice details').first();await meta.locator('summary').click();
 await page.evaluate(()=>render());assert.equal(await meta.getAttribute('open'),'');
 assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Preserved source metadata');
 await b.focus();await page.evaluate(()=>render());assert.equal(await page.evaluate(()=>document.activeElement.dataset.readerOpen),'KMEM-18L');
 // Collapse every reader disclosure; SCAN must find mounted text and reveal ancestors.
 await page.evaluate(()=>document.querySelectorAll('.notam-reader details').forEach(el=>el.open=false));
 await page.locator('#scan-toggle').click();await page.locator('#scan-terms').fill('lighting report');
 await page.waitForFunction(()=>document.querySelectorAll('.notam-reader mark').length>0);
 await page.locator('#scan-next').click();
 assert.ok(await page.locator('.notam-reader details[open], [data-reader-inline]:not([hidden])').count()>0);
 // Dynamic refresh remains searchable and preserves raw time tokens.
 await page.evaluate(()=>{SPATCHIN_SYNTHETIC_PREVIEW.notams.airports.KMEM.notices[0].text+=' DYNAMICREADER';render();});
 await page.locator('#scan-terms').fill('DYNAMICREADER');await page.waitForFunction(()=>document.querySelectorAll('.notam-reader mark').length>0);
 await page.locator('#scan-next').click();
 assert.equal(await page.locator('.notam-reader b').count(),0);
 assert.match(await page.locator('#reader-KMEM-all').textContent(),/PERM/);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 mkdirSync('/tmp/spatchin-reader-review',{recursive:true});
 await page.evaluate(()=>{document.querySelectorAll('.notam-reader details').forEach(el=>el.open=false);document.getElementById('reader-KMEM-18L').hidden=false;});
 await page.screenshot({path:`/tmp/spatchin-reader-review/${width}.png`,fullPage:true});
 await page.selectOption('#preview-state','stale');assert.match(await page.locator('.notam-reader').innerText(),/source reports STALE/);
 await page.selectOption('#preview-state','error');assert.match(await page.locator('.notam-reader').innerText(),/refresh failed/);
 await page.selectOption('#preview-state','unavailable');assert.match(await page.locator('.notam-reader').innerText(),/NOTAM data unavailable/);
 assert.equal(await page.locator('.rw-table > tbody > tr.runway-row').count(),8);
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 }finally{await context.close();}
});
for(const state of ['available','unavailable','synthetic-rejected'])test(`production entry isolates preview fixtures with snapshot ${state}`,async()=>{
 const context=await browser.newContext(),page=await context.newPage(),external=[],errors=[];
 const fixture={retrieved_utc:'2020-01-01T00:00:00Z',airports:{KMEM:{completeness:'UNKNOWN',warning:'SYNTHETIC TEST SNAPSHOT',notices:[{id:'TEST-ONLY',text:'SYNTHETIC TEST NOTICE'}]}}};
 if(state==='synthetic-rejected')fixture.synthetic=true;
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin===base)return route.continue();external.push(route.request().url());return route.abort();});
 // Control the NOTAM response: publishing a real file must not change this test.
 await context.route('**/data/notams.json',route=>state==='unavailable'?route.fulfill({status:404,body:'test fixture unavailable'}):route.fulfill({json:fixture}));
 page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto(base);
  await page.waitForFunction(()=>notamTransport&&!notamTransport.refreshing&&(notamTransport.snapshot||notamTransport.error));
  assert.equal(await page.evaluate(()=>typeof SPATCHIN_SYNTHETIC_PREVIEW),'undefined');
  assert.equal(await page.locator('.synthetic-banner').count(),0);
  const reader=page.locator('.notam-reader');
  if(state==='available'){
   assert.ok((await reader.innerText()).includes('All notices in this snapshot (1)'));
   assert.match(await reader.innerText(),/SYNTHETIC TEST SNAPSHOT/);
   assert.doesNotMatch(await reader.innerText(),/NOTAM data unavailable/);
   await page.locator('#reader-KMEM-all > summary').click();
   assert.equal(await page.locator('#reader-KMEM-all .reader-raw').textContent(),'SYNTHETIC TEST NOTICE');
  }else{
   assert.match(await reader.innerText(),/NOTAM data unavailable/);
   assert.equal(await page.locator('.reader-notice').count(),0);
   if(state==='synthetic-rejected')assert.match(await reader.innerText(),/Synthetic NOTAM snapshot rejected outside preview/);
  }
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
 }finally{await context.close();}
});
for(const width of [1440,390])test(`offline normalized documentation example renders with SCAN at ${width}px`,async()=>{
 const {spawnSync}=require('node:child_process');
 const normalized=spawnSync('python3',['-B','scripts/normalize_notam_snapshot.py','tests/fixtures/nms_reader_documentation.json','--airport','KCLT','--location','KCLT','--retrieved-utc','documentation preview only'],{encoding:'utf8'});
 assert.equal(normalized.status,0,normalized.stderr);
 const snapshot=JSON.parse(normalized.stdout);
 const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),external=[];
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin===base)return route.continue();external.push(route.request().url());return route.abort();});
 try{
  await page.goto(base+'/preview/');await page.waitForSelector('.rw-table > tbody > tr.runway-row');
  // Exercise the standalone reader against actual adapter output, with no feed wiring.
  await page.evaluate(data=>{
   NotamReader.set(data);const container=document.createElement('div');container.id='adapter-review';
   container.innerHTML='<table><tbody><tr>'+NotamReader.cell('KCLT','27',['09','27'])+'</tr></tbody></table>'+NotamReader.block('KCLT',['09','27']);
   document.body.append(container);NotamReader.after();
  },snapshot);
  const review=page.locator('#adapter-review');
  assert.match(await review.innerText(),/Documentation-derived mapping/);
  await review.locator('#reader-KCLT-all').evaluate(el=>el.open=true);
  assert.match(await review.innerText(),/27 RWY END ID LGT U\/S/);
  assert.match(await review.innerText(),/LOCAL_FORMAT/);
  assert.match(await review.locator('#reader-KCLT-all').innerText(),/!CLT 01\/123 SAMPLE NOTAM/);
  await review.locator('#reader-KCLT-all').evaluate(el=>el.open=false);
  await page.locator('#scan-toggle').click();await page.locator('#scan-terms').fill('SAMPLE NOTAM');
  await page.waitForFunction(()=>document.querySelector('#adapter-review mark'));
  await page.locator('#scan-next').click();
  assert.ok(await review.locator('mark').count()>0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  assert.deepEqual(external,[]);
 }finally{await context.close();}
});
