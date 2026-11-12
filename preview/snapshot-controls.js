document.getElementById('preview-state').addEventListener('change',event=>{
 snapshotPreviewMode=event.target.value;
 notamSnapshots.refresh();
});
