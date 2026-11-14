'use strict';
const {test,before,after}=require('node:test'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const {readFileSync,mkdtempSync}=require('node:fs'),{tmpdir}=require('node:os'),{join}=require('node:path'),{createHash}=require('node:crypto');
const longNotices=require('./fixtures/ui-long-notams.cjs');
const base='http://127.0.0.1:8771',screenshots=process.env.SPATCHIN_SCREENSHOTS||mkdtempSync(join(tmpdir(),'spatchin-dashboard-'));let server,browser;
const hash=p=>createHash('sha256').update(readFileSync(p)).digest('hex');
before(async()=>{server=spawn('python3',['-B','-m','http.server','8771','--bind','127.0.0.1'],{stdio:'ignore'});for(let i=0;i<50;i++){try{if((await fetch(base)).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH,headless:true});});
after(async()=>{await browser?.close();server?.kill();});
test('version-controlled originals match owner-approved fingerprints',()=>{
 assert.equal(hash('assets/branding/spatchin-logo-source.png'),'ad5e051cfbfc48cfd5faf7c7c4f5048adb683039c9a8319a60840fc485a00144');
 assert.equal(hash('docs/reference/dashboard-reference.png'),'8380cbe3c991e1af1259294fffc2ab90bd538d6be74a1f798b47a5e3d9ca4c09');
});
async function localPage(width){const context=await browser.newContext({viewport:{width,height:1200}}),page=await context.newPage(),external=[],errors=[];
 await context.route('**/*',r=>{if(new URL(r.request().url()).origin===base)return r.continue();external.push(r.request().url());return r.abort();});page.on('pageerror',e=>errors.push(e.message));return {context,page,external,errors};}
for(const width of [1440,390])test(`actual app synthetic visual review and branding at ${width}px`,async()=>{
 const {context,page,external,errors}=await localPage(width),now=new Date().toISOString();
 const notice={number:'11/207',id:'SYNTHETIC-ONLY',source:'Synthetic test fixture, not a provider record',text:'SYNTHETIC RWY 18R/36L lighting report\r\nSCANDEMO — illustrative text only. No operational interpretation.'};
 await context.route('**/data/notams.json',r=>r.fulfill({json:{retrieved_utc:now,airports:Object.fromEntries(['KMEM','KLAX','KJFK'].map(c=>[c,{completeness:'UNKNOWN',warning:'SYNTHETIC DEMONSTRATION — coverage UNKNOWN; no real provider data.',notices:c==='KMEM'?longNotices():[{...notice,text:c==='KLAX'?'SYNTHETIC RWY 06L/24R demonstration only.':'SYNTHETIC RWY 04L/22R demonstration only.'}]}]))}}));
 await context.route('**/data/metars.json?*',r=>r.fulfill({json:{updated_utc:now,metars:['KMEM','KLAX','KJFK'].map(icaoId=>({icaoId,obsTime:now,wdir:250,wspd:12,wgst:18,rawOb:'SYNTHETIC observation — test fixture only'}))}}));
 await context.route('**/data/atis.json?*',r=>r.fulfill({json:{retrieved_utc:now,airports:Object.fromEntries(['KMEM','KLAX','KJFK'].map(c=>[c,[{type:'combined',code:'S',time:'1200',updatedAt:now,datis:`SYNTHETIC ONLY. LANDING RWY ${c==='KMEM'?'18R':c==='KLAX'?'06L':'04L'}. DEP EXP RWYS ${c==='KMEM'?'36L':c==='KLAX'?'24R':'22R'}. Demonstration; not an airport configuration.`}]]))}}));
 await context.addInitScript(()=>localStorage.setItem('spatchin-airports','["MEM","LAX","JFK"]'));
 try{
  await page.goto(base);await page.waitForFunction(()=>notamTransport?.snapshot&&ends('MEM').length===8&&metars.MEM?.wind);
  await page.evaluate(()=>{const label=document.createElement('div');label.className='synthetic-banner';label.textContent='SYNTHETIC VISUAL TEST — NOT LIVE AVIATION DATA';document.querySelector('.wip-banner').after(label);});
  assert.equal(await page.locator('.wip-banner').innerText(),'WORK IN PROGRESS — Spatchin is not ready for actual use.');
  assert.match(await page.title(),/v0\.9$/);assert.match(await page.locator('.version').innerText(),/v0\.9/);
  assert.equal(await page.locator('.brand-logo').evaluate(el=>el.complete&&el.naturalWidth>0&&getComputedStyle(el).objectFit==='contain'),true);
  const sizes=await page.evaluate(()=>Promise.all([...document.querySelectorAll('link[rel="icon"],link[rel="apple-touch-icon"]')].map(el=>new Promise(resolve=>{const image=new Image();image.onload=()=>resolve(image.naturalWidth>0);image.onerror=()=>resolve(false);image.src=el.href;}))));
  assert.deepEqual(sizes,[true,true,true]);
  const columns=await page.locator('.cards').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length);assert.equal(columns,width===1440?3:1);
  assert.equal(await page.locator('.airport').count(),3);assert.equal(await page.locator('[data-observed-time]').count(),3);
  await page.locator('[data-reader-open="KMEM-36L"]').click();
  await page.screenshot({path:join(screenshots,`synthetic-${width}.png`),fullPage:true});
  await page.locator('#scan-toggle').click();await page.locator('#scan-terms').fill('LOWER_MATCH_A, LOWER_MATCH_B');await page.waitForFunction(()=>document.querySelector('#cards mark'));await page.locator('#scan-next').click();await page.locator('#scan-next').click();
  assert.equal(await page.locator('.scan-current').textContent(),'LOWER_MATCH_B');
  assert.ok(await page.locator('.scan-current').evaluate(el=>{const r=el.getBoundingClientRect(),panel=el.closest('[data-reader-scroll]'),p=panel.getBoundingClientRect();return r.top>=Math.max(0,p.top)&&r.bottom<=Math.min(innerHeight,p.bottom)&&panel.scrollTop>(panel.scrollHeight-panel.clientHeight)*.8;}));
  assert.equal(await page.locator('mark mark').count(),0);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:join(screenshots,`synthetic-scan-${width}.png`),fullPage:true});
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
 }finally{await context.close();}
});
test('unchanged public historical MEM snapshot retains all 100 records and 131 representations',async()=>{
 const {context,page,external,errors}=await localPage(1440),data=JSON.parse(readFileSync('data/notams.json','utf8'));
 try{
  await page.goto(base);await page.waitForFunction(()=>notamTransport?.snapshot&&ends('MEM').length===8);
  const records=await page.evaluate(()=>NotamReader.records('KMEM',ends('MEM')).map(n=>({key:n.key,text:n.text,formatted_texts:n.formatted_texts})));
  assert.equal(records.length,100);assert.equal(records.reduce((n,r)=>n+(r.formatted_texts||[]).length,0),131);
  for(const record of records){const original=data.airports.KMEM.notices[Number(record.key.split('-')[1])];assert.equal(record.text,original.text);assert.deepEqual(record.formatted_texts,original.formatted_texts);}
  await page.locator('#reader-KMEM-all > summary').click();assert.equal(await page.locator('#reader-KMEM-all .reader-notice').count(),100);
  for(const record of records){const source=page.locator(`#reader-KMEM-all [data-reader-key="${record.key}"] .reader-raw`);assert.deepEqual(await source.allTextContents(),[record.text||'Original text unavailable',...(record.formatted_texts||[]).map(t=>t.text)]);}
  await page.locator('#reader-KMEM-all > summary').click();
  assert.match(await page.locator('.notam-summary').innerText(),/2026-11-13T02:52:31\.040326\+00:00/);
  assert.match(await page.locator('.notam-summary').innerText(),/UNKNOWN/);assert.match(await page.locator('.notam-summary').innerText(),/no automatic updates/i);
  assert.equal(await page.locator('.collection-warning').textContent(),data.airports.KMEM.warning);
  await page.locator('[data-reader-open="KMEM-36L"]').click();await page.screenshot({path:join(screenshots,'public-historical-mem-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:1200});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:join(screenshots,'public-historical-mem-mobile.png'),fullPage:true});
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
 }finally{await context.close();}
});
