/* Multi-device sync and collaboration. All wording is plain Chinese; no git terms. */
(()=>{'use strict';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function create(host,options){
 const api=options.api;
 const S={data:null,job:null,busy:false,summary:'',relayPath:'',deviceName:'',restoreArm:null};
 let alive=true,timer=null,generation=0;
 const $=s=>host.querySelector(s);
 const readonly=()=>!!options.project.readOnly;
 const shortTime=value=>String(value||'').slice(0,16);
 function statusMessage(text,error=false){try{options.status(text,error);}catch{}}
 function paintJob(){
  if(!S.job)return;
  const panel=$('[data-sync-progress]');if(!panel)return;
  panel.hidden=false;
  panel.querySelector('h3').textContent=S.job.phase||'同步中';
  panel.querySelector('progress').value=S.job.percent||0;
  panel.querySelector('[data-sync-percent]').textContent=(S.job.percent||0)+'%';
  const running=!['done','error','cancelled'].includes(S.job.state);
  panel.querySelector('[data-sync-cancel]').hidden=!running;
 }
 function stateBadge(){
  if(!S.data)return '';
  if(!S.data.joined)return '<span class="sync-badge" data-ok="false">未加入同步</span>';
  const picks=S.data.pendingPicks?.length||0;
  if(picks)return `<span class="sync-badge" data-ok="false">有 ${picks} 份待选</span>`;
  return '<span class="sync-badge" data-ok="true">已连接</span>';
 }
 function statusCard(){
  const d=S.data;
  const changed=(d.state['已修改']?.length||0)+(d.state['新增']?.length||0)+(d.state['已删除']?.length||0);
  const cards=[
   ['中转站',d.joined?d.relay:'还没选'],
   ['这台电脑',d.device.name],
   ['没同步的改动',changed+' 处'],
   ['待选',(d.pendingPicks?.length||0)+' 份'],
   ['存档数',String(d.snapshotCount)],
   ['成员',d.members?.length?d.members.map(m=>m['名字']).join('、'):'只有你'],
  ].map(([k,v])=>`<div class="sync-stat"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('');
  return `<section class="sync-card" aria-label="同步状态"><div class="sync-head"><h3>现在的情况</h3>${stateBadge()}</div><div class="sync-grid">${cards}</div>${d.sizeWarning?`<p class="wk-reference-note" role="status">${esc(d.sizeWarning)}</p>`:''}</section>`;
 }
 function saveCard(){
  return `<section class="sync-card" aria-label="存档"><h3>存档</h3><p class="helper">写一句这次干了什么，比如"第一章初稿"。没变化就不用记。</p><div class="sync-row"><input data-sync-input="summary" maxlength="200" value="${esc(S.summary)}" placeholder="这次干了什么…" aria-label="存档说明" ${readonly()?'disabled':''}><button class="primary" data-sync="save" ${readonly()?'disabled':''}>存档</button></div></section>`;
 }
 function syncCard(){
  const running=S.job&&!['done','error','cancelled'].includes(S.job.state);
  const report=S.job?.report;
  const merged=report?.['合上']||[];
  const shown=merged.slice(0,20).map(f=>`<li>${esc(f)}</li>`).join('');
  return `<section class="sync-card" aria-label="同步"><h3>同步</h3><p class="helper">先收别人的、合到一起、再发上去。合不上的两边都留着，等你挑。</p><div class="sync-row"><button class="primary" data-sync="start" ${readonly()?'disabled':''}>同步</button><button data-sync="refresh">刷新状态</button></div><div class="sync-progress" data-sync-progress hidden><h3>同步中</h3><progress max="100" value="0" aria-label="同步进度"></progress><div class="sync-progress-row"><span data-sync-percent>0%</span><button data-sync-cancel>停下来</button></div></div>${S.job&&!running?`<div class="sync-result">${S.job.state==='done'?`<p><strong>同步完成。</strong></p>${merged.length?`<p>合上了 ${merged.length} 处：</p><ul>${shown}</ul>${merged.length>20?`<p class="helper">等 ${merged.length-20} 处省略。</p>`:''}`:'<p>两边一模一样，不用合。</p>'}${(report?.['说明']||[]).map(t=>`<p>${esc(t)}</p>`).join('')}`:`<p class="sync-error" role="alert">${esc(S.job.error?.message||'同步没完成。')}</p>`}</div>`:''}</section>`;
 }
 function timelineCard(){
  const items=(S.data?.timeline||[]).map(e=>`<div class="sync-item"><div class="sync-item-main"><strong>${esc(e['说明'])}</strong><small>${esc(e['作者'])} · ${esc(shortTime(e['时间']))} · ${esc(e['编号'])}</small></div><button data-sync="restore" data-id="${esc(e['编号'])}" ${readonly()?'disabled':''}>${S.restoreArm?.id===e['编号']?'再次点击，回到这次':'回到这次'}</button></div>`).join('');
  return `<section class="sync-card" aria-label="时间线"><h3>时间线</h3>${items?`<div class="sync-list">${items}</div>`:'<p class="helper">还没有存档，先在上面存一版。</p>'}</section>`;
 }
 function picksCard(){
  const picks=S.data?.pendingPicks||[];
  if(!picks.length)return '';
  const items=picks.map(p=>`<div class="sync-item"><div class="sync-item-main"><strong>${esc(p['文件'])}</strong><small>${esc(p['处理']||'')}</small></div><div class="sync-item-actions"><button data-sync="pick-mine" data-file="${esc(p['文件'])}" ${readonly()?'disabled':''}>就用我的</button><button class="primary" data-sync="pick-theirs" data-file="${esc(p['文件'])}" ${readonly()?'disabled':''}>要对方的</button></div></div>`).join('');
  return `<section class="sync-card" aria-label="待选"><h3>待选 <small>（${picks.length} 份）</small></h3><p class="helper">同一处两边改的不一样：你的已经在原位，对方的存在待选里，挑完才算完。</p><div class="sync-list">${items}</div></section>`;
 }
 function teamCard(){
  const t=S.data?.team;if(!t)return '';
  if(!t.active)return `<section class="sync-card" aria-label="协作组"><h3>协作组</h3><p class="helper">开始协作后，每人领一段专属号段，同时新建内容也不会撞号。一个人用时不用开。</p><div class="sync-row"><button data-sync="team-start" ${readonly()?'disabled':''}>开始协作</button></div></section>`;
  const rows=t.members.map(m=>`<div class="sync-item"><div class="sync-item-main"><strong>${esc(m.name)}${m.me?' · 我':''}${m.owner?' · 主人':''}</strong><small>号段 ${m.slot} / ${t.shards}</small></div>${t.isOwner&&!m.me?`<div class="sync-item-actions"><input data-sync-slot="${esc(m.id)}" inputmode="numeric" placeholder="0–1023" aria-label="给${esc(m.name)}指定号段"><button data-sync="team-slot" data-id="${esc(m.id)}">指定</button><button data-sync="team-remove" data-id="${esc(m.id)}">移出</button></div>`:''}</div>`).join('');
  return `<section class="sync-card" aria-label="协作组"><div class="sync-head"><h3>协作组</h3><span class="sync-badge" data-ok="${t.collisions.length?'false':'true'}">${t.collisions.length?'撞号段了':'号段正常'}</span></div>${t.collisions.length?`<p class="wk-reference-note" role="status">这些人撞号段了：${esc(t.collisions.map(g=>g.join('、')).join('；'))}。撞的人点“重领我的”，或让主人指定。</p>`:''}<div class="sync-list">${rows}</div><div class="sync-row"><button data-sync="team-reroll" ${readonly()?'disabled':''}>重领我的号段</button><button data-sync="team-leave" ${readonly()?'disabled':''}>离开协作</button></div><p class="helper">我的号段：${t.mySlot??'—'}。新建内容只用自己的号段；派生编号（对话跟事件走）不受影响。</p></section>`;
 }
 function settingsCard(){
  return `<section class="sync-card" aria-label="设置"><h3>设置</h3><label class="sync-label">中转站文件夹<small>网盘里的空文件夹；账本只会追加，不会删里面的东西。</small></label><div class="sync-row"><input data-sync-input="relayPath" value="${esc(S.relayPath||S.data?.relay||'')}" placeholder="例如 D:\\网盘\\拾光工坊同步" aria-label="中转站文件夹" ${readonly()?'disabled':''}><button data-sync="relay" ${readonly()?'disabled':''}>设为中转站</button><button data-sync="join" ${readonly()?'disabled':''}>加入同步</button></div><label class="sync-label">这台电脑的名字</label><div class="sync-row"><input data-sync-input="deviceName" maxlength="30" value="${esc(S.deviceName||S.data?.device?.name||'')}" aria-label="这台电脑的名字" ${readonly()?'disabled':''}><button data-sync="rename" ${readonly()?'disabled':''}>改名</button></div></section>`;
 }
 function render(){
  if(!alive)return;
  const d=S.data;
  host.innerHTML=`<div class="sync-wrap"><div class="sync-head"><div><span class="eyebrow">多人协作 · 多平台同步</span><h2>同步协作</h2><p>${esc(options.project.name||options.project.title||'当前模组')}</p></div></div>${readonly()?'<p class="wk-reference-note">订阅模组只能看。想一起写，先去创建副本。</p>':''}${d?statusCard()+saveCard()+syncCard()+picksCard()+timelineCard()+teamCard()+settingsCard():'<p class="helper">正在读取同步状态…</p>'}</div>`;
  paintJob();
 }
 async function refresh(){
  const data=await api('/api/sync-status?projectId='+encodeURIComponent(options.project.id));
  if(!alive)return;
  S.data=data;
  noteRevision(data.revision);
  render();
 }
 async function reloadProject(){
  try{await options.onSaved?.();}catch{}
  try{window.STUDIO_DEFER_PROJECT_REFRESH?.();}catch{}
 }
 function noteRevision(revision){if(revision)try{options.onState?.({revision});}catch{}}
 async function guard(busyText){
  if(readonly())throw Error('订阅模组只能看。想一起写，先去创建副本。');
  if(S.busy)throw Error('正在忙，请稍等。');
 }
 async function run(fn){await guard();S.busy=true;render();try{await fn();}catch(error){statusMessage(error.message,true);}finally{S.busy=false;if(alive)render();}}
 function poll(){
  clearTimeout(timer);timer=null;
  if(!alive||!S.job||['done','error','cancelled'].includes(S.job.state))return;
  api('/api/sync-job?jobId='+encodeURIComponent(S.job.jobId)).then(job=>{
   if(!alive)return;S.job=job;paintJob();
   if(['done','error','cancelled'].includes(job.state)){
    // 修订号以 refresh 拿到的为准：任务开工时的已经旧了，用它会顶掉别人的保存。
    refresh().then(()=>{if(job.state==='done'&&(job.report?.['合上']?.length||job.report?.['待选']?.length))reloadProject();}).catch(e=>statusMessage(e.message,true));
    return;
   }
   timer=setTimeout(poll,900);
  }).catch(()=>{
   if(!alive)return;timer=setTimeout(poll,2000);
  });
 }
 host.onclick=e=>{
  const button=e.target.closest('[data-sync]');if(!button||button.disabled)return;
  const action=button.dataset.sync;
  if(action==='refresh')run(refresh,false).catch(()=>{});
  else if(action==='save')run(async()=>{
   if(!S.summary.trim())throw Error('先写一句这次干了什么，再存档。');
   const result=await api('/api/sync-save',{projectId:options.project.id,revision:S.data.revision,summary:S.summary.trim()});
   S.summary='';noteRevision(result.revision);
   statusMessage(result.saved?'已存档。':'和上次存档一模一样，这次不用记。');
   await refresh();
  }).catch(()=>{});
  else if(action==='relay')run(async()=>{
   if(!S.relayPath.trim())throw Error('先填写中转站文件夹，再设置。');
   await api('/api/sync-relay',{projectId:options.project.id,path:S.relayPath.trim()});
   statusMessage('中转站已记住。');await refresh();
  }).catch(()=>{});
  else if(action==='join')run(async()=>{
   if(!S.relayPath.trim()&&!S.data?.relay)throw Error('先填写中转站文件夹，再加入。');
   await api('/api/sync-join',{projectId:options.project.id,relay:S.relayPath.trim()||S.data.relay,deviceName:S.deviceName.trim()||undefined});
   statusMessage('已加入，稿子拿下来了。');await refresh();reloadProject();
  }).catch(()=>{});
  else if(action==='rename')run(async()=>{
   if(!S.deviceName.trim())throw Error('先写这台电脑的名字。');
   await api('/api/sync-device',{projectId:options.project.id,name:S.deviceName.trim()});
   statusMessage('名字已改。');await refresh();
  }).catch(()=>{});
  else if(action==='start')run(async()=>{
   const mine=++generation;
   S.job=await api('/api/sync-start',{projectId:options.project.id});
   if(mine!==generation||!alive)return;
   paintJob();poll();
  }).catch(()=>{});
  else if(action==='cancel')run(async()=>{
   if(!S.job)return;
   S.job=await api('/api/sync-cancel',{jobId:S.job.jobId});
   paintJob();
  }).catch(()=>{});
  else if(action==='pick-mine'||action==='pick-theirs')run(async()=>{
   const result=await api('/api/sync-pick',{projectId:options.project.id,file:button.dataset.file,choice:action==='pick-mine'?'留我的':'要对方的'});
   noteRevision(result.revision);
   statusMessage(result['剩余待选']?'已定稿，还剩 '+result['剩余待选']+' 份。':'定稿完成。');
   await refresh();reloadProject();
  }).catch(()=>{});
  else if(action==='restore'){
   const id=button.dataset.id;
   if(S.restoreArm?.id!==id||Date.now()>S.restoreArm.until){S.restoreArm={id,until:Date.now()+10000};render();return;}
   S.restoreArm=null;
   run(async()=>{
    await api('/api/sync-restore',{projectId:options.project.id,id});
    statusMessage('已回到这次，当前的样子也自动存了一版，放心。');
    await refresh();reloadProject();
   }).catch(()=>{});
  }
  else if(action==='team-start')run(async()=>{
   await api('/api/sync-team-start',{projectId:options.project.id});
   statusMessage('协作组建好了，你是主人，号段 0。');await refresh();
  }).catch(()=>{});
  else if(action==='team-reroll')run(async()=>{
   await api('/api/sync-team-reroll',{projectId:options.project.id});
   statusMessage('号段已重领，新建内容会用新的段。');await refresh();
  }).catch(()=>{});
  else if(action==='team-slot')run(async()=>{
   const input=button.parentElement?.querySelector('input');const shard=Number(input?.value);
   if(!Number.isInteger(shard)||shard<0||shard>1023)throw Error('号段是 0 到 1023 的整数。');
   await api('/api/sync-team-slot',{projectId:options.project.id,deviceId:button.dataset.id,shard});
   statusMessage('号段已指定。');await refresh();
  }).catch(()=>{});
  else if(action==='team-remove')run(async()=>{
   await api('/api/sync-team-remove',{projectId:options.project.id,deviceId:button.dataset.id});
   statusMessage('已移出。');await refresh();
  }).catch(()=>{});
  else if(action==='team-leave')run(async()=>{
   await api('/api/sync-team-remove',{projectId:options.project.id});
   statusMessage('已离开协作组。');await refresh();
  }).catch(()=>{});
 };
 host.oninput=e=>{
  const input=e.target.closest('[data-sync-input]');if(!input)return;
  S[input.dataset.syncInput]=input.value;
 };
 async function load(){await refresh();}
 return {load,save:async()=>true,dirty:()=>false,discard(){},undo(){},redo(){},
  navigation:{capture:()=>({}),restore:async()=>{await refresh();}},
  focusSearch:()=>$('[data-sync-input="summary"]')?.focus(),
  destroy(){alive=false;clearTimeout(timer);timer=null;host.onclick=null;host.oninput=null;host.innerHTML='';}};
}
window.StudentAgeSync={create};})();
