"""The patrol console: one self-contained HTML page per patrol run.

It opens on the morning's question, what broke, what was fixed and what needs a person, with a
trail of every daily partition across the top. Selecting an incident shows the evidence the gate
used: each candidate fix, how far it stays from the source's control totals, and the threshold.
"""
from __future__ import annotations

import html
import json
import math

LOGO = """<svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true"><path d="M16 2 28 7v9c0 7-5.2 12.2-12 14C9.2 28.2 4 23 4 16V7z" fill="var(--canopy)"/><path d="M8.5 19.5c2.6-1.4 3.6-4.6 6.3-5.3 2.9-.8 4.3 2.4 8.7.8" fill="none" stroke="var(--blaze)" stroke-width="2.2" stroke-linecap="round"/><circle cx="23.5" cy="15" r="2" fill="var(--blaze)"/></svg>"""

CSS = """
:root{--spruce:#1d332c;--canopy:#2c6a4d;--moss:#e3eee6;--blaze:#d9a21b;--blaze-soft:#fbf1d6;--ember:#b8432f;
--fog:#eef1ee;--surface:#ffffff;--ink:#17211d;--ridge:#5b6863;--line:#d6ddd8;--focus:#2f6fde;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--spruce:#0f1c18;--canopy:#5fb98a;--moss:#1c2f26;--blaze:#e8b53a;
--blaze-soft:#3a3016;--ember:#f07a62;--fog:#141b18;--surface:#1b2420;--ink:#e6ece8;--ridge:#a2b0aa;--line:#2c3833;--focus:#8fb3ff;color-scheme:dark}}
:root[data-theme="dark"]{--spruce:#0f1c18;--canopy:#5fb98a;--moss:#1c2f26;--blaze:#e8b53a;--blaze-soft:#3a3016;--ember:#f07a62;
--fog:#141b18;--surface:#1b2420;--ink:#e6ece8;--ridge:#a2b0aa;--line:#2c3833;--focus:#8fb3ff;color-scheme:dark}
*{box-sizing:border-box}html,body{margin:0}body{background:var(--fog);color:var(--ink);
font:15px/1.5 "Source Sans 3","Segoe UI",system-ui,sans-serif;font-variant-numeric:tabular-nums}
h1,h2,h3,.brand,.trail-label,.count{font-family:"Barlow Semi Condensed","Arial Narrow",system-ui,sans-serif}
button{font:inherit;cursor:pointer}:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.app{display:grid;grid-template-columns:220px 1fr;min-height:100vh}
.side{background:var(--spruce);color:#dfe8e3;padding:20px 14px;display:flex;flex-direction:column;gap:22px}
.brand{display:flex;align-items:center;gap:10px;font-size:21px;font-weight:600;color:#fff;letter-spacing:.2px}
.nav{display:flex;flex-direction:column;gap:2px}.nav a{color:#c9d6cf;text-decoration:none;padding:8px 10px;border-radius:6px;font-size:14.5px}
.nav a[aria-current]{background:rgba(255,255,255,.09);color:#fff;font-weight:600}.nav a:hover{color:#fff}
.side .foot{margin-top:auto;font-size:12.5px;color:#9db0a6;line-height:1.45}
main{padding:26px 30px 48px;min-width:0}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;flex-wrap:wrap}
h1{font-size:30px;line-height:1.1;margin:0;font-weight:600}.sub{color:var(--ridge);margin:4px 0 0}
.gatebox{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 14px;max-width:340px;font-size:13.5px}
.gatebox strong{display:block;font-size:14.5px}.gatebox span{color:var(--ridge)}
.trail{margin:22px 0 8px;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px 12px}
.trail-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.trail-label{font-size:17px;font-weight:600}.legend{display:flex;gap:14px;font-size:12.5px;color:var(--ridge);flex-wrap:wrap}
.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.path{position:relative;height:84px;margin-top:6px}
.path svg.line{position:absolute;inset:0;width:100%;height:100%}
.stop{position:absolute;top:50%;transform:translate(-50%,-50%);border:0;padding:0;background:none;width:18px;height:30px;display:grid;place-items:center}
.stop b{display:block;width:6px;height:6px;border-radius:50%;background:var(--line)}
.stop.fixed b{width:12px;height:12px;background:var(--canopy)}
.stop.benign b{width:11px;height:11px;background:var(--surface);border:2px solid var(--canopy)}
.stop.escalate b{width:14px;height:14px;border-radius:3px;transform:rotate(45deg);background:var(--blaze)}
.stop[aria-pressed="true"] b{box-shadow:0 0 0 3px var(--surface),0 0 0 5px var(--ink)}
.ticks{display:flex;justify-content:space-between;font-size:12px;color:var(--ridge)}
.summary{font-size:17px;margin:18px 0 18px;max-width:72ch}.summary .count{font-size:22px;font-weight:600}
.c-esc{color:#9a6e00}.c-fix{color:var(--canopy)}.c-ok{color:var(--ridge)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .c-esc{color:var(--blaze)}}:root[data-theme="dark"] .c-esc{color:var(--blaze)}
.work{display:grid;grid-template-columns:minmax(260px,360px) 1fr;gap:18px;align-items:start}
.queue{background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.tabs{display:flex;border-bottom:1px solid var(--line);overflow-x:auto}
.tabs button{border:0;background:none;padding:11px 12px;color:var(--ridge);font-size:14px;white-space:nowrap;border-bottom:2px solid transparent}
.tabs button[aria-selected="true"]{color:var(--ink);font-weight:600;border-bottom-color:var(--canopy)}
.list{list-style:none;margin:0;padding:0;max-height:560px;overflow:auto}
.item{display:grid;grid-template-columns:14px 1fr auto;gap:10px;align-items:center;width:100%;text-align:left;border:0;border-bottom:1px solid var(--line);background:none;padding:11px 14px;color:var(--ink)}
.item:hover{background:var(--moss)}.item[aria-current="true"]{background:var(--moss);box-shadow:inset 3px 0 0 var(--canopy)}
.item .d{font-weight:600}.item .a{display:block;color:var(--ridge);font-size:13px}
.mark{width:12px;height:12px;border-radius:50%}.mark.escalate{background:var(--blaze);border-radius:2px;transform:rotate(45deg) scale(.85)}
.mark.fixed{background:var(--canopy)}.mark.benign{border:2px solid var(--canopy)}
.state{font-size:12.5px;color:var(--ridge)}.empty{padding:22px 16px;color:var(--ridge)}
.detail{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px 22px;min-width:0}
.detail h2{font-size:24px;margin:0;font-weight:600}.verdict{margin:6px 0 16px;font-size:15.5px;max-width:70ch}
.badge{display:inline-block;padding:2px 9px;border-radius:99px;font-size:13px;font-weight:600;margin-right:6px}
.badge.escalate{background:var(--blaze-soft);color:#7a5600}.badge.fixed{background:var(--moss);color:var(--canopy)}.badge.benign{background:var(--fog);color:var(--ridge)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .badge.escalate{color:var(--blaze)}}:root[data-theme="dark"] .badge.escalate{color:var(--blaze)}
h3{font-size:16px;margin:18px 0 8px;font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{border:1px solid var(--line);border-radius:6px;padding:3px 8px;font-size:13px}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px 18px;font-size:14px}
.facts div span{display:block;color:var(--ridge);font-size:12.5px}
.cands{width:100%;border-collapse:collapse;font-size:14px}.cands th{text-align:left;color:var(--ridge);font-weight:600;font-size:12.5px;padding:6px 8px;border-bottom:1px solid var(--line)}
.cands td{padding:8px;border-bottom:1px solid var(--line);vertical-align:middle}.cands tr.chosen td{background:var(--moss)}
.gap{position:relative;height:10px;background:var(--fog);border-radius:5px;min-width:120px}
.gap i{position:absolute;left:0;top:0;bottom:0;border-radius:5px;background:var(--canopy)}.gap i.over{background:var(--blaze)}.gap i.inf{background:var(--ember);width:100%!important}
.gap u{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--ink)}
.gapnum{font-size:12.5px;color:var(--ridge);white-space:nowrap}
.actions{display:flex;gap:10px;margin-top:18px;flex-wrap:wrap}
.btn{border:1px solid var(--line);background:var(--surface);color:var(--ink);padding:8px 14px;border-radius:8px;font-weight:600}
.btn.primary{background:var(--canopy);border-color:var(--canopy);color:#fff}.btn:disabled{opacity:.5;cursor:default}
.note{font-size:13px;color:var(--ridge);margin-top:10px;max-width:72ch}
.toast{position:fixed;bottom:20px;right:20px;background:var(--spruce);color:#fff;padding:10px 16px;border-radius:8px;font-size:14px;opacity:0;transform:translateY(8px);transition:.2s;pointer-events:none}
.toast.show{opacity:1;transform:none}
@media (max-width:900px){.app{grid-template-columns:1fr}.side{flex-direction:row;align-items:center;flex-wrap:wrap;padding:12px 16px;gap:12px}
.nav{flex-direction:row;overflow-x:auto}.side .foot{display:none}main{padding:18px 16px 40px}.work{grid-template-columns:1fr}}
@media (max-width:600px){.stop.clean{display:none}.stop b{transform:scale(.75)}.stop.escalate b{transform:rotate(45deg) scale(.75)}}
@media (prefers-reduced-motion:reduce){.toast{transition:none}}
@media print{
  body{background:#fff!important;color:#000!important;font-size:12pt}
  .side,.toast,.actions{display:none}
  .app{display:block}
  .item,.detail,.trail,.queue,.summary{page-break-inside:avoid;break-inside:avoid}
  a[href]:after{content:" (" attr(href) ")";font-size:0.8em}
  .mark.fixed:after{content:" ✓"}
  .mark.benign:after{content:" ○"}
  .mark.escalate:after{content:" ■"}
}
"""

JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const fixInf=c=>{if(c&&(c.residual===null||c.residual===undefined))c.residual=Infinity;return c};
D.incidents.forEach(r=>{(r.candidates||[]).forEach(fixInf);fixInf(r.chosen)});
const FIX = {none:()=>'Leave the data as it is', dedupe:p=>p.keep==='last'?'Remove duplicate keys, keep the latest copy':'Remove duplicate keys, keep the first copy',
 dedupe_business:()=>'Remove rows re-sent under new keys', reload:()=>'Reload the partition from the source',
 rescale:p=>(p.channel?`Rescale ${p.channel} amounts`:'Rescale all amounts')+` by ${p.factor}`, trim:p=>`Trim back to ${p.to} rows`,
 trim_value:p=>`Trim excess ${p.value} rows`, quarantine:()=>'Quarantine rows with unknown customers', remap:()=>'Remap the renamed column'};
const ALERT = {duplicate_keys:'Duplicate keys', duplicate_rows:'Rows re-sent under new keys', missing_partition:'Partition missing',
 orphan_keys:'Unknown customers', volume_drop:'Volume drop', volume_rise:'Volume spike', mix_drop:'Category or channel share fell',
 mix_rise:'Category or channel share rose', scale_shift:'Amounts on a different scale', reconcile_gap:'Does not match source totals',
 schema_contract:'Schema changed', null_spike:'Column suddenly empty'};
const KIND = {'auto-fixed':'fixed','no action needed':'benign','escalate':'escalate'};
const LABEL = {fixed:'Fixed and proven', benign:'Checked, left alone', escalate:'Needs you'};
function parse(fx){const m=/^(\w+)\((.*)\)$/.exec(fx||'');if(!m)return{a:fx,p:{}};const p={};
 m[2].split(', ').filter(Boolean).forEach(kv=>{const i=kv.indexOf('=');p[kv.slice(0,i)]=kv.slice(i+1)});return{a:m[1],p}}
const fixName = fx=>{const {a,p}=parse(fx);return (FIX[a]||(()=>fx))(p)};
const pct = r=>r===null||r===undefined?'n/a':(!isFinite(r)?'breaks a rule':(r*100).toFixed(r<0.001?3:2)+'%');
const fmtDate = s=>s?new Date(s+'T00:00:00').toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'}):'Whole table';
const inc = D.incidents.map((r,i)=>({...r,i,k:KIND[r.decision]||'escalate',state:null}));
const lam = D.meta.lambda;
let tab='escalate', sel=(inc.find(r=>r.k==='escalate'&&r.chosen&&isFinite(r.chosen.residual))||inc.find(r=>r.k==='escalate')||inc[0]||{}).i;

function summary(){const n=k=>inc.filter(r=>r.k===k).length;
 document.getElementById('summary').innerHTML=`<span class="count c-esc">${n('escalate')}</span> incidents need you. <span class="count c-fix">${n('fixed')}</span> were fixed and proven against the source. <span class="count c-ok">${n('benign')}</span> were checked and left alone.`;
 document.querySelectorAll('[data-tab]').forEach(b=>{const k=b.dataset.tab;b.querySelector('em').textContent=k==='all'?inc.length:n(k)})}

function trail(){const days=D.meta.days, el=document.getElementById('path'), byDay={};
 inc.forEach(r=>{if(r.partition)byDay[r.partition]=r});
 const W=days.length-1||1, yAt=x=>50+30*Math.sin(x*Math.PI*3);let s='', pts=[];
 for(let j=0;j<=100;j++)pts.push(`${j},${(yAt(j/100)/10).toFixed(2)}`);
 days.forEach((d,i)=>{const r=byDay[d];const f=i/W, x=(f*100).toFixed(3), y=yAt(f).toFixed(2);
  const cls=r?r.k:'clean';const lab=r?`${fmtDate(d)}: ${LABEL[r.k]}`:`${fmtDate(d)}: no alerts`;
  s+=r?`<button class="stop ${cls}" style="left:${x}%;top:${y}%" data-i="${r.i}" aria-pressed="${r.i===sel}" aria-label="${lab}" title="${lab}"><b></b></button>`
     :`<span class="stop clean" style="left:${x}%;top:${y}%" title="${lab}"><b></b></span>`;});
 el.innerHTML=`<svg class="line" preserveAspectRatio="none" viewBox="0 0 100 10" aria-hidden="true"><polyline points="${pts.join(' ')}" fill="none" stroke="var(--line)" stroke-width="2" vector-effect="non-scaling-stroke" stroke-dasharray="6 5"/></svg>`+s;
 el.querySelectorAll('button.stop').forEach(b=>b.onclick=()=>{select(+b.dataset.i,true)});
 document.getElementById('t0').textContent=fmtDate(days[0]);document.getElementById('t1').textContent=fmtDate(days[days.length-1])}

function list(){const ul=document.getElementById('list');const rows=inc.filter(r=>tab==='all'||r.k===tab);
 ul.innerHTML=rows.length?rows.map(r=>`<li><button class="item" data-i="${r.i}" aria-current="${r.i===sel}"><span class="mark ${r.k}"></span>
  <span><span class="d">${fmtDate(r.partition)}</span><span class="a">${(r.detectors||['schema_contract']).map(a=>ALERT[a]||a).slice(0,2).join(', ')}</span></span>
  <span class="state">${r.state||''}</span></button></li>`).join(''):`<li class="empty">${tab==='escalate'?'Nothing needs you. The patrol handled every incident on its own or found nothing wrong.':'No incidents here.'}</li>`;
 ul.querySelectorAll('.item').forEach(b=>b.onclick=()=>select(+b.dataset.i))}

function why(r){
 const c=r.chosen;if(r.table_level)return'A column was renamed or changed type upstream. Schema changes always go to a person.';
 if(r.k==='fixed')return`${fixName(c.fix)}. The result matches the source's control totals within ${pct(c.residual)}, under the certified threshold of ${pct(lam)}.`;
 if(r.k==='benign')return`The data already matches the source within ${pct(c.residual)}. The alert reflects a real event or a slightly stale control total.`;
 if(!c)return'No fix could be proposed for this alert.';
 if(!isFinite(c.residual))return`Every fix DataRanger could try would still leave duplicate keys, unknown customers or an empty partition.`;
 if(c.tier!=='auto')return`The best-supported fix, "${fixName(c.fix).toLowerCase()}", deletes data or changes a value that the gate cannot verify.`;
 return`The closest fix, "${fixName(c.fix).toLowerCase()}", still misses the source by ${pct(c.residual)}, past the certified threshold.`;
}

function detail(){const r=inc.find(x=>x.i===sel);const el=document.getElementById('detail');if(!r){el.innerHTML='';return}
 const cands=(r.candidates||[]).map(c=>`<tr class="${c===r.chosen?'chosen':''}"><td>${fixName(c.fix)}</td><td class="gap"><i class="${c.residual>lam?'over':''}${!isFinite(c.residual)?' inf':''}" style="width:${Math.min(100,c.residual/Math.max(lam*4,0.01)*100)}%"></i><u style="left:${Math.min(100,lam/Math.max(lam*4,0.01)*100)}%"></u></td><td class="gapnum">${pct(c.residual)}</td></tr>`).join('');
 el.innerHTML=`<h2>${fmtDate(r.partition)}</h2><div class="verdict"><span class="badge ${r.k}">${LABEL[r.k]}</span> ${why(r)}</div>
  <h3>What the patrol saw</h3><div class="chips">${(r.detectors||[]).map(a=>`<span class="chip">${ALERT[a]||a}</span>`).join('')}</div>
  <div class="facts"><div><span>Table</span>${r.table||'n/a'}</div><div><span>Partition</span>${r.partition||'n/a'}</div><div><span>Rows</span>${r.rows||'n/a'}</div></div>
  <h3>Candidate fixes</h3><table class="cands"><thead><tr><th>Fix</th><th>Gap from source</th><th></th></tr></thead><tbody>${cands||'<tr><td colspan="3">None</td></tr>'}</tbody></table>
  <div class="actions"><button class="btn primary" ${r.k!=='escalate'?'disabled':''}>Accept and apply</button><button class="btn">Leave for a person</button></div>
  <p class="note">The gate only applies a fix when the result stays inside the certified residual. Everything else waits for you.</p>`;
}

function select(i,fromTrail){sel=i;list();detail();if(fromTrail)document.getElementById('list').querySelector(`[data-i="${i}"]`)?.scrollIntoView({block:'nearest'})}

summary();trail();list();detail();
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;document.querySelectorAll('[data-tab]').forEach(x=>x.setAttribute('aria-selected',x===b));list()});
"""

def write_outputs(out, incidents, meta):
    """Write the self-contained dashboard.html and supporting files."""
    data = {"incidents": incidents, "meta": meta}
    html_page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>DataRanger patrol</title>
<style>{CSS}</style></head><body>
<div class="app"><aside class="side"><div class="brand">{LOGO} DataRanger</div>
<nav class="nav"><a href="#" aria-current="true">Today</a><a href="#">History</a></nav>
<div class="foot">Every fix is proven against the source before it is applied.</div></aside>
<main><div class="top"><div><h1>Morning patrol</h1><p class="sub">What broke, what was fixed, what needs a person.</p></div>
<div class="gatebox"><strong>Certified residual</strong><span>{meta.get('lambda',0):.2%} of source totals</span></div></div>
<div class="trail"><div class="trail-head"><span class="trail-label">Every daily partition</span>
<div class="legend"><i style="background:var(--canopy)"></i>fixed <i style="border:2px solid var(--canopy);background:var(--surface)"></i>checked <i style="background:var(--blaze);border-radius:2px;transform:rotate(45deg)"></i>needs you</div></div>
<div class="path" id="path"></div><div class="ticks"><span id="t0"></span><span id="t1"></span></div></div>
<div class="summary" id="summary"></div>
<div class="work"><section class="queue"><div class="tabs">
<button data-tab="escalate" aria-selected="true">Needs you <em></em></button>
<button data-tab="fixed">Fixed <em></em></button>
<button data-tab="benign">Checked <em></em></button>
<button data-tab="all">All <em></em></button></div>
<ul class="list" id="list"></ul></section><section class="detail" id="detail" aria-live="polite"></section></div>
</main></div><div class="toast" id="toast" role="status"></div>
<script type="application/json" id="data">{json.dumps(data)}</script><script>{JS}</script></body></html>"""
    (out / "dashboard.html").write_text(html_page)
    # other outputs omitted for brevity in this example; full file has them
