/* Pure NOTAM validation and status rules; shared by the browser and Node tests. */
'use strict';
const NotamFeed = (() => {
 const STALE_MS=15*60*1000;
 const AIRPORTS=['KORD','KLAX','KJFK'];
 const states=['closure','restriction','unresolved'];
 const finiteTime=value=>typeof value==='string' && /Z$/.test(value) && Number.isFinite(Date.parse(value));
 function validate(data,now=Date.now()){
  if(!data || data.schema_version!==1 || !finiteTime(data.generated_utc) || Date.parse(data.generated_utc)>now+300000 || !data.airports)throw Error('Invalid NOTAM feed');
  for(const airport of AIRPORTS){
   const entry=data.airports[airport];
   if(!entry || !Array.isArray(entry.notams) || typeof entry.verified!=='boolean' || typeof entry.complete!=='boolean' || !['UNKNOWN','VERIFIED'].includes(entry.state) || !(entry.error===null || typeof entry.error==='string'))throw Error('Invalid NOTAM airport');
   for(const key of ['attempted_utc','last_success_utc'])if(entry[key]!==null && (!finiteTime(entry[key]) || Date.parse(entry[key])>now+300000 || Date.parse(entry[key])>Date.parse(data.generated_utc)))throw Error('Invalid NOTAM timestamp');
   if(entry.verified && (!entry.complete || entry.state!=='VERIFIED' || entry.error!==null || !entry.last_success_utc || entry.last_success_utc!==entry.attempted_utc))throw Error('Invalid NOTAM coverage');
   const ids=new Set();
   if(entry.observed_notams!==undefined && !Array.isArray(entry.observed_notams))throw Error('Invalid observed notices');
   for(const n of [...entry.notams,...(entry.observed_notams||[])]){
    if(n?.lifecycle==='UNKNOWN'){
     if(entry.verified || entry.complete || n.end_kind!=='unverified' || n.classification!=='unresolved' || !Array.isArray(n.runway_ends) || n.runway_ends.length || typeof n.record_key!=='string' || !n.record_key || ids.has(n.record_key) || typeof n.id!=='string' || !n.id || n.airport!==airport || typeof n.text!=='string' || !n.text || !finiteTime(n.retrieved_utc) || Date.parse(n.retrieved_utc)>Date.parse(data.generated_utc) || !Object.hasOwn(n,'original_feature') || typeof n.reason!=='string')throw Error('Invalid unverified source notice');
     for(const time of [n.effective_start_utc,n.effective_end_utc])if(time!==null && !finiteTime(time))throw Error('Invalid unverified validity');
     ids.add(n.record_key);
     continue;
    }
    if(!n || typeof n.id!=='string' || !n.id || ids.has(n.id) || n.airport!==airport || typeof n.text!=='string' || !n.text || !finiteTime(n.effective_start_utc) || !finiteTime(n.retrieved_utc) || Date.parse(n.retrieved_utc)>Date.parse(data.generated_utc) || !['fixed','estimated','permanent'].includes(n.end_kind) || !['ACTIVE','CANCELLED','REPLACED'].includes(n.lifecycle) || !states.includes(n.classification) || !Array.isArray(n.runway_ends) || n.runway_ends.some(id=>typeof id!=='string' || !/^(0[1-9]|[12][0-9]|3[0-6])[LRC]?$/.test(id)) || !(n.schedule===null || typeof n.schedule==='string') || typeof n.reason!=='string')throw Error('Invalid NOTAM record');
    if(n.end_kind==='permanent' ? n.effective_end_utc!==null : (!finiteTime(n.effective_end_utc) || Date.parse(n.effective_end_utc)<=Date.parse(n.effective_start_utc)))throw Error('Invalid NOTAM validity');
    if(n.classification!=='unresolved' && (!n.runway_ends.length || n.schedule || n.end_kind==='estimated'))throw Error('Unverified NOTAM interpretation');
    ids.add(n.id);
   }
  }
  return data;
 }
 function phase(n,now=Date.now()){
  if(n.lifecycle==='UNKNOWN' || n.end_kind==='unverified')return 'unverified lifecycle / validity';
  if(n.lifecycle!=='ACTIVE')return n.lifecycle.toLowerCase();
  if(now<Date.parse(n.effective_start_utc))return 'future';
  if(n.end_kind!=='permanent' && n.end_kind!=='estimated' && now>=Date.parse(n.effective_end_utc))return 'expired';
  return 'within validity window';
 }
 function quality(entry,failed=false,now=Date.now()){
  const flags=[];
  if(failed)flags.push('FEED REFRESH FAILED');
  if(!entry)return [...flags,'UNAVAILABLE / UNVERIFIED'];
  if(entry.error)flags.push(entry.error);
  if(!entry.verified)flags.push('UNVERIFIED');
  if(!entry.complete)flags.push('INCOMPLETE');
  if((entry.notams||[]).some(n=>n.lifecycle==='UNKNOWN' || n.end_kind==='unverified') || entry.observed_notams?.length)flags.push('NOTICE SEMANTICS UNVERIFIED');
  const age=now-Date.parse(entry.last_success_utc);
  if(!entry.last_success_utc || !Number.isFinite(age) || age>=STALE_MS || age< -300000)flags.push('STALE / NO CURRENT SUCCESS');
  return flags;
 }
 function status(entry,id,failed=false,now=Date.now()){
  const active=(entry?.notams||[]).filter(n=>phase(n,now)==='within validity window');
  const applicable=active.filter(n=>n.runway_ends.includes(id));
  const closures=applicable.filter(n=>n.classification==='closure');
  const uncertain=active.some(n=>n.classification==='unresolved' && (!n.runway_ends.length || n.runway_ends.includes(id)));
  const flags=quality(entry,failed,now);
  if(uncertain)flags.push('UNRESOLVED NOTAM');
  const state=flags.length?'UNKNOWN':closures.length?'CLOSED':applicable.some(n=>n.classification==='restriction')?'NOTAM RESTRICTION':'NO CLOSURE IDENTIFIED';
  return {state,flags,lastReportedClosure:closures.length>0};
 }
 return {validate,quality,status,phase,STALE_MS};
})();
if(typeof module!=='undefined')module.exports=NotamFeed;
