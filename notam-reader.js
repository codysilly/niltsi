/* Reading only: text references do not establish applicability or availability. */
'use strict';
const NotamReader = (() => {
 // Character references avoid HTML's CR/CRLF normalization of source text.
 const esc=s=>String(s??'Not supplied').replace(/[&<>"'\r]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','\r':'&#13;'}[c]));
 let snapshot=null, error=null;
 const opened=new Set(); let focus=null;
 function references(text, inventory){
  const found=[]; const known=new Set(inventory);
  const pattern=/\bRWYS?\s+((?:3[0-6]|[12][0-9]|0?[1-9])[LRC]?(?:(?:\s*[/,]\s*|\s+AND\s+)(?:RWYS?\s+)?(?:3[0-6]|[12][0-9]|0?[1-9])[LRC]?)*)\b/gi;
  for(const match of String(text??'').matchAll(pattern)){
   const start=match.index+match[0].indexOf(match[1]);
   for(const token of match[1].matchAll(/(?:3[0-6]|[12][0-9]|0?[1-9])[LRC]?/gi)){
    const raw=token[0], id=raw.toUpperCase().padStart(/[LRC]$/i.test(raw)?3:2,'0');
    if(known.has(id))found.push({id,start:start+token.index,end:start+token.index+raw.length,raw});
   }
  }
  // Narrow prefix grammar from the OpenAPI example: "27 RWY END ...".
  // Do not recognize bare numbers, arbitrary text before RWY, or reciprocal ends.
  for(const match of String(text??'').matchAll(/\b((?:3[0-6]|[12][0-9]|0?[1-9])[LRC]?)\s+RWY\s+END\b/gi)){
   const raw=match[1], id=raw.toUpperCase().padStart(/[LRC]$/i.test(raw)?3:2,'0');
   if(known.has(id))found.push({id,start:match.index,end:match.index+raw.length,raw});
  }
  return found.filter(r=>!/[A-Z0-9_]/i.test(String(text)[r.start-1]||'')&&!/[A-Z0-9_]/i.test(String(text)[r.end]||'')).sort((a,b)=>a.start-b.start);
 }
 // Fixed family order and numeric tokens make this comparator transitive. Never
 // derive a source year from retrieval/effective dates or expand two-digit years.
 const natural=(a,b)=>{const aa=String(a).match(/\d+|\D+/g)||[],bb=String(b).match(/\d+|\D+/g)||[];for(let i=0;i<Math.min(aa.length,bb.length);i++){const x=aa[i],y=bb[i];const c=/^\d+$/.test(x)&&/^\d+$/.test(y)?Number(x)-Number(y):(x<y?-1:x>y?1:0);if(c)return c;}return aa.length-bb.length;};
 function sortKey(n){const raw=String(n.number||n.id||'').toUpperCase(),year=n.year??n.original_feature?.properties?.coreNOTAMData?.notam?.year;const explicit=/^\d{4}$/.test(String(year));const d=raw.match(/^(?:![A-Z0-9]{3,4}\s+)?(0?[1-9]|1[0-2])\/(\d+)$/),intl=raw.match(/^([A-Z]+)(\d+)\/(\d{2}|\d{4})$/);return [explicit?0:1,explicit?Number(year):0,d?0:intl?1:raw?2:3,d?`${Number(d[1])}/${Number(d[2])}`:intl?`${intl[1]}/${intl[3]}/${Number(intl[2])}`:raw];}
 function compare(a,b){const x=sortKey(a),y=sortKey(b);for(let i=0;i<x.length;i++){const c=i<3?x[i]-y[i]:natural(x[i],y[i]);if(c)return c;}return 0;}
 function records(code,inventory){return (snapshot?.airports?.[code]?.notices||[]).map((n,i)=>({...n,key:`${code}-${i}`,references:[...references(n.text,inventory),...(Array.isArray(n.formatted_texts)?n.formatted_texts:[]).flatMap((t,i)=>typeof t?.text==='string'?references(t.text,inventory).map(r=>({...r,representation:`formatted_texts[${i}].text` })):[])]})).sort(compare);}
 function verifiedZero(code){const e=snapshot?.airports?.[code];return Boolean(e&&e.notices.length===0&&e.completeness==='VERIFIED'&&e.zero_verified===true&&typeof e.verification_reference==='string'&&e.verification_reference.trim());}
 function availability(code){const e=snapshot?.airports?.[code];return !e?'Unavailable — no accepted airport snapshot':verifiedZero(code)?'Explicitly verified zero airport notices; not an operational status':'Coverage UNKNOWN — snapshot references only';}
 function summary(code){const entry=snapshot?.airports?.[code];return `<section class="notam-summary" aria-label="${esc(code)} NOTAM coverage"><strong>${esc(availability(code))}</strong><p>Retrieved: ${esc(snapshot?.retrieved_utc)}. Snapshot only; no current operational validity established.</p>${entry?.warning?`<p>${esc(entry.warning)}</p>`:''}${error?`<p>${esc(error)}${entry?' · Retained snapshot':''}</p>`:''}</section>`;}
 function cell(code,id,inventory){const n=records(code,inventory).filter(n=>n.references.some(r=>r.id===id)).length;return `<td class="unknown"><button id="toggle-${esc(code)}-${esc(id)}" type="button" data-reader-open="${esc(code)}-${esc(id)}" aria-label="Read NOTAMs (${n}) for runway ${esc(id)} at ${esc(code)}" aria-expanded="false" aria-controls="reader-${esc(code)}-${esc(id)}">Read NOTAMs (${n})</button><small class="reader-note">${esc(!snapshot?.airports?.[code]?'Data unavailable':n?'Literal references only':verifiedZero(code)?'Verified zero airport notices; no operational status':'No matching references; coverage UNKNOWN')}</small></td>`;}
 function table(items){return `<table class="notice-table"><thead><tr><th>Identifier</th><th>Complete source text / details</th></tr></thead><tbody>${items.map(n=>`<tr><th scope="row">${esc(n.number||n.id||'Not supplied')}</th><td>${notice(n)}</td></tr>`).join('')}</tbody></table>`;}
 function row(code,id,inventory){const items=records(code,inventory).filter(n=>n.references.some(r=>r.id===id));return `<tr id="reader-${esc(code)}-${esc(id)}" class="notam-detail-row" data-reader-inline hidden><td colspan="4"><section class="inline-reader" aria-labelledby="toggle-${esc(code)}-${esc(id)}"><p>${esc(availability(code))}. Availability not assessed. No closure interpretation.</p>${items.length?table(items):'<p>No matching runway references in the available snapshot. Missing information does not establish absence of notices.</p>'}</section></td></tr>`;}
 function notice(n){return `<article class="reader-notice" data-reader-key="${esc(n.key)}"><h4>${esc(n.number||n.id||'Source ID not supplied')}</h4><p class="reader-raw">${esc(typeof n.text==='string'?n.text:'Original text unavailable')}</p>${(Array.isArray(n.formatted_texts)?n.formatted_texts:[]).map(t=>`<p>Provider representation: ${esc(t.type)} / ${esc(t.field)}</p><p class="reader-raw">${esc(t.text)}</p>`).join('')}${n.warnings?.length?`<p>${esc(n.warnings.join('; '))}</p>`:''}<details class="notice-provenance"><summary>Source, reported times and reference evidence</summary><dl><dt>Source ID</dt><dd>${esc(n.id)}</dd><dt>Source</dt><dd>${esc(n.source)}</dd><dt>Reported effective start</dt><dd>${esc(n.effectiveStart)}</dd><dt>Reported effective end</dt><dd>${esc(n.effectiveEnd)}</dd><dt>Retrieved</dt><dd>${esc(n.retrieved_utc||snapshot?.retrieved_utc)}</dd><dt>Source-reported relationships / type</dt><dd>${esc(n.reported_relationships?JSON.stringify(n.reported_relationships):'Not supplied')}</dd></dl><p>Text references: ${esc(n.references.map(r=>`${r.id} [${r.start}, ${r.end}) in ${r.representation||'text'}`).join(', ')||'None indexed')} (zero-based UTF-16 offsets into the named source representation)</p></details><details><summary>Preserved source metadata</summary><pre data-scan-ignore>${esc(JSON.stringify(n,null,2))}</pre></details></article>`;}
 function block(code,inventory){
  const list=records(code,inventory),entry=snapshot?.airports?.[code];
  const group=(key,title,items)=>`<details id="reader-${code}-${key}" data-reader-detail><summary>${esc(title)} (${items.length})</summary>${key==='all'?table(items):'<p>Included in the all-notices table above; no records excluded from that view.</p>'+items.map(n=>`<p>${esc(n.number||n.id||'Source ID not supplied')} · source record ${esc(n.key)}</p>`).join('')}${!items.length?'<p>No notices in this snapshot group. Coverage unknown.</p>':''}</details>`;
  const elapsed=Date.now()-Date.parse(snapshot?.retrieved_utc),age=Number.isFinite(elapsed)?`${Math.max(0,Math.floor(elapsed/3600000))} hours since retrieval`:'Age unavailable';
  return `<section class="notam-reader" aria-label="${code} NOTAM reader"><h3>NOTAM reader</h3><p>${snapshot?.synthetic?'SYNTHETIC — NO LIVE DATA · ':''}Reading only · Availability not assessed. Text references do not establish applicability.</p><p class="coverage-warning" role="status">${esc(!entry?'NOTAM data unavailable':entry.warning||(verifiedZero(code)?'Explicit zero-notice report; operational status not assessed':'Completeness unknown'))}</p><p>${esc(availability(code))}</p>${error?`<p role="status">${esc(error)}</p>`:''}<p>Retrieved: ${esc(snapshot?.retrieved_utc)} · Completeness: ${esc(entry?.completeness||'UNKNOWN')} · ${esc(age)}. Historical snapshot, not current operational validity. Freshness policy not established; check retrieval time.</p>${error&&entry?'<p>Showing retained snapshot after reader error.</p>':''}<p>${verifiedZero(code)?'Explicit verification applies only to this reported collection; no runway operational status is assessed.':'Snapshot records only; complete airport coverage has not been established. No closure interpretation is performed.'}</p>${group('all','All notices in this snapshot',list)}${group('airport','Explicitly source-declared airport-wide',list.filter(n=>n.scope==='airport-wide'))}${group('unmatched','Unmatched / scope unknown',list.filter(n=>!n.references.length&&n.scope!=='airport-wide'))}</section>`;
 }
 function detailKey(el){return el.id||`${el.closest('[data-reader-detail], [data-reader-inline]')?.id}:${el.closest('[data-reader-key]')?.dataset.readerKey}:${el.className||'metadata'}`;}
 function before(){
  if(typeof document==='undefined'||!document.querySelectorAll)return;
  opened.clear();
  document.querySelectorAll('.notam-reader details, .inline-reader details').forEach(el=>{if(el.open)opened.add(detailKey(el));});
  document.querySelectorAll('[data-reader-inline]').forEach(el=>{if(!el.hidden)opened.add(el.id);});
  const active=document.activeElement;
  focus=active?.dataset?.readerOpen?{button:active.dataset.readerOpen}:active?.matches?.('.notam-reader summary, .inline-reader summary')?{detail:detailKey(active.parentElement)}:null;
 }
 function after(){
  if(typeof document==='undefined'||!document.querySelectorAll)return;
  document.querySelectorAll('.notam-reader details, .inline-reader details').forEach(el=>{el.open=opened.has(detailKey(el));});
  document.querySelectorAll('[data-reader-inline]').forEach(el=>{el.hidden=!opened.has(el.id);});
  document.querySelectorAll('[data-reader-open]').forEach(b=>{
   const panel=document.getElementById('reader-'+b.dataset.readerOpen);
   const sync=()=>b.setAttribute('aria-expanded',String(Boolean(panel&&!panel.hidden)));
   sync();panel?.addEventListener('scan:reveal',sync);
   b.addEventListener('click',()=>{
   const el=document.getElementById('reader-'+b.dataset.readerOpen);
   if(el){el.hidden=!el.hidden;sync();}
  });});
  if(focus){const target=focus.button?[...document.querySelectorAll('[data-reader-open]')].find(el=>el.dataset.readerOpen===focus.button):[...document.querySelectorAll('.notam-reader details, .inline-reader details')].find(el=>detailKey(el)===focus.detail)?.querySelector('summary');target?.focus({preventScroll:true});}
 }
 function set(data,problem=null){snapshot=data;error=problem;}
 return {references,records,compare,availability,summary,cell,row,block,before,after,set};
})();
if(typeof module!=='undefined')module.exports=NotamReader;
