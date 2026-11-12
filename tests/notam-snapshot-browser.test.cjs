const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const {readFileSync,mkdirSync}=require('node:fs');
const {join}=require('node:path');
const base='http://127.0.0.1:8768';let server,browser;
before(async()=>{
 server=spawn('python3',['-B','-m','http.server','8768','--bind','127.0.0.1'],{stdio:'ignore'});
 for(let i=0;i<50;i++){try{if((await fetch(base)).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true});
});
after(async()=>{await browser?.close();server?.kill();});
const fixture=()=>({retrieved_utc:'2026-11-11T12:00:00Z',airports:{KMEM:{completeness:'UNKNOWN',notices:[{id:'SYNTHETIC',text:'SYNTHETIC RWY 18L lighting report',source:'Local test fixture',effectiveEnd:'PERM'}]}}});
const inventory=['09','27','18L','36R','18C','36C','18R','36L'];
for(const width of [1440,390])test(`same-origin snapshot flow, retained failures, SCAN and MEM inventory at ${width}px`,async()=>{
 const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),external=[],errors=[];let mode='good',requests=0;
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin===base)return route.continue();external.push(route.request().url());return route.abort();});
 await context.route('**/data/notams.json',route=>{
  requests++;if(mode==='error')return route.fulfill({status:503,body:'private error body'});
  const data=fixture();if(mode==='bad')data.airports.KMEM.notices={};if(mode==='synthetic')data.synthetic=true;
  return route.fulfill({json:data});
 });
 await context.route('**/data/metars.json?*',route=>route.fulfill({json:{updated_utc:new Date().toISOString(),metars:[]}}));
 const runways=JSON.parse(readFileSync(join(__dirname,'../data/runways.json'),'utf8'));
 runways.airports.MEM=inventory.map((id,i)=>({id,true_heading:i*40,pair:'SYNTHETIC'}));
 await context.route('**/data/runways.json?*',route=>route.fulfill({json:runways}));
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(base);await page.waitForFunction(()=>notamTransport?.checked_utc&&!notamTransport.refreshing);
  assert.equal(requests,1);assert.equal(await page.locator('.rw-table tbody tr').count(),8);
  await page.locator('[data-reader-open="KMEM-18L"]').click();
  const panel=page.locator('#reader-KMEM-18L');assert.match(await panel.innerText(),/lighting report/);
  await panel.locator('summary').first().focus();
  const prefs=await page.evaluate(()=>[localStorage.getItem('spatchin-airports'),localStorage.getItem('spatchin-scan-v1')]);
  mode='error';await page.evaluate(()=>notamSnapshots.refresh());
  assert.equal(await panel.getAttribute('open'),'');assert.equal(await page.evaluate(()=>document.activeElement.parentElement.id),'reader-KMEM-18L');
  assert.match(await page.locator('.notam-reader').innerText(),/retained snapshot/);assert.match(await panel.innerText(),/lighting report/);
  assert.deepEqual(await page.evaluate(()=>[localStorage.getItem('spatchin-airports'),localStorage.getItem('spatchin-scan-v1')]),prefs);
  await page.locator('#scan-toggle').click();await page.locator('#scan-terms').fill('lighting report');await page.waitForFunction(()=>document.querySelectorAll('#cards mark').length>0);
  await page.locator('#scan-next').click();assert.ok(await page.locator('#reader-KMEM-18L mark').count()>0);
  for(const bad of ['bad','synthetic']){mode=bad;await page.evaluate(()=>notamSnapshots.refresh());assert.match(await page.locator('.notam-reader').innerText(),/retained snapshot/);}
  mode='good';await page.locator('[data-notam-reload]').click();await page.waitForFunction(()=>notamTransport&&!notamTransport.refreshing&&!notamTransport.error);
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.notamReload),'MEM');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  mkdirSync('/tmp/spatchin-snapshot-review',{recursive:true});await page.screenshot({path:`/tmp/spatchin-snapshot-review/${width}.png`,fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 }finally{await context.close();}
});
test('first-load 404 is unavailable; absent MEM inventory does not fabricate runway ends',async()=>{
 const context=await browser.newContext(),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 await context.route('**/data/notams.json',route=>route.fulfill({status:404,body:''}));
 try{await page.goto(base);await page.waitForFunction(()=>notamTransport?.error);assert.match(await page.locator('.notam-reader').innerText(),/NOTAM data unavailable/);assert.equal(await page.locator('.rw-table tbody tr').count(),0);assert.match(await page.locator('#cards').innerText(),/Runway data pending/);}finally{await context.close();}
});
