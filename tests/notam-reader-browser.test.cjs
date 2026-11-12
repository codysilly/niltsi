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
for(const width of [1440,390])test(`synthetic reader, focus, SCAN and unavailable states at ${width}px`,async()=>{
 const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),external=[],errors=[];
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin===base)return route.continue();external.push(route.request().url());return route.abort();});
 page.on('pageerror',e=>errors.push(e.message));
 try{
 await page.goto(base+'/preview/');
 await page.waitForSelector('.rw-table tbody tr');
 assert.equal(await page.locator('.rw-table tbody tr').count(),8);
 assert.match(await page.locator('.synthetic-banner').innerText(),/SYNTHETIC — NO LIVE DATA/);
 assert.equal(await page.evaluate(()=>localStorage.getItem('spatchin-airports')),null);
 const b=page.locator('[data-reader-open="KMEM-18L"]');assert.match(await b.innerText(),/\(2\)/);
 await b.click();assert.equal(await page.locator('#reader-KMEM-18L').getAttribute('open'),'');
 await page.evaluate(()=>{render();});
 assert.equal(await page.evaluate(()=>document.activeElement.parentElement.id),'reader-KMEM-18L');
 assert.equal(await page.locator('#reader-KMEM-18L').getAttribute('open'),'');
 const meta=page.locator('#reader-KMEM-18L .reader-notice details').first();await meta.locator('summary').click();
 await page.evaluate(()=>render());assert.equal(await meta.getAttribute('open'),'');
 assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Preserved source metadata');
 await b.focus();await page.evaluate(()=>render());assert.equal(await page.evaluate(()=>document.activeElement.dataset.readerOpen),'KMEM-18L');
 // Collapse every reader disclosure; SCAN must find mounted text and reveal ancestors.
 await page.evaluate(()=>document.querySelectorAll('.notam-reader details').forEach(el=>el.open=false));
 await page.locator('#scan-toggle').click();await page.locator('#scan-terms').fill('lighting report');
 await page.waitForFunction(()=>document.querySelectorAll('.notam-reader mark').length>0);
 await page.locator('#scan-next').click();
 assert.ok(await page.locator('.notam-reader details[open]').count()>0);
 // Dynamic refresh remains searchable and preserves raw time tokens.
 await page.evaluate(()=>{SPATCHIN_SYNTHETIC_PREVIEW.notams.airports.KMEM.notices[0].text+=' DYNAMICREADER';render();});
 await page.locator('#scan-terms').fill('DYNAMICREADER');await page.waitForFunction(()=>document.querySelectorAll('.notam-reader mark').length>0);
 await page.locator('#scan-next').click();
 assert.equal(await page.locator('.notam-reader b').count(),0);
 assert.match(await page.locator('#reader-KMEM-all').textContent(),/PERM/);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 mkdirSync('/tmp/spatchin-reader-review',{recursive:true});
 await page.evaluate(()=>{document.querySelectorAll('.notam-reader details').forEach(el=>el.open=false);document.getElementById('reader-KMEM-18L').open=true;});
 await page.screenshot({path:`/tmp/spatchin-reader-review/${width}.png`,fullPage:true});
 await page.selectOption('#preview-state','stale');assert.match(await page.locator('.notam-reader').innerText(),/source reports STALE/);
 await page.selectOption('#preview-state','error');assert.match(await page.locator('.notam-reader').innerText(),/refresh failed/);
 await page.selectOption('#preview-state','unavailable');assert.match(await page.locator('.notam-reader').innerText(),/NOTAM data unavailable/);
 assert.equal(await page.locator('.rw-table tbody tr').count(),8);
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 }finally{await context.close();}
});
test('production entry never loads synthetic fixtures',async()=>{
 const context=await browser.newContext(),page=await context.newPage();
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
 try{await page.goto(base);await page.waitForSelector('.notam-reader');assert.equal(await page.evaluate(()=>typeof SPATCHIN_SYNTHETIC_PREVIEW),'undefined');assert.match(await page.locator('.notam-reader').innerText(),/NOTAM data unavailable/);}finally{await context.close();}
});
