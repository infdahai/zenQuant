'use strict';
const $ = id => document.getElementById(id);
async function api(action, body) { const r = await fetch('/api/research/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-ZenQuant-Request':'1'},body:typeof body==='string'?body:JSON.stringify(body)}); const data=await r.json(); if(!r.ok) throw Error(data.error||'Request failed'); return data; }
async function refresh(){for(const [kind,ids] of [['snapshot',['snapshots']],['result',['resultA','resultB']]]){const values=await api('list',{kind}); for(const id of ids){const select=$(id),current=select.value;select.replaceChildren(...values.map(value=>{const o=document.createElement('option');o.value=value;o.textContent=value;return o}));if(values.includes(current))select.value=current;}}}
function act(id,status,fn){$(id).addEventListener('click',async()=>{try{$(status).textContent='Working…'; await fn();}catch(e){$(status).textContent=e.message}})}
act('import','importStatus',async()=>{const f=$('file').files[0];if(!f)throw Error('Choose a JSON file first.');const x=await api('import',await f.text());await refresh();$('snapshots').value=x.id;$('quality').textContent=JSON.stringify(x.quality,null,2);$('importStatus').textContent='Snapshot saved: '+x.id});
act('inspect','quality',async()=>{const x=await api('snapshot',{id:$('snapshots').value});$('quality').textContent=JSON.stringify({id:x.id,source_sha256:x.source_sha256,provider:x.dataset.provider,venue:x.dataset.venue,instrument:x.dataset.instrument,timeframe:x.dataset.timeframe,quality:x.quality},null,2)});
act('run','runStatus',async()=>{const x=await api('run',{snapshot_id:$('snapshots').value,fee_rate:Number($('fee').value)});await refresh();$('resultA').value=x.id;$('output').textContent=JSON.stringify(x,null,2);$('runStatus').textContent='Diagnostic saved: '+x.id});
act('open','output',async()=>{$('output').textContent=JSON.stringify(await api('result',{id:$('resultA').value}),null,2)});
act('compare','output',async()=>{$('output').textContent=(await api('compare',{a:$('resultA').value,b:$('resultB').value})).comparison});
refresh().catch(e=>{$('importStatus').textContent=e.message});
