'use strict';
const {test,before,after}=require('node:test'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),{chromium}=require('playwright');
const longNotices=require('./fixtures/ui-long-notams.cjs');
const base='http://127.0.0.1:8773';let server,browser;
before(async()=>{server=spawn('python3',['-B','-m','http.server','8773','--bind','127.0.0.1'],{stdio:'ignore'});for(let i=0;i<50;i++){try{if((await fetch(base)).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH,headless:true});});
after(async()=>{await browser?.close();server?.kill();});
for(const width of [1440,390])test(`bounded panels, lower SCAN targets, diagram provenance and consolidated warnings at ${width}px`,async()=>{
 const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),external=[],errors=[];
 await context.route('**/*',r=>{if(new URL(r.request().url()).origin===base)return r.continue();external.push(r.request().url());return r.abort();});
 await context.addInitScript(()=>localStorage.setItem('spatchin-airports','["MEM","LAX"]'));
 page.on('pageerror',e=>errors.push(e.message));
 const warning='SYNTHETIC supplied collection/legal warning <unsafe>. Preserve verbatim.',notices=longNotices(),expected=notices.flatMap(n=>[n.text,...n.formatted_texts.map(t=>t.text)]);
 let failed=false;
 await context.route('**/data/notams.json',r=>failed?r.fulfill({status:503,body:'suppressed upstream body'}):r.fulfill({json:{retrieved_utc:'2020-01-01T00:00:00Z',airports:{KMEM:{completeness:'UNKNOWN',warning,notices:[...notices,{number:'12/1',text:'SYNTHETIC RWY 18R independent disclosure'}]},KLAX:{completeness:'UNKNOWN',notices:[]}}}}));
 try{
  await page.goto(base);await page.waitForFunction(()=>notamTransport?.snapshot&&ends('MEM').length===8);
  const link=page.locator('[data-airport="MEM"] .airport-head a');
  assert.equal(await link.getAttribute('href'),'https://aeronav.faa.gov/d-tpp/2611/00253AD.PDF');
  assert.equal(await link.getAttribute('target'),'_blank');assert.equal(await link.getAttribute('rel'),'noopener');
  assert.match(await link.textContent(),/Airport diagram.*↗/);assert.match(await page.locator('.airport-diagram small').innerText(),/2026-10-29 through 2026-11-26/);
  assert.equal(await page.locator('[data-airport="LAX"] .airport-head a').count(),0);
  assert.deepEqual(await page.evaluate(()=>['ORD','LAX','JFK','IND'].map(c=>diagramHeader(c))),['','','','']);
  assert.equal(await page.evaluate(()=>{runwayData.airport_metadata={LAX:{heading_method:'chart-derived-approximate',source_url:runwayData.source_url}};const html=diagramHeader('LAX');delete runwayData.airport_metadata;return html;}),'');
  assert.equal(await page.locator('.notam-summary').count(),2);assert.equal(await page.locator('.notam-reader .notam-summary').count(),0);
  const summary=page.locator('#coverage-KMEM');assert.match(await summary.innerText(),/Completeness: UNKNOWN/);assert.match(await summary.innerText(),/Historical snapshot; no automatic reader updates/);
  assert.equal(await summary.locator('.collection-warning').textContent(),warning);assert.equal(await page.locator('unsafe').count(),0);
  assert.equal(await page.locator('.reader-note').filter({hasText:'UNKNOWN'}).count(),0);
  const toggle=page.locator('[data-reader-open="KMEM-36L"]'),region=page.locator('#scroll-KMEM-36L');
  assert.equal(await toggle.getAttribute('aria-describedby'),'coverage-KMEM');
  await page.locator('[data-reader-open="KLAX-06L"]').click();await page.locator('[data-reader-open="KMEM-18R"]').click();
  await toggle.focus();await page.keyboard.press('Enter');
  assert.equal(await region.getAttribute('role'),'region');assert.equal(await region.getAttribute('tabindex'),'0');
  assert.equal(await region.getAttribute('aria-label'),'KMEM runway 36L source notices');
  assert.equal(await region.evaluate(el=>el.closest('tr').previousElementSibling.querySelector('td').textContent),'36L');
  assert.equal(await region.locator('.reader-notice').count(),32);assert.deepEqual(await region.locator('.reader-raw').allTextContents(),expected);
  assert.ok(await region.evaluate(el=>el.scrollHeight>el.clientHeight*8&&el.clientHeight<=innerHeight*.66));
  await region.focus();await page.keyboard.press('PageDown');await page.waitForFunction(()=>document.querySelector('#scroll-KMEM-36L').scrollTop>0);
  assert.equal(await region.evaluate(async el=>{let last=el.scrollTop,stable=0;for(let i=0;i<120&&stable<6;i++){await new Promise(requestAnimationFrame);stable=el.scrollTop===last?stable+1:0;last=el.scrollTop;}return stable;}),6);
  await region.evaluate(el=>el.scrollTop=el.scrollHeight*.6);const prior=await region.evaluate(el=>el.scrollTop);
  await page.evaluate(()=>render());assert.equal(await page.evaluate(()=>document.activeElement.id),'scroll-KMEM-36L');
  assert.ok(Math.abs(await region.evaluate(el=>el.scrollTop)-prior)<=2,`Scroll before ${prior}; after ${await region.evaluate(el=>el.scrollTop)}`);
  await toggle.click();assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  await page.locator('#scan-toggle').click();await page.locator('#scan-terms').fill('LOWER_MATCH_A, LOWER_MATCH_B');
  await page.waitForFunction(()=>document.querySelectorAll('#scroll-KMEM-36L mark').length===2);
  const visible=async token=>{
   assert.equal(await page.locator('.scan-current').textContent(),token);
   assert.equal(await page.locator('.scan-current').evaluate(el=>el.closest('[data-reader-scroll]')?.id),'scroll-KMEM-36L');
   assert.ok(await page.locator('.scan-current').evaluate(el=>{const r=el.getBoundingClientRect(),panel=el.closest('[data-reader-scroll]'),p=panel.getBoundingClientRect(),title=panel.querySelector('.reader-scroll-title').getBoundingClientRect();return r.top>=Math.max(0,title.bottom)&&r.bottom<=Math.min(innerHeight,p.bottom)&&panel.scrollTop>(panel.scrollHeight-panel.clientHeight)*.8;}));
  };
  await page.locator('#scan-next').click();await visible('LOWER_MATCH_A');assert.equal(await toggle.getAttribute('aria-expanded'),'true');
  await page.locator('#scan-next').click();await visible('LOWER_MATCH_B');
  await page.locator('#scan-prev').click();await visible('LOWER_MATCH_A');
  for(const key of ['KMEM-18R','KLAX-06L'])assert.equal(await page.locator(`[data-reader-open="${key}"]`).getAttribute('aria-expanded'),'true');
  const scanScroll=await region.evaluate(el=>el.scrollTop);await region.focus();await page.evaluate(()=>render());
  await page.waitForFunction(()=>document.querySelector('#scroll-KMEM-36L .scan-current'));
  assert.equal(await page.evaluate(()=>document.activeElement.id),'scroll-KMEM-36L');assert.ok(Math.abs(await region.evaluate(el=>el.scrollTop)-scanScroll)<40);
  assert.deepEqual(await region.locator('.reader-raw').allTextContents(),expected);assert.equal(await page.locator('mark mark').count(),0);
  await page.locator('#scan-terms').fill('');await page.waitForFunction(()=>!document.querySelector('mark[data-scan-match]'));
  assert.deepEqual(await region.locator('.reader-raw').allTextContents(),expected);
  failed=true;await page.evaluate(()=>notamSnapshots.refresh());assert.match(await summary.innerText(),/HTTP 503/);assert.match(await summary.innerText(),/Showing retained snapshot/);
  assert.equal(await summary.locator('.collection-warning').textContent(),warning);assert.deepEqual(await region.locator('.reader-raw').allTextContents(),expected);
  failed=false;await page.evaluate(()=>notamSnapshots.refresh());assert.doesNotMatch(await summary.innerText(),/HTTP 503|Showing retained snapshot/);assert.equal(await summary.locator('.collection-warning').textContent(),warning);
  if(width===390){
   for(const selector of ['.reader-note','.reader-raw','.notice-provenance summary','.notam-summary','.time-control label','.atis-provider'])assert.ok(await page.locator(selector).first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize)>=12),selector);
   for(const selector of ['[data-reader-open]','#time-MEM','#scan-toggle','#scan-next','#add-form button','[data-expand]'])assert.ok((await page.locator(selector).first().boundingBox()).height>=44,selector);
  }
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  // Explicit local verification is distinct from missing, empty-unknown and historical data.
  await page.evaluate(()=>{NotamReader.set({retrieved_utc:'2020-01-01T00:00:00Z',airports:{KMEM:{notices:[],completeness:'VERIFIED',zero_verified:true,verification_reference:'SYNTHETIC evidence'}}});render();});
  assert.match(await summary.innerText(),/Explicitly verified zero/);assert.match(await page.locator('#coverage-KLAX').innerText(),/NOTAM data unavailable/);
  assert.equal(await toggle.getAttribute('aria-describedby'),'coverage-KMEM');assert.equal(await page.locator('.rw-table > tbody > .runway-row').count(),16);
 }finally{await context.close();}
});
