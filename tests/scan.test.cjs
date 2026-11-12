// Run: node --test tests/scan.test.cjs (Playwright + installed Chromium required).
const {test, before, after, beforeEach, afterEach} = require('node:test');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {mkdtempSync} = require('node:fs');
const {tmpdir} = require('node:os');
const {join} = require('node:path');
const {chromium} = require('playwright');
const screenshots = mkdtempSync(join(tmpdir(), 'spatchin-scan-'));
let browser, server, context, page, errors, external;
const url = 'http://127.0.0.1:8766';
before(async () => {
  server = spawn('python3', ['-m', 'http.server', '8766', '--bind', '127.0.0.1', '--directory', process.cwd()], {stdio:'ignore'});
  for (let i=0;i<50;i++) {try {if ((await fetch(url)).ok) break;} catch {} await new Promise(r=>setTimeout(r,100));}
  browser = await chromium.launch({...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {}), headless:true});
});
after(async () => {await browser?.close(); server?.kill();});
beforeEach(async () => {
  context = await browser.newContext({viewport:{width:1440,height:1000}});
  page = await context.newPage(); errors=[]; external=[];
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin === url) return route.continue();
    external.push(route.request().url());return route.abort();
  });
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);
  // Exercise existing aviation fixtures as a saved selection, independent of defaults.
  await page.evaluate(()=>localStorage.setItem('spatchin-airports',JSON.stringify(['ORD','LAX','JFK'])));
  await page.reload();
  await page.waitForFunction(()=>document.querySelectorAll('.rw-table tbody tr').length===32 && document.querySelector('.reading').textContent!=='—');
});
afterEach(async()=>{await context.close();assert.deepEqual(errors,[]);assert.deepEqual(external,[],'No external requests');});
async function search(value) {
  if (await page.locator('#scan-panel').isHidden()) await page.locator('#scan-toggle').click();
  await page.locator('#scan-terms').fill(value);
}
async function fixture(html) {await page.evaluate(html=>{const el=document.createElement('section');el.id='fixture';el.innerHTML=html;document.querySelector('main').append(el);},html);}
async function count(n, selector='#fixture mark') {await page.waitForFunction(([s,n])=>document.querySelectorAll(s).length===n,[selector,n]);}

test('fresh storage uses MEM only without writing preferences or changing SCAN',async()=>{
  // Explicit offline fixture: this case has no MEM observation, regardless of published data.
  await page.route('**/data/metars.json?*',route=>route.fulfill({
    json:{updated_utc:new Date().toISOString(),metars:[]},
  }));
  await page.evaluate(()=>{
    localStorage.removeItem('spatchin-airports');
    localStorage.removeItem('niltsi-airports');
    localStorage.setItem('spatchin-scan-v1',JSON.stringify({keywords:'MEM',enabled:true,caseSensitive:false,expanded:false,colors:{MEM:'#ffd166'}}));
  });
  const scan=await page.evaluate(()=>localStorage.getItem('spatchin-scan-v1'));
  await page.reload();
  await page.waitForFunction(()=>!loading && metars.MEM?.error==='No valid METAR returned');
  assert.deepEqual(await page.locator('.icao').allTextContents(),['MEM']);
  assert.equal(await page.evaluate(()=>icao('MEM')),'KMEM');
  assert.equal(await page.locator('.metar-status').textContent(),'METAR UNAVAILABLE');
  assert.match(await page.locator('#cards').textContent(),/Runway data pending/);
  assert.equal(await page.evaluate(()=>localStorage.getItem('spatchin-airports')),null);
  assert.equal(await page.evaluate(()=>localStorage.getItem('niltsi-airports')),null);
  assert.equal(await page.evaluate(()=>localStorage.getItem('spatchin-scan-v1')),scan);
});

test('KMEM observation loads through refresh and renders on the MEM card',async()=>{
  const observed = Math.floor(Date.now()/1000);
  const raw = 'KMEM OFFLINE TEST OBSERVATION';
  let requests = 0;
  // Test-only response: never written to published data or obtained from a provider.
  await page.route('**/data/metars.json?*',route=>{
    requests++;
    return route.fulfill({json:{updated_utc:new Date().toISOString(),metars:[
      {icaoId:'KMEM',obsTime:observed,wdir:180,wspd:12,wgst:20,rawOb:raw},
      {icaoId:'KORD',obsTime:observed,wdir:90,wspd:3,rawOb:'ORD TEST'},
    ]}});
  });
  await page.evaluate(()=>{
    localStorage.removeItem('spatchin-airports');
    localStorage.removeItem('niltsi-airports');
  });
  await page.reload();
  const card=page.locator('.airport');
  await page.waitForFunction(()=>document.querySelector('.reading')?.textContent==='180° / 12G20KT');
  assert.ok(requests>0,'Startup refresh fetched same-origin METAR JSON');
  assert.equal(await card.count(),1);
  assert.equal(await card.locator('.icao').textContent(),'MEM');
  assert.equal(await card.locator('.name').textContent(),'Memphis International Airport');
  assert.equal(await card.locator('.metar-status').textContent(),'METAR CURRENT');
  assert.match(await card.locator('.details').textContent(),/KMEM OFFLINE TEST OBSERVATION/);
  assert.equal(await card.locator('.details a').first().getAttribute('href'),
    'https://aviationweather.gov/api/data/metar?ids=KMEM&format=raw');
  await page.evaluate(()=>refreshAtis());
  assert.equal(await card.locator('.atis-provider').textContent(),'atis.info · STALE / UNVERIFIED');
  assert.match(await card.locator('.atis-full').textContent(),/ATIS not available\./);
  assert.equal(await card.locator('.atis-raw').count(),0);
  assert.equal(await page.evaluate(()=>localStorage.getItem('spatchin-airports')),null);
});

test('literal matching, case toggle, whitespace, duplicates and overlap priority',async()=>{
  await fixture('<p>Alpha alpha ALPHA a+b aab runway</p>');
  await search(' Alpha, alpha, a+b, run, runway, ,');await count(5);
  assert.deepEqual(await page.locator('#fixture mark').allTextContents(),['Alpha','alpha','ALPHA','a+b','runway']);
  await page.locator('#scan-case').check();await count(4);
  assert.deepEqual(await page.locator('#fixture mark').allTextContents(),['Alpha','alpha','a+b','runway']);
});
test('distinct stable colors, saved keywords/preferences, master switch',async()=>{
  await fixture('<p>Alpha Bravo</p>');await search('Alpha, Bravo');await count(2);
  const colors=await page.locator('#fixture mark').evaluateAll(ms=>ms.map(m=>m.style.backgroundColor));assert.notEqual(colors[0],colors[1]);
  await page.locator('#scan-case').check();await page.reload();
  assert.equal(await page.locator('#scan-terms').inputValue(),'Alpha, Bravo');assert.equal(await page.locator('#scan-case').isChecked(),true);
  assert.deepEqual(await page.locator('.scan-chip').evaluateAll(ms=>ms.map(m=>m.style.backgroundColor)),colors);
  await page.locator('#scan-enabled').uncheck();await count(0,'mark[data-scan-match]');
  await page.reload();assert.equal(await page.locator('#scan-enabled').isChecked(),false);assert.equal(await page.locator('#scan-terms').inputValue(),'Alpha, Bravo');
  await page.locator('#scan-enabled').check();await fixture('<p>Alpha Bravo</p>');await count(2);
});
test('navigation wraps, counts, reveals native and custom disclosures, preserves keyboard focus',async()=>{
  await fixture('<details><summary>Report</summary><div hidden id="custom">ZEBRA</div></details><button aria-controls="custom" aria-expanded="false">Reveal</button><p>ZEBRA</p>');
  await search('ZEBRA');await count(2);await page.locator('#scan-terms').press('Enter');
  assert.equal(await page.locator('#scan-count').textContent(),'1 / 2 matches');
  assert.equal(await page.locator('#fixture details').getAttribute('open'),'');assert.equal(await page.locator('#custom').isVisible(),true);
  assert.equal(await page.locator('[aria-controls="custom"]').getAttribute('aria-expanded'),'true');assert.equal(await page.locator('#scan-terms').evaluate(e=>e===document.activeElement),true);
  await page.locator('#scan-terms').press('Shift+Enter');assert.equal(await page.locator('#scan-count').textContent(),'2 / 2 matches');
  await page.locator('#scan-next').click();assert.equal(await page.locator('#scan-count').textContent(),'1 / 2 matches');
});
test('dynamic text, replacement, removal and future panels are observed without hooks',async()=>{
  await search('ZEBRA');await fixture('<h3>NOTAM / TAF</h3><p>ZEBRA</p>');await count(1);
  await page.locator('#fixture p').evaluate(el=>{el.textContent='ZEBRA ZEBRA';});await count(2);
  await page.locator('#fixture').evaluate(el=>el.replaceChildren(document.createTextNode('ZEBRA')));await count(1);
  await page.locator('#fixture').evaluate(el=>el.remove());await count(0,'mark[data-scan-match]');
  await page.waitForFunction(()=>document.getElementById('scan-next').disabled);
});
test('safe DOM highlighting preserves links, listeners, inputs and editable content',async()=>{
  await fixture('<p>&lt;img src=x onerror=alert(1)&gt; ZEBRA</p><a href="#fixture">ZEBRA</a><button>ZEBRA</button><input value="ZEBRA"><div contenteditable="true">ZEBRA</div>');
  await page.locator('#fixture button').evaluate(el=>{el.addEventListener('click',()=>el.dataset.clicked='yes');});
  await search('<img src=x onerror=alert(1)>, ZEBRA');await count(4);
  assert.equal(await page.locator('#fixture img').count(),0);assert.equal(await page.locator('#fixture a').getAttribute('href'),'#fixture');
  await page.locator('#fixture button').click();assert.equal(await page.locator('#fixture button').getAttribute('data-clicked'),'yes');
  assert.equal(await page.locator('#fixture input').inputValue(),'ZEBRA');assert.equal(await page.locator('[contenteditable] mark').count(),0);
  await search('');await count(0);assert.match(await page.locator('#fixture p').textContent(),/^<img/);
});
test('airport, METAR, ATIS and runway behavior survives highlights and data refresh',async()=>{
  await page.waitForFunction(()=>document.querySelectorAll('.atis-raw').length>0 && document.querySelector('.rw-table td[title]').title.startsWith('FAA true alignment'));
  const dashboard = () => page.locator('#cards').evaluate(el=>({
    text:el.textContent,
    runways:[...el.querySelectorAll('.rw-table tbody tr')].map(row=>[...row.cells].map(cell=>({text:cell.textContent,title:cell.title})))
  }));
  const baseline = await dashboard();
  await search('ORD, METAR, ATIS, 04L');await page.waitForFunction(()=>document.querySelectorAll('#cards mark').length>10);
  assert.deepEqual(await dashboard(),baseline,'Highlighting preserves dashboard text and runway values');
  await page.locator('[data-expand="ORD"]').click();assert.equal(await page.locator('#detail-ORD').isVisible(),true);
  await page.locator('#iata').fill('ATL');await page.locator('#add-form button').click();await page.waitForFunction(()=>document.querySelectorAll('.airport').length===4);
  await page.locator('[data-expand="ATL"]').click();await page.locator('[data-remove="ATL"]').click();
  await page.evaluate(()=>Promise.all([refresh(),refreshAtis(),refreshRunways()]));
  await page.waitForFunction(()=>document.querySelectorAll('.airport').length===3 && document.querySelectorAll('#cards mark').length>10);
  assert.equal(await page.locator('.rw-table tbody tr').count(),32);assert.match(await page.locator('.reading').first().textContent(),/KT/);
  await page.locator('#scan-enabled').uncheck();assert.equal(await page.locator('#cards mark').count(),0);
  assert.deepEqual(await dashboard(),baseline,'Airport changes and local refreshes preserve original aviation data');
});
test('navigation expands the existing airport details control',async()=>{
  await search('Last successful retrieval');await page.waitForFunction(()=>document.querySelectorAll('#cards mark').length===3);
  await page.locator('#scan-next').click();assert.equal(await page.locator('#detail-ORD').isVisible(),true);assert.equal(await page.locator('[data-expand="ORD"]').getAttribute('aria-expanded'),'true');
  await page.locator('[data-expand="ORD"]').click();assert.equal(await page.locator('#detail-ORD').isVisible(),false);
});
test('corrupt or denied storage does not prevent operation',async()=>{
  await page.evaluate(()=>localStorage.setItem('spatchin-scan-v1','{broken'));await page.reload();await search('METAR');assert.ok(await page.locator('mark').count()>0);
  await page.addInitScript(()=>{Storage.prototype.getItem=()=>{throw Error('denied')};Storage.prototype.setItem=()=>{throw Error('denied')};});
  await page.reload();await search('ATIS');assert.ok(await page.locator('mark').count()>0);
});
test('dark dashboard screenshots and narrow-screen toolbar',async()=>{
  await search('METAR, ATIS, 04L');await page.screenshot({path:join(screenshots,'scan-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('#scan-toolbar').evaluate(el=>el.scrollWidth<=el.clientWidth),true);
  await page.screenshot({path:join(screenshots,'scan-mobile.png'),fullPage:true});
});

test('zero results, Unicode, punctuation, and more than six keyword colors',async()=>{
  await fixture('<p>Ångström ångström [x] .* a b c d e f g h</p>');
  await search('ångström, [x], .*, a, b, c, d, e, f, g, h');
  await page.waitForFunction(()=>document.querySelectorAll('.scan-chip').length===11);
  const colors=await page.locator('.scan-chip').evaluateAll(ms=>ms.map(m=>m.style.backgroundColor));assert.equal(new Set(colors).size,11);
  assert.equal(await page.locator('#fixture mark').filter({hasText:/^ångström$/i}).count(),2);
  await search('NEVER_PRESENT_12345');assert.equal(await page.locator('#scan-count').textContent(),'0 / 0 matches');assert.equal(await page.locator('#scan-next').isDisabled(),true);
});

test('toolbar defaults collapsed and supports keyboard expansion with hidden controls out of tab order',async()=>{
  const toggle=page.locator('#scan-toggle');
  assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  assert.equal(await page.locator('#scan-panel').isHidden(),true);
  assert.equal(await page.locator('#scan-keyword-count').textContent(),'0 active keywords');
  await toggle.focus();await toggle.press('Tab');
  assert.equal(await page.locator('#scan-panel').evaluate(el=>el.contains(document.activeElement)),false);
  await toggle.focus();await toggle.press('Enter');
  assert.equal(await toggle.getAttribute('aria-expanded'),'true');
  await toggle.press('Tab');assert.equal(await page.locator('#scan-terms').evaluate(el=>el===document.activeElement),true);
  await toggle.focus();await toggle.press('Space');
  assert.equal(await page.locator('#scan-panel').isHidden(),true);
  assert.equal(await toggle.evaluate(el=>el===document.activeElement),true);
});

test('collapse preserves highlights, selected match, preferences and dashboard controls',async()=>{
  await fixture('<p>ZEBRA ZEBRA</p>');await search('ZEBRA, zebra, ,');await count(2);
  await page.locator('#scan-next').click();
  await page.locator('#fixture mark').first().evaluate(el=>window.selectedScanMark=el);
  assert.equal(await page.locator('#scan-keyword-count').textContent(),'1 active keyword');
  await page.locator('#scan-toggle').click();
  assert.equal(await page.locator('#fixture mark').count(),2);
  assert.equal(await page.locator('#fixture mark.scan-current').evaluate(el=>el===window.selectedScanMark),true);
  await page.locator('[data-expand="ORD"]').click();assert.equal(await page.locator('#detail-ORD').isVisible(),true);
  await page.locator('#scan-toggle').click();
  assert.equal(await page.locator('#scan-count').textContent(),'1 / 2 matches');
  await page.locator('#scan-next').click();assert.equal(await page.locator('#scan-count').textContent(),'2 / 2 matches');
  await page.locator('#scan-case').check();assert.equal(await page.locator('#scan-keyword-count').textContent(),'2 active keywords');
  await page.locator('#scan-enabled').uncheck();assert.equal(await page.locator('#scan-keyword-count').textContent(),'0 active keywords');
  await page.locator('#scan-toggle').click();await page.reload();
  assert.equal(await page.locator('#scan-panel').isHidden(),true);
  await page.locator('#scan-toggle').click();
  assert.equal(await page.locator('#scan-terms').inputValue(),'ZEBRA, zebra, ,');
  assert.equal(await page.locator('#scan-case').isChecked(),true);
  assert.equal(await page.locator('#scan-enabled').isChecked(),false);
});

test('saved searches highlight while collapsed and older preferences migrate safely',async()=>{
  await page.evaluate(()=>localStorage.setItem('spatchin-scan-v1',JSON.stringify({keywords:'METAR, ATIS',enabled:true,caseSensitive:false,colors:{METAR:'#ffd166'}})));
  await page.reload();
  assert.equal(await page.locator('#scan-panel').isHidden(),true);
  await page.waitForFunction(()=>document.querySelectorAll('#cards mark').length>3);
  assert.equal(await page.locator('#scan-keyword-count').textContent(),'2 active keywords');
  await page.locator('#scan-toggle').click();await page.reload();
  assert.equal(await page.locator('#scan-panel').isVisible(),true);
  assert.equal(await page.locator('#scan-terms').inputValue(),'METAR, ATIS');
});
