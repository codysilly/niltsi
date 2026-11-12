/* Local preview state controls; no acquisition. */
document.getElementById('preview-state').addEventListener('change',event=>{
 const state=event.target.value;
 NotamReader.set(state==='unavailable'?null:SPATCHIN_SYNTHETIC_PREVIEW.notams,state==='error'?'NOTAM refresh failed; last snapshot retained':state==='stale'?'SYNTHETIC source reports STALE snapshot; retrieval time preserved. No age threshold applied.':null);
 render();
});
