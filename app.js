'use strict';
// Niltsi v0.8. METARs loaded from GitHub Pages same-origin JSON. Not for operational dispatch use.
const runwayPairs={ORD:['04L/22R','04R/22L','09L/27R','09R/27L','09C/27C','10L/28R','10C/28C','10R/28L'],LAX:['06L/24R','06R/24L','07L/25R','07R/25L'],JFK:['04L/22R','04R/22L','13L/31R','13R/31L']};
const airportInfo={ORD:{name:'Chicago O’Hare',icao:'KORD'},LAX:{name:'Los Angeles International',icao:'KLAX'},JFK:{name:'John F. Kennedy International',icao:'KJFK'}};
let runwayData=null;
let atisData=null; let atisRetrieved=null;
const initial=['ORD','LAX','JFK'];let airports=initial.slice();const metars={};let lastFetch=null,loading=false;
try{const saved=JSON.parse(localStorage.getItem('niltsi-airports'));if(Array.isArray(saved)&&saved.length)airports=[...new Set(saved.filter(x=>typeof x==='string'&&/^[A-Z]{3}$/.test(x)))].slice(0,30);}catch{}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function icao(code){return airportInfo[code]?.icao||('K'+code);} // K prefix only a US-airport fallback; not valid worldwide.
function components(w,h){const a=(w.direction-h)*Math.PI/180,head=w.speed*Math.cos(a),cross=w.speed*Math.sin(a);return {head:Math.round(head),cross:Math.round(Math.abs(cross)),side:cross>0?'R':cross<0?'L':'—'};}
function heading(id){return Number(id.slice(0,2))*10%360;}
function ends(code){return runwayData?.airports?.[code]?.map(x=>x.id)||(runwayPairs[code]||[]).flatMap(pair=>pair.split('/'));}
function runwayHeading(code,id){const end=runwayData?.airports?.[code]?.find(x=>x.id===id);return end?end.true_heading:heading(id);}
function runwayRow(code,id,wind){
 if(!wind||!Number.isFinite(wind.direction)||!Number.isFinite(wind.speed))return `<tr><td>${esc(id)}</td><td class="unknown">—</td><td class="unknown">—</td><td class="unknown">—</td></tr>`;
 const bearing=runwayHeading(code,id), c=components(wind,bearing);
 const gust=Number.isFinite(wind.gust)&&wind.gust>wind.speed?components({...wind,speed:wind.gust},bearing):null;
 function longitudinal(v){const head=v.head>=0;return `<span class="${head?'headwind':Math.abs(v.head)<5?'tailwind-low':'tailwind-high'}">${head?v.head+' HW':Math.abs(v.head)+' TW'}</span>`;}
 function lateral(v){return `<span class="${v.cross<10?'crosswind-low':v.cross<15?'crosswind-mid':'crosswind-high'}">${v.cross} ${v.side}</span>`;}
 return `<tr><td title="${runwayData?.airports?.[code]?'FAA true alignment':'Approximate runway-number heading'}: ${bearing}°">${esc(id)}</td><td>${longitudinal(c)}${gust?`<div class="gust">G ${longitudinal(gust)}</div>`:''}</td><td>${lateral(c)}${gust?`<div class="gust">G ${lateral(gust)}</div>`:''}</td><td class="unknown">—</td></tr>`;
}
// Conservative extraction: only explicit approach/departure clauses, never generic runway mentions.
function advertised(text,kind){
 const normalized=String(text||'').toUpperCase();
 const pattern=kind==='arr' ? /(?:ARR\s+EXP|ARRIVALS?\s+(?:EXP|EXPECT)|LANDING\s+RWYS?)\s+([\s\S]*?)(?=\.\s|\bDEPS?\s+EXP\b|$)/g : /(?:DEPS?\s+EXP\s+RWYS?|DEPARTURES?\s+(?:EXP|EXPECT)\s+RWYS?)\s+([\s\S]*?)(?=\.\s|$)/g;
 const found=new Set(); let match;
 while((match=pattern.exec(normalized))!==null){
  const fragment=match[1].slice(0,300);
  for(const runway of fragment.matchAll(/\b(?:RWY\s*)?([0-3]?[0-9][LRC]?)\b/g)){
   const id=runway[1].padStart(runway[1].match(/[LRC]$/)?3:2,'0');
   if(/^(?:0[1-9]|[12][0-9]|3[0-6])[LRC]?$/.test(id))found.add(id);
  }
 }
 return [...found];
}
function atisBlock(code){
 const reports=atisData?.airports?.[icao(code)];
 const list=Array.isArray(reports)?reports:[];
 const latest=type=>list.filter(x=>x.type===type).sort((a,b)=>Date.parse(b.updatedAt||0)-Date.parse(a.updatedAt||0))[0];
 const combined=latest('combined'), arr=latest('arr')||combined, dep=latest('dep')||combined;
 const report=combined||latest('arr')||latest('dep');
 const newest=Math.max(...list.map(x=>Date.parse(x.updatedAt||0)).filter(Number.isFinite),0);
 const age= newest?Date.now()-newest:Infinity;
 const stale=age>45*60*1000 || age< -5*60*1000 || !atisRetrieved || Date.now()-Date.parse(atisRetrieved)>45*60*1000;
 const landing=arr?advertised(arr.datis,'arr'):[], departing=dep?advertised(dep.datis,'dep'):[];
 const label=values=>values.length?values.join(' · '):'—';
 const info=report?.code?esc(report.code):'—';
 const issue=report?.time&&/^\d{4}$/.test(String(report.time))?esc(report.time)+'Z':'—';
 const full=list.map(r=>`<p><strong>${esc(r.type.toUpperCase())} · INFO ${esc(r.code||'—')} · ${esc(r.time||'—')}Z</strong></p><p class="atis-raw">${esc(r.datis)}</p>`).join('');
 return `<section class="atis"><div class="atis-top"><h3>ATIS <span class="atis-provider">atis.info · ${stale?'STALE / UNVERIFIED':'UNOFFICIAL'}</span></h3><div class="atis-meta">INFO ${info} · ISSUED <strong>${issue}</strong></div></div><div class="atis-runways"><div class="atis-box"><div class="atis-label">↘ LANDING</div><div class="atis-value">${esc(label(landing))}</div></div><div class="atis-box"><div class="atis-label">↗ DEPARTING</div><div class="atis-value">${esc(label(departing))}</div></div></div><details class="atis-full"><summary>Full ATIS information</summary>${full||'<p>ATIS not available.</p>'}<p>Source update: ${esc(newest?utc(newest):'—')} · Snapshot retrieved: ${esc(utc(atisRetrieved))}</p><p><a href="https://atis.info/api/${encodeURIComponent(icao(code))}" target="_blank" rel="noopener">Original ATIS source</a></p><p>Do not use for real world flight planning or navigation. Advertised runway extraction is automated and may be incomplete.</p></details></section>`;
}
function windLabel(w){if(!w)return '—';const dir=Number.isFinite(w.direction)?String(w.direction).padStart(3,'0')+'°':'VRB';return `${dir} / ${w.speed}${Number.isFinite(w.gust)&&w.gust>0?'G'+w.gust:''}KT`;}
function utc(t){if(!t)return '—';const d=new Date(t);return Number.isNaN(d.getTime())?'—':d.toISOString().slice(0,16).replace('T',' ')+'Z';}
function render(){document.getElementById('cards').innerHTML=airports.map(code=>{const a=airportInfo[code],m=metars[code],r=ends(code),w=m?.wind;
 
 const link=`https://aviationweather.gov/api/data/metar?ids=${encodeURIComponent(icao(code))}&format=raw`;
 return `<article class="airport"><div class="airport-head"><div><div class="icao">${esc(code)}</div><div class="name">${esc(a?.name||'Airport not yet configured')}</div></div></div><div class="wind"><div><div class="sub">METAR WIND · ${m?.observed?esc(utc(m.observed)):'—'}</div><div class="reading">${windLabel(w)}</div><div class="sub">${m?.error?esc(m.error):w&&w.direction===null?'Variable direction: components unavailable':''}</div></div></div>${atisBlock(code)}<div class="runways">${r.length?`<table class="rw-table"><thead><tr><th>RWY</th><th>Head/Tail</th><th>Cross</th><th>Status</th></tr></thead><tbody>${r.map(id=>runwayRow(code,id,w)).join('')}</tbody></table>`:'<div class="empty">Runway data pending.</div>'}</div><button class="expand" type="button" data-expand="${code}" aria-expanded="false">Show details ▾</button><div class="details" id="detail-${code}" hidden><p><strong>METAR:</strong> ${esc(m?.raw||'No observation available.')}</p><p><strong>Observation:</strong> ${esc(utc(m?.observed))} · <strong>Last retrieval:</strong> ${esc(utc(lastFetch))}</p><p><a href="${link}" target="_blank" rel="noopener">Original METAR (Aviation Weather Center)</a></p><p><strong>Wind components:</strong> ${runwayData?.airports?.[code]?'FAA NASR true alignments':'Approximate runway designation headings (FAA data not loaded)'}; sustained and gust components where available.</p><p><strong>Runways:</strong> ${runwayData?.airports?.[code]?`FAA NASR effective ${esc(runwayData.effective_date)} · <a href="${esc(runwayData.source_url)}" target="_blank" rel="noopener">Source archive</a>`:'FAA NASR data not yet published; provisional runway list.'}</p><p><strong>NOTAM / closure status:</strong> Not connected; dashes do not mean open.</p><button type="button" data-remove="${code}">Remove airport</button></div></article>`;}).join('');
 document.querySelectorAll('[data-expand]').forEach(b=>b.addEventListener('click',()=>{const el=document.getElementById('detail-'+b.dataset.expand);el.hidden=!el.hidden;b.setAttribute('aria-expanded',String(!el.hidden));b.textContent=el.hidden?'Show details ▾':'Hide details ▴';}));
 document.querySelectorAll('[data-remove]').forEach(b=>b.addEventListener('click',()=>{airports=airports.filter(x=>x!==b.dataset.remove);save();render();}));}
function save(){try{localStorage.setItem('niltsi-airports',JSON.stringify(airports));}catch{}}
function parseMetar(m){const speed=m.wspd===null||m.wspd===undefined?NaN:Number(m.wspd),direction=m.wdir===null||m.wdir===undefined||m.wdir==='VRB'?null:Number(m.wdir),gust=m.wgst===null||m.wgst===undefined?null:Number(m.wgst);return {wind:Number.isFinite(speed)?{speed,direction:Number.isFinite(direction)?direction:null,gust:Number.isFinite(gust)?gust:null}:null,observed:typeof m.obsTime==='number'?new Date(m.obsTime*1000).toISOString():(m.obsTime||m.reportTime||null),raw:m.rawOb||'',error:null};}
async function refresh(){if(loading)return;loading=true;render();const targets=airports.filter(c=>airportInfo[c]);if(!targets.length){loading=false;render();return;}
 const url=`./data/metars.json?t=${Date.now()}`;
 try{const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw new Error(`HTTP ${response.status}`);const data=await response.json();if(!Array.isArray(data.metars))throw new Error('Unexpected METAR response');const found=new Set();for(const m of data.metars){const code=targets.find(c=>icao(c)===m.icaoId);if(code){metars[code]=parseMetar(m);found.add(code);}}for(const code of targets){if(!found.has(code))metars[code]={error:'No METAR returned',wind:null};}lastFetch=data.updated_utc||null;}
 catch(e){for(const code of targets){metars[code]={error:'Published METAR feed unavailable',wind:null};}console.error('Niltsi METAR retrieval:',e);}finally{loading=false;render();}}
document.getElementById('add-form').addEventListener('submit',e=>{e.preventDefault();const f=document.getElementById('iata'),code=f.value.trim().toUpperCase();if(!/^[A-Z]{3}$/.test(code)){alert('Enter a three-letter IATA code.');return;}if(airports.includes(code)){alert(code+' is already displayed.');return;}if(airports.length>=30){alert('Maximum 30 airports.');return;}airports.push(code);save();render();f.value='';refresh();});
async function refreshRunways(){
 try{
  const response=await fetch(`./data/runways.json?t=${Date.now()}`,{cache:'no-store'});
  if(!response.ok)throw new Error(`HTTP ${response.status}`);
  const data=await response.json();
  if(data.heading_reference!=='TRUE'||!data.airports||!data.effective_date)throw new Error('Invalid FAA runway data');
  for(const [code,count] of Object.entries({ORD:16,LAX:8,JFK:8})){
   const list=data.airports[code];
   if(!Array.isArray(list)||list.length!==count||list.some(x=>!x.id||!Number.isFinite(x.true_heading)))throw new Error(`Invalid ${code} runway data`);
  }
  runwayData=data;render();
 }catch(e){console.info('FAA runway data not yet available; retaining provisional headings:',e.message);}
}
async function refreshAtis(){
 try{
  const response=await fetch(`./data/atis.json?t=${Date.now()}`,{cache:'no-store'});
  if(!response.ok)throw new Error(`HTTP ${response.status}`);
  const data=await response.json();
  if(!data.airports||!data.retrieved_utc)throw new Error('Invalid ATIS snapshot');
  atisData=data;atisRetrieved=data.retrieved_utc;render();
 }catch(e){console.info('ATIS snapshot unavailable:',e.message);}
}
function clock(){document.getElementById('clock').textContent=new Date().toISOString().replace('T',' ').slice(0,19)+'Z';}clock();setInterval(clock,1000);render();refresh();refreshRunways();refreshAtis();setInterval(refresh,120000);setInterval(refreshRunways,3600000);setInterval(refreshAtis,120000);
