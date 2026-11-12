/* Isolated transport simulation. Only snapshot-loader.html loads this file. */
SPATCHIN_SYNTHETIC_PREVIEW.useTransport=true;
let snapshotPreviewMode='snapshot';
SPATCHIN_SYNTHETIC_PREVIEW.fetchSnapshot=async()=>{
 if(snapshotPreviewMode==='error')return {ok:false,status:503};
 if(snapshotPreviewMode==='unavailable')return {ok:false,status:404};
 const data=JSON.parse(JSON.stringify(SPATCHIN_SYNTHETIC_PREVIEW.notams));
 if(snapshotPreviewMode==='malformed')data.airports.KMEM.notices={};
 if(snapshotPreviewMode==='older')data.retrieved_utc='2026-11-10T12:00:00Z';
 return {ok:true,json:async()=>data};
};
