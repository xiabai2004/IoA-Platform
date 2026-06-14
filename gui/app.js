/* app.js — IoA 主逻辑：配置、NL指令、指标、DAG、消息流、WebSocket */

const API=window.location.origin||'http://127.0.0.1:8000';
const SIM=API.replace(/:\d+$/,'') + ':8001' || 'http://127.0.0.1:8001';
const AUTH_TOKEN = '';
const AUTH = {};

async function ensureAuth(){ return true; }
const DOMAINS=['east-china','north-china','south-china','west-china'];
const DOMAIN_CN={'east-china':'华东','north-china':'华北','south-china':'华南','west-china':'西南'};
const COLORS={'east-china':'#3b82f6','north-china':'#22c55e','south-china':'#f59e0b','west-china':'#a855f7'};

// ── 场景切换 ────────────────────────────────────────
let currentScenario='network';
function switchScenario(){
  const sel=$('scenarioSelect');
  currentScenario=sel.value;
  const isDoc=currentScenario==='document';
  $('nlInput').placeholder=isDoc
    ?'输入文档内容进行审核，例如：这是一份合同草案，请您审核...'
    :'输入自然语言运维指令，例如：华东地区网络延迟异常，请全流程诊断修复';
  const networkCtrls=['faultType','faultDomain'];
  networkCtrls.forEach(id=>{const el=$(id);if(el)el.style.display=isDoc?'none':'';});
  document.querySelectorAll('.btn-danger,.btn-success,.btn-purple').forEach(b=>{
    if(b.textContent.includes('注入故障')||b.textContent.includes('清除故障')
       ||b.textContent.includes('演示')||b.textContent.includes('复合故障')){
      b.style.display=isDoc?'none':'';
    }
  });
  log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle">swap_horiz</span> 切换到: ${isDoc?'📄 文档审核':'🌐 网络运维'}`);
}

let expandedDags={}, dagData={}, chart=null, topoNetwork=null;
let topoNodeDS=null, topoEdgeDS=null;
let metricsHistory={}; for(const d of DOMAINS) metricsHistory[d]={latency:[],loss:[],bw:[],labels:[]};
const MAX_H=60;
let msgBuffer=[];
let currentFilter='all';
let _autoScroll=true;

function scrollToBottom(){
  const el=$('msgFlow');
  el.scrollTop=el.scrollHeight;
  _autoScroll=true;
  $('scrollBottomBtn').classList.remove('visible');
}

document.addEventListener('DOMContentLoaded',()=>{
  const msgEl=$('msgFlow');
  if(msgEl){
    msgEl.addEventListener('scroll',()=>{
      const atBottom=msgEl.scrollHeight-msgEl.scrollTop-msgEl.clientHeight<40;
      _autoScroll=atBottom;
      $('scrollBottomBtn').classList.toggle('visible',!atBottom);
    });
  }
});

/* ── NL / Fault ── */
const _NL_KEYWORDS = [
  {name:'monitor_only',kw:['监控','查看','指标','状态','monitor'],domain:true},
  {name:'diagnose',kw:['诊断','分析','原因','根因','diagnose','why'],domain:true},
  {name:'full_remediation',kw:['修复','处理','解决','repair','fix','resolve','remediate'],domain:true},
  {name:'full_remediation',kw:['全流程','全链路','全自动','自动处理','端到端','auto'],domain:true},
  {name:'full_remediation_all',kw:['全域','所有域','全部域','全局','all','全部'],domain:false},
  {name:'health_check',kw:['健康','巡检','检查','health','check'],domain:false},
  {name:'doc_review',kw:['审核','审批','review','approve','文档','检查文档'],domain:false},
];

function validateNLInput(txt){
  const len=txt.trim().length;
  if(len<2)return {valid:false,msg:'指令太短（至少2个字符），请描述您的运维需求'};
  if(len>500)return {valid:false,msg:'指令过长（不超过500字符），请精简描述'};
  return {valid:true};
}

async function sendNL(){
  const txt=$('nlInput').value.trim();if(!txt)return;
  if(currentScenario==='document'){await sendDocReview(txt);return;}
  const check = validateNLInput(txt);
  if(!check.valid){log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--yellow)">info</span> ${esc(check.msg)}`);toast(check.msg,'warning');return;}
  pushOp();
  log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle">progress_activity</span> 发送指令: ${esc(txt)}`);
  try{
    await loadDags();
    const beforeDagIds=new Set(Object.keys(dagData));
    const msgId='nl-'+Date.now();
    const r=await postJSON(`${API}/messages`,{
      msg_id:msgId,from_agent:'gui',to_agent:'orchestrator-agent',
      intent:{type:'user',description:txt,priority:'high'},
      payload:{params:{message:txt}},correlation_id:'gui-'+msgId,ts_ms:Date.now()
    });
    if(!r.ok)throw Error('HTTP '+r.status);
    log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--yellow)">schedule</span> 指令已发送，等待 orchestrator 处理...`);
    toast('指令已发送','success');
    $('nlInput').value='';
    let foundDag=null, dagFoundAt=0;
    for(let i=0;i<60;i++){
      await new Promise(r2=>setTimeout(r2,1000));
      try{await loadDags();}catch(e){console.warn('loadDags poll error:',e);}
      const allDags=Object.values(dagData).sort((a,b)=>(b.submitted_at_ms||0)-(a.submitted_at_ms||0));
      const newDag=allDags.find(d=>d.dag_id&&!beforeDagIds.has(d.dag_id));
      if(newDag){
        if(!foundDag){foundDag=newDag.dag_id;dagFoundAt=i;}
        const st=newDag.status;
        if(st==='completed'){log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--green)">check_circle</span> DAG ${foundDag} 已完成（${(i+1).toFixed(0)}s）`);toast('任务完成','success');popOp();return;}
        if(st==='failed'){log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">cancel</span> DAG 执行失败 (${foundDag})`);toast('DAG 失败','error');popOp();return;}
      }
    }
    if(foundDag){log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--yellow)">hourglass_top</span> DAG ${foundDag} 仍在执行（已等待60s）`);}
    else{log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">help</span> 未检测到新DAG，请确认Orchestrator是否正常运行`);toast('未检测到DAG','warning');}
    popOp();
  }catch(e){popOp();log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">error</span> 发送失败: ${esc(friendlyError(e))}`);toast('发送失败: '+friendlyError(e),'error');}
}

async function demoFault(){
  const type=$('faultType').value, domain=$('faultDomain').value;
  pushOp();
  log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--yellow)">bolt</span> 注入故障: ${type} @ ${DOMAIN_CN[domain]}`);
  toast(`注入 ${type} 到 ${DOMAIN_CN[domain]}`,'warning');
  try{
    const targets=[];
    if(type==='cpu_overload'||type==='device_failure'){targets.push('Core-Router');}
    else if(type==='ddos'||type==='misconfig'){targets.push(`Edge-R${DOMAINS.indexOf(domain)+1}`);}
    else{targets.push(`Core-Router->Edge-R${DOMAINS.indexOf(domain)+1}`);}
    const r=await fetch(`${SIM}/simulator/faults/inject`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type,domain,targets})});
    if(r.ok){
      const data=await r.json();
      log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--green)">check</span> 故障已注入: ${(data.faults||[]).map(f=>f.fault_id).join(', ')}`);
      toast('故障注入成功','success');
      // 用注入的精确信息触发自动修复
      try{await refreshTopoFromSimulator();}catch(e){console.warn('topo refresh:',e);}
      try{await sendNL();}catch(e){console.warn('auto sendNL:',e);}
    }else{throw Error('HTTP '+r.status);}
  }catch(e){log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">error</span> 注入失败: ${esc(friendlyError(e))}`);toast('故障注入失败','error');}
  popOp();
}

async function clearAllFaults(skipConfirm){
  if(!skipConfirm&&!confirm('确定清除所有故障？'))return;
  pushOp();
  log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle">delete_sweep</span> 清除所有故障`);
  try{
    const r=await fetch(`${SIM}/simulator/faults/clear`,{method:'DELETE'});
    if(r.ok){
      log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--green)">check</span> 所有故障已清除`);
      toast('故障已清除','success');
      try{await refreshTopoFromSimulator();}catch(e){console.warn('topo refresh:',e);}
      await loadDags();
    }else{throw Error('HTTP '+r.status);}
  }catch(e){log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">error</span> 清除失败: ${esc(friendlyError(e))}`);}
  popOp();
}

async function runDemo(){
  pushOp();
  log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle">play_circle</span> 一键演示: 注入链路拥塞 → 自动修复`);
  toast('开始一键演示...','info');
  $('faultType').value='link_congestion';$('faultDomain').value='east-china';
  await demoFault();
  popOp();
}

async function runMultiFaultDemo(){
  pushOp();
  log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle">warning</span> 复合故障: 3域同时注入不同类型的故障`);
  toast('复合故障注入中...','warning');
  try{
    const configs=[{type:'link_congestion',domain:'east-china'},{type:'device_failure',domain:'north-china'},{type:'ddos',domain:'south-china'}];
    const targets={link_congestion:[`Core-Router->Edge-R1`],device_failure:['Core-Router'],ddos:[`Edge-R3`]};
    const results=[];
    for(const cfg of configs){
      try{
        const r=await fetch(`${SIM}/simulator/faults/inject`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:cfg.type,domain:cfg.domain,targets:targets[cfg.type]||[]})});
        if(r.ok)results.push(await r.json());
      }catch(e){console.warn(cfg.domain,'fault inject:',e);}
    }
    log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--green)">check</span> 复合故障注入完成（${results.length}个故障）`);
    try{await refreshTopoFromSimulator();}catch(e){console.warn('topo refresh:',e);}
    // 触发全域修复
    $('nlInput').value='所有域发生故障，请全自动诊断修复';
    await sendNL();
  }catch(e){log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">error</span> 复合故障注入失败: ${esc(friendlyError(e))}`);}
  popOp();
}

/* ── Metrics ── */
function updateMetrics(data){
  const m=data.metrics||data;
  let html='';
  for(const d of DOMAINS){
    const r=m[d];if(!r)continue;
    const lat=r.latency_ms||0, loss=(r.packet_loss||0)*100, bw=(r.bandwidth_util||0)*100;
    const ll=level(lat,50,100), bl=level(bw,70,90), lol=level(loss,1,5);
    const worst=ll==='crit'||bl==='crit'||lol==='crit'?'critical':(ll==='warn'||bl==='warn'||lol==='warn'?'warn':'');
    html+=`<div class="metric-card ${worst}">
      <div class="metric-domain"><span class="dot" style="background:${COLORS[d]}"></span>${DOMAIN_CN[d]}</div>
      <div class="metric-row"><span class="metric-label">延迟</span><span class="metric-value ${ll}" id="m-${d}-latency_ms"><span class="status-dot ${ll}"></span>${lat.toFixed(1)}ms</span></div>
      <div class="metric-row"><span class="metric-label">丢包</span><span class="metric-value ${lol}" id="m-${d}-packet_loss"><span class="status-dot ${lol}"></span>${loss.toFixed(2)}%</span></div>
      <div class="metric-row"><span class="metric-label">带宽</span><span class="metric-value ${bl}" id="m-${d}-bandwidth_util"><span class="status-dot ${bl}"></span>${bw.toFixed(1)}%</span></div>
    </div>`;
    const h=metricsHistory[d];
    h.latency.push(lat);h.loss.push(loss);h.bw.push(bw);
    h.labels.push(new Date().toLocaleTimeString());
    if(h.latency.length>MAX_H){h.latency.shift();h.loss.shift();h.bw.shift();h.labels.shift();}
  }
  $('metricsPanel').innerHTML=html;
  $('metricsTime').textContent=new Date().toLocaleTimeString();
  updateChart();
  flashMetrics(m);
}

/* ── Chart ── */
function initChart(){
  const ctx=$('metricsChart').getContext('2d');
  chart=new Chart(ctx,{
    type:'line',
    data:{labels:[],datasets:DOMAINS.map(d=>({label:DOMAIN_CN[d],data:[],borderColor:COLORS[d],backgroundColor:COLORS[d]+'22',borderWidth:2,pointRadius:0,tension:0.3,fill:true}))},
    options:{responsive:true,maintainAspectRatio:false,animation:{duration:300},
      plugins:{legend:{display:true,position:'top',labels:{color:'#64748b',font:{size:10},boxWidth:12,padding:8}}},
      scales:{x:{display:false},y:{beginAtZero:true,grid:{color:'#1e293b'},ticks:{color:'#64748b',font:{size:10}}}}
    }
  });
}
function updateChart(){
  if(!chart)return;
  chart.data.labels=metricsHistory[DOMAINS[0]].labels;
  DOMAINS.forEach((d,i)=>{chart.data.datasets[i].data=metricsHistory[d].latency;});
  chart.update('none');
}

/* ── Agents ── */
async function loadAgents(){
  try{
    const d=await getJSON(`${API}/registry/agents`);
    const agents=d.agents||[];
    $('agentCount').textContent=agents.length;
    let html='<table class="agent-table"><tr><th>状态</th><th>ID</th><th>域</th><th>能力</th><th>负载</th></tr>';
    for(const a of agents){
      const st=a.status==='active'?'up':'down';
      let caps=[];
      try{caps=typeof a.capabilities==='string'?JSON.parse(a.capabilities):(a.capabilities||[]);}catch(e){caps=[];}
      const capsHtml=caps.map(c=>`<span class="agent-cap">${esc(c)}</span>`).join('');
      const load=((a.load||0)*100).toFixed(0);
      const loadDisplay = a.load > 0 ? `${load}%` : `<span style="color:var(--muted);font-style:italic">待命中</span>`;
      html+=`<tr><td><span class="agent-dot ${st}"></span>${st==='up'?'在线':'离线'}</td>
        <td>${esc(a.agent_id)}</td><td>${esc(DOMAIN_CN[a.domain]||a.domain)}</td><td>${capsHtml}</td><td>${loadDisplay}</td></tr>`;
    }
    html+='</table>';$('agentTable').innerHTML=html;
  }catch(e){console.warn('loadAgents error:',friendlyError(e));}
}

/* ── DAG ── */
async function loadDags(){
  try{
    const d=await getJSON(`${API}/dag?limit=20`);
    const dags=d.dags||[];$('dagCount').textContent=dags.length;
    dagData={};
    if(dags.length>0){
      const ids=dags.map(g=>g.dag_id).join(',');
      try{
        const batch=await getJSON(`${API}/dag/batch?ids=${encodeURIComponent(ids)}`);
        for(const dt of (batch.dags||[])){dagData[dt.dag_id]=dt;}
      }catch(e){
        console.error('DAG batch error:',e);
        for(const g of dags){
          try{const detail=await getJSON(`${API}/dag/${g.dag_id}`);dagData[g.dag_id]=detail;}catch(e2){console.error('DAG detail error:',g.dag_id,e2);}
        }
      }
    }
    const all=Object.values(dagData).sort((a,b)=>(b.submitted_at_ms||0)-(a.submitted_at_ms||0));
    renderDags(all);
    return all;
  }catch(e){console.error('loadDags error:',e);return[];}
}

function renderDags(dags){
  const container=$('dagList');
  if(!dags.length){container.innerHTML='<div class="empty-hint"><span class="material-symbols-outlined">inbox</span> 暂无 DAG 记录，发送一条自然语言指令开始</div>';return;}
  let html='';
  for(const d of dags){
    const statusClass={'pending':'pending','running':'running','completed':'completed','failed':'failed'}[d.status]||'pending';
    const statusCN={'pending':'等待中','running':'执行中','completed':'已完成','failed':'失败'}[d.status]||d.status;
    const nodes=d.nodes||[];
    const completed=nodes.filter(n=>n.status==='completed').length;
    const failed=nodes.filter(n=>n.status==='failed').length;
    const progress=nodes.length>0?Math.round(completed/nodes.length*100):0;
    const expanded=expandedDags[d.dag_id];
    html+=`<div class="dag-item ${statusClass}" onclick="toggleDag('${d.dag_id}')">
      <div class="dag-header">
        <span class="dag-status ${statusClass}">${statusCN}</span>
        <span class="dag-id">${esc(d.dag_id)}</span>
        <span class="dag-desc">${esc(d.description||'')}</span>
        <span class="dag-progress">${completed}/${nodes.length} (${failed>0?'<span style="color:var(--red)">'+failed+'失败</span>, ':''}${progress}%)</span>
        <span class="dag-time">${new Date(d.submitted_at_ms||0).toLocaleTimeString()}</span>
      </div>`;
    if(expanded){
      html+=`<div class="dag-nodes" onclick="event.stopPropagation()">`;
      for(const n of nodes){
        const ns={'pending':'⌛','assigned':'📋','running':'🔄','completed':'✅','failed':'❌'}[n.status]||'❓';
        html+=`<div class="dag-node ${n.status||'pending'}">
          <span class="dag-node-icon">${ns}</span>
          <span class="dag-node-id">${esc(n.node_id)}</span>
          <span class="dag-node-type">${esc(n.node_type||'')}</span>
          <span class="dag-node-agent">→ ${esc(n.assigned_agent||'未分配')}</span>
          ${n.output?`<div class="dag-node-output">${esc(typeof n.output==='string'?n.output.substring(0,120):JSON.stringify(n.output).substring(0,120))}</div>`:''}
        </div>`;
      }
      html+=`</div>`;
    }
    html+=`</div>`;
  }
  container.innerHTML=html;
}

function toggleDag(id){expandedDags[id]=!expandedDags[id];renderDags(Object.values(dagData).sort((a,b)=>(b.submitted_at_ms||0)-(a.submitted_at_ms||0)));}

/* ── Messages ── */
async function loadMessages(){
  try{
    const d=await getJSON(`${API}/messages?limit=100`);
    msgBuffer=(d.messages||[]).sort((a,b)=>(a.ts_ms||0)-(b.ts_ms||0));
    renderMessages();
  }catch(e){console.error('loadMessages error:',e);}
}
function renderMessages(){
  const filtered=currentFilter==='all'?msgBuffer:msgBuffer.filter(m=>m.intent_type===currentFilter||m.from_agent===currentFilter);
  const container=$('msgFlow');
  let html='';
  for(const m of filtered){
    const time=m.ts_ms?new Date(m.ts_ms).toLocaleTimeString():'--:--:--';
    html+=`<div class="msg-item"><span class="msg-time">${time}</span><span class="msg-from">${esc(m.from_agent||'?')}</span> → <span class="msg-to">${esc(m.to_agent||'*')}</span> <span class="msg-intent">[${esc(m.intent_type||'')}]</span> <span class="msg-desc">${esc((m.intent_desc||'').substring(0,80))}</span></div>`;
  }
  container.innerHTML=html;
  if(_autoScroll)scrollToBottom();
}
function filterMsgs(type,el){
  currentFilter=type;
  document.querySelectorAll('.msg-filter').forEach(b=>b.classList.remove('active'));
  if(el)el.classList.add('active');
  renderMessages();
}

/* ── WebSocket ── */
let _dagLoadTimer=null;
function debouncedLoadDags(){clearTimeout(_dagLoadTimer);_dagLoadTimer=setTimeout(loadDags,300);}
let _wsState='disconnected', _wsReconnDelay=3000, _lastToastMsg='';
function _wsToast(msg,type){if(msg===_lastToastMsg)return;_lastToastMsg=msg;toast(msg,type);}
async function _reloadDataOnReconnect(){
  try{await loadAgents();}catch(e){console.warn('reconnect loadAgents:',e);}
  try{await loadDags();}catch(e){console.warn('reconnect loadDags:',e);}
  try{await loadMessages();}catch(e){console.warn('reconnect loadMessages:',e);}
}
function connectWS(){
  const wsUrl=`${API.replace('http','ws')}/ws/dashboard?token=${AUTH_TOKEN}`;
  const ws=new WebSocket(wsUrl);
  ws.onopen=()=>{
    $('wsDot').className='ok';$('wsLabel').textContent='控制';
    _wsReconnDelay=3000;
    if(_wsState==='disconnected'){_wsToast('WebSocket 已连接','success');}
    else if(_wsState==='reconnecting'){_wsToast(`WS 重连成功 (等待${(_wsReconnDelay/1000).toFixed(0)}s)`,'success');_reloadDataOnReconnect();}
    _wsState='connected';
    loadDags();
  };
  ws.onmessage=(e)=>{
    try{const d=JSON.parse(e.data);if(d.type==='dag_update'||d.type==='node_update')debouncedLoadDags();}catch(ex){console.warn('ws parse:',ex);}
  };
  ws.onclose=()=>{
    $('wsDot').className='off';_wsState='reconnecting';
    const delaySec=(_wsReconnDelay/1000).toFixed(0);$('wsLabel').textContent=`重连中(${delaySec}s)`;
    setTimeout(connectWS,_wsReconnDelay);_wsReconnDelay=Math.min(_wsReconnDelay*2,30000);
  };
  ws.onerror=()=>ws.close();
}
let _simState='disconnected', _simReconnDelay=3000;
function connectSimWS(){
  const ws=new WebSocket(`${API.replace('http','ws').replace(/:\d+$/,':8001')}/simulator/ws`);
  ws.onopen=()=>{
    $('simDot').className='ok';$('simLabel').textContent='仿真';
    _simReconnDelay=3000; _simState='connected';
    startTopoRefreshLoop();
  };
  ws.onmessage=(e)=>{
    try{
      const d=JSON.parse(e.data);
      if(d.type==='metrics_update'){updateMetrics(d);updateTopoStatus(d.metrics||{},null,null);}
    }catch(ex){console.warn('sim ws parse:',ex);}
  };
  ws.onclose=()=>{
    $('simDot').className='off';_simState='reconnecting';
    const delaySec=(_simReconnDelay/1000).toFixed(0);$('simLabel').textContent=`重连中(${delaySec}s)`;
    setTimeout(connectSimWS,_simReconnDelay);_simReconnDelay=Math.min(_simReconnDelay*2,30000);
  };
  ws.onerror=()=>ws.close();
}

/* ── Stage Timeline ── */
let _stageStartMs = 0, _stageTimer = null;
function showStageTimeline(){
  const sb = $('stageBar');if(sb){sb.style.display='flex';sb.querySelectorAll('.stage-item').forEach(el=>el.className='stage-item');}
  _stageStartMs = Date.now();
}
function updateStage(nodeType, nodeStatus){
  const sb = $('stageBar');if(!sb)return;
  const item=sb.querySelector(`.stage-item[data-stage="${nodeType}"]`);
  if(item){
    item.className='stage-item ';
    if(nodeStatus==='running')item.classList.add('current');
    else if(nodeStatus==='completed')item.classList.add('done');
    else if(nodeStatus==='failed')item.classList.add('failed');
  }
  const elapsed=((Date.now()-_stageStartMs)/1000).toFixed(1);
  const timer=sb.querySelector('span.material-symbols-outlined');if(timer) timer.nextSibling.textContent=`${elapsed}s`;
}
function hideStageTimeline(){const sb=$('stageBar');if(sb)sb.style.display='none';}

/* ── 第二场景 ── */
async function sendDocReview(content){
  pushOp();
  const title = content.length>30 ? content.substring(0,30)+'...' : content;
  log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle">description</span> 文档审核: ${esc(title)}`);
  try{
    const dagId='dag-doc-'+Date.now();
    const r=await postJSON(`${API}/messages`,{
      msg_id:'doc-'+Date.now(),from_agent:'gui',to_agent:'orchestrator-agent',
      intent:{type:'user',description:'审核文档: '+title,priority:'normal'},
      payload:{params:{template:'doc_review',content:content,title:title,dag_id:dagId}},
      correlation_id:'gui-doc-'+Date.now(),ts_ms:Date.now()
    });
    if(!r.ok)throw Error('HTTP '+r.status);
    log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--yellow)">schedule</span> 审核任务已提交, DAG: ${dagId}`);
    toast('文档审核已提交','success');
    $('nlInput').value='';
    await new Promise(r2=>setTimeout(r2,2000));
    await loadDags();
  }catch(e){log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">error</span> 提交失败: ${esc(e.message)}`);}
}

/* ── Clock ── */
function updateClock(){$('clock').textContent=new Date().toLocaleTimeString();}

/* ── Init ── */
async function init(){
  initTopo();initChart();
  setInterval(updateClock,1000);
  // 初始拉取
  try{await loadAgents();}catch(e){console.warn('init loadAgents:',e);}
  try{await loadDags();}catch(e){console.warn('init loadDags:',e);}
  try{await loadMessages();}catch(e){console.warn('init loadMessages:',e);}
  try{await refreshTopoFromSimulator();}catch(e){console.warn('init topo:',e);}
  startTopoRefreshLoop();
  connectWS();connectSimWS();
  // 定时轮询
  setInterval(async()=>{try{await loadAgents();}catch(e){};try{await loadDags();}catch(e){};try{await loadMessages();}catch(e){}},15000);
}
document.addEventListener('DOMContentLoaded', init);
