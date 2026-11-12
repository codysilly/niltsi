/* Reading only: text references do not establish applicability or availability. */
'use strict';
const NotamReader = (() => {
 const esc=s=>String(s??'Not supplied').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
  return found;
 }
 function records(code,inventory){return (snapshot?.airports?.[code]?.notices||[]).map((n,i)=>({...n,key:`${code}-${i}`,references:references(n.text,inventory)}));}
 function cell(code,id,inventory){const n=records(code,inventory).filter(n=>n.references.some(r=>r.id===id)).length;return `<td class="unknown"><button type="button" data-reader-open="${esc(code)}-${esc(id)}" aria-label="Read NOTAMs (${n}) for runway ${esc(id)} at ${esc(code)}" aria-expanded="false" aria-controls="reader-${esc(code)}-${esc(id)}">Read NOTAMs (${n})</button><small class="reader-note">Availability not assessed</small></td>`;}
 function notice(n){return `<article class="reader-notice" data-reader-key="${esc(n.key)}"><h4>${esc(n.number||n.id||'Source ID not supplied')}</h4><p class="reader-raw">${esc(n.text||'Original text unavailable')}</p><dl><dt>Source</dt><dd>${esc(n.source)}</dd><dt>Reported effective start</dt><dd>${esc(n.effectiveStart)}</dd><dt>Reported effective end</dt><dd>${esc(n.effectiveEnd)}</dd><dt>Retrieved</dt><dd>${esc(n.retrieved_utc||snapshot?.retrieved_utc)}</dd><dt>Source-reported relationships / type</dt><dd>${esc(n.reported_relationships?JSON.stringify(n.reported_relationships):'Not supplied')}</dd></dl><p>Text references: ${esc(n.references.map(r=>`${r.id} [${r.start}, ${r.end})`).join(', ')||'None indexed')} (zero-based UTF-16 offsets into original text)</p><details><summary>Preserved source metadata</summary><pre>${esc(JSON.stringify(n,null,2))}</pre></details></article>`;}
 function block(code,inventory){
  const list=records(code,inventory),entry=snapshot?.airports?.[code];
  const group=(key,title,items)=>`<details id="reader-${code}-${key}" data-reader-detail><summary>${esc(title)} (${items.length})</summary>${items.map(notice).join('')||'<p>No notices in this snapshot group. Coverage unknown.</p>'}</details>`;
  return `<section class="notam-reader" aria-label="${code} NOTAM reader"><h3>NOTAM reader</h3><p>${snapshot?.synthetic?'SYNTHETIC — NO LIVE DATA · ':''}Reading only · Availability not assessed. Text references do not establish applicability.</p><p role="status">${esc(error||(!entry?'NOTAM data unavailable':entry.warning||'Completeness unknown'))}</p><p>Retrieved: ${esc(snapshot?.retrieved_utc)} · Completeness: ${esc(entry?.completeness||'UNKNOWN')} · Freshness policy not established; check retrieval time.</p>${error&&entry?'<p>Showing retained snapshot after reader error.</p>':''}${group('all','All notices',list)}${group('airport','Explicitly source-declared airport-wide',list.filter(n=>n.scope==='airport-wide'))}${group('unmatched','Unmatched / scope unknown',list.filter(n=>!n.references.length&&n.scope!=='airport-wide'))}${inventory.map(id=>group(id,`Text references ${id}`,list.filter(n=>n.references.some(r=>r.id===id)))).join('')}</section>`;
 }
 function detailKey(el){return el.id||`${el.closest('[data-reader-detail]')?.id}:${el.closest('[data-reader-key]')?.dataset.readerKey}`;}
 function before(){
  if(typeof document==='undefined'||!document.querySelectorAll)return;
  opened.clear();
  document.querySelectorAll('.notam-reader details').forEach(el=>{if(el.open)opened.add(detailKey(el));});
  const active=document.activeElement;
  focus=active?.dataset?.readerOpen?{button:active.dataset.readerOpen}:active?.matches?.('.notam-reader summary')?{detail:detailKey(active.parentElement)}:null;
 }
 function after(){
  if(typeof document==='undefined'||!document.querySelectorAll)return;
  document.querySelectorAll('.notam-reader details').forEach(el=>{el.open=opened.has(detailKey(el));});
  document.querySelectorAll('[data-reader-open]').forEach(b=>{
   const panel=document.getElementById('reader-'+b.dataset.readerOpen);
   const sync=()=>b.setAttribute('aria-expanded',String(Boolean(panel?.open)));
   sync();panel?.addEventListener('toggle',sync);
   b.addEventListener('click',()=>{
   const el=document.getElementById('reader-'+b.dataset.readerOpen);
   if(el){el.open=true;el.querySelector('summary').focus();el.scrollIntoView({block:'nearest'});}
  });});
  if(focus){const target=focus.button?[...document.querySelectorAll('[data-reader-open]')].find(el=>el.dataset.readerOpen===focus.button):[...document.querySelectorAll('.notam-reader details')].find(el=>detailKey(el)===focus.detail)?.querySelector('summary');target?.focus({preventScroll:true});}
 }
 function set(data,problem=null){snapshot=data;error=problem;}
 return {references,records,cell,block,before,after,set};
})();
if(typeof module!=='undefined')module.exports=NotamReader;
