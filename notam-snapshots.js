/* Same-origin published snapshots only. No provider access or automatic polling. */
'use strict';
const NotamSnapshots = (() => {
 const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
 function validate(data,{allowSynthetic=false}={}){
  if(!object(data)||!object(data.airports))throw Error('Invalid NOTAM snapshot envelope');
  if(data.synthetic!==undefined&&typeof data.synthetic!=='boolean')throw Error('Invalid synthetic marker');
  if(data.synthetic===true&&!allowSynthetic)throw Error('Synthetic NOTAM snapshot rejected outside preview');
  if(typeof data.retrieved_utc!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(data.retrieved_utc)||!Number.isFinite(Date.parse(data.retrieved_utc)))throw Error('Invalid NOTAM retrieval timestamp');
  for(const [code,entry] of Object.entries(data.airports)){
   if(!/^[A-Z0-9]{3,4}$/.test(code)||!object(entry)||!Array.isArray(entry.notices))throw Error('Invalid NOTAM airport collection');
   for(const field of ['warning','completeness'])if(entry[field]!==undefined&&typeof entry[field]!=='string')throw Error('Invalid NOTAM collection metadata');
   for(const notice of entry.notices){
    if(!object(notice))throw Error('Invalid NOTAM record');
    for(const field of ['text','id','number','source','effectiveStart','effectiveEnd','retrieved_utc','scope']){
     if(notice[field]!==undefined&&notice[field]!==null&&typeof notice[field]!=='string')throw Error('Invalid NOTAM display field');
    }
    if(notice.warnings!==undefined&&(!Array.isArray(notice.warnings)||notice.warnings.some(w=>typeof w!=='string')))throw Error('Invalid NOTAM warnings');
    if(notice.formatted_texts!==undefined){
     if(!Array.isArray(notice.formatted_texts)||notice.formatted_texts.some(t=>!object(t)||typeof t.text!=='string'||['type','field'].some(k=>t[k]!==undefined&&typeof t[k]!=='string')))throw Error('Invalid NOTAM representation');
    }
   }
  }
  return data;
 }
 function create({fetchFn,baseURL,now=Date.now,onChange=()=>{},timeoutMs=15000,allowSynthetic=false}){
  const base=new URL(baseURL),url=new URL('./data/notams.json',base);
  if(!['http:','https:'].includes(base.protocol)||url.origin!==base.origin)throw Error('Same-origin HTTP snapshot required');
  const state={snapshot:null,refreshing:false,error:null,attempted_utc:null,checked_utc:null};
  let pending=null;
  const read=()=>({...state});
  const notify=()=>onChange(read());
  function refresh(){
   if(pending)return pending;
   state.refreshing=true;state.attempted_utc=new Date(now()).toISOString();
   pending=Promise.resolve().then(async()=>{
    const controller=new AbortController();let timer;
    try{
     const work=(async()=>{
      const response=await fetchFn(url.href,{cache:'no-store',credentials:'same-origin',redirect:'error',signal:controller.signal});
      if(!response.ok)throw Error(`NOTAM snapshot refresh failed (HTTP ${response.status})`);
      const data=validate(await response.json(),{allowSynthetic});
      if(Date.parse(data.retrieved_utc)>now())throw Error('Future NOTAM snapshot rejected; retrieval time unverified');
      if(state.snapshot&&Date.parse(data.retrieved_utc)<Date.parse(state.snapshot.retrieved_utc))throw Error('Older NOTAM snapshot rejected');
      return data;
     })();
     const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(Error('NOTAM snapshot refresh timed out'));},timeoutMs);});
     const data=await Promise.race([work,timeout]);
     state.snapshot=data;state.checked_utc=new Date(now()).toISOString();
     state.error=null;
    }catch(error){
     // Fixed local diagnostics only; never expose server bodies or arbitrary URLs.
     state.error=/^(NOTAM snapshot refresh failed \(HTTP \d{3}\)|Invalid NOTAM .+|Invalid synthetic marker|Synthetic NOTAM snapshot rejected outside preview|Older NOTAM snapshot rejected|Future NOTAM snapshot rejected; retrieval time unverified|NOTAM snapshot refresh timed out)$/.test(error?.message||'')?error.message:'NOTAM snapshot refresh failed';
    }finally{clearTimeout(timer);state.refreshing=false;pending=null;notify();}
    return read();
   });
   notify();return pending;
  }
  return {refresh,read};
 }
 return {validate,create};
})();
if(typeof module!=='undefined')module.exports=NotamSnapshots;
