/* app.js — IoA 主逻辑：配置、NL指令、指标、DAG、消息流、WebSocket */

const API=window.location.origin||'http://127.0.0.1:8000';
const SIM='http://127.0.0.1:8001';
const AUTH_TOKEN = '';
const AUTH = {};

async function ensureAuth(){ return true; }
const DOMAINS=['east-china','north-china','south-china','west-china'];
const DOMAIN_CN={'east-china':'华东','north-china':'华北','south-china':'华南','west-china':'西南'};
const COLORS={'east-china':'#3b82f6','north-china':'#22c55e','south-china':'#f59e0b','west-china':'#a855f7'};

// ── 场景 ────────────────────────────────────────
let currentScenario='network';

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
];

function validateNLInput(txt){
  const len=txt.trim().length;
  if(len<2)return {valid:false,msg:'指令太短（至少2个字符），请描述您的运维需求'};
  if(len>500)return {valid:false,msg:'指令过长（不超过500字符），请精简描述'};
  return {valid:true};
}

async function sendNL(){
  const txt=$('nlInput').value.trim();if(!txt)return;
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
    const r=await fetch(`${SIM}/simulator/fault/inject?fault_type=${type}&target=${encodeURIComponent(targets[0])}`,{method:'POST'});
    if(r.ok){
      const data=await r.json();
      log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--green)">check</span> 故障已注入: ${data.fault_id||(data.faults||[]).map(f=>f.fault_id).join(', ')}`);
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
    const r=await fetch(`${SIM}/simulator/fault/clear_all`,{method:'GET'});
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
  toast('一键演示: 清除旧故障 → 注入 → 诊断修复','info');
  log('<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle">play_circle</span> 一键演示开始');
  try{
    // 1. 清除旧故障（跳过确认）
    await clearAllFaults(true);
    await new Promise(r=>setTimeout(r,500));

    // 2. 注入故障
    $('faultType').value='link_congestion'; $('faultDomain').value='east-china';
    await demoFault();
    await new Promise(r=>setTimeout(r,500));

    // 3. 记录当前 DAG，发送 NL 指令
    await loadDags();
    const beforeIds=new Set(Object.keys(dagData));
    const txt='华东链路拥塞，请全流程诊断修复';
    const msgId='demo-'+Date.now();
    await postJSON(`${API}/messages`,{
      msg_id:msgId,from_agent:'gui',to_agent:'orchestrator-agent',
      intent:{type:'user',description:txt,priority:'high'},
      payload:{params:{message:txt}},correlation_id:'gui-'+msgId,ts_ms:Date.now()
    });
    toast('指令已发送，等待修复...','success');

    // 4. 轮询等待新 DAG 完成（最多 90s）
    let found=null;
    for(let i=0;i<90;i++){
      await new Promise(r=>setTimeout(r,1000));
      try{await loadDags();}catch(e){}
      const all=Object.values(dagData).sort((a,b)=>(b.submitted_at_ms||0)-(a.submitted_at_ms||0));
      const nd=all.find(d=>d.dag_id&&!beforeIds.has(d.dag_id));
      if(nd){
        if(!found){found=nd.dag_id;expandedDags[found]=true;}
        const st=nd.status;
        const nodes=nd.nodes||[];
        const done=nodes.filter(n=>n.status==='completed').length;
        log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--yellow)">progress_activity</span> DAG ${st} (${done}/${nodes.length}) ${i+1}s`);
        if(st==='completed'||st==='failed'){
          renderDags(Object.values(dagData).map(d=>({dag_id:d.dag_id,status:d.status,definition:d.definition,description:d.description,submitted_at_ms:d.submitted_at_ms,finished_at_ms:d.finished_at_ms})));
          if(st==='completed'){
            toast('一键演示完成！故障已修复','success');
            log('<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--green)">check_circle</span> 演示完成: 华东链路拥塞已自动修复');
          }else{
            toast('DAG 执行失败，请重试','error');
            log('<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">error</span> DAG 失败，请手动注入后重试');
          }
          break;
        }
      }
    }
    if(!found)log('<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--muted)">info</span> 指令已投递，可在 DAG 面板查看进度');
  }catch(e){
    toast('演示失败: '+friendlyError(e),'error');
    log('<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">cancel</span> 演示异常: '+esc(friendlyError(e)));
  }finally{popOp();}
}

async function runMultiFaultDemo(){
  pushOp();
  log('<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle">warning</span> 复合故障: 3域同时注入');
  toast('复合故障注入中...','warning');
  try{
    await clearAllFaults(true);
    await new Promise(r=>setTimeout(r,300));
    const configs=[{type:'link_congestion',domain:'east-china'},{type:'device_failure',domain:'north-china'},{type:'ddos',domain:'south-china'}];
    const targets={link_congestion:['Core-Router->Edge-R1'],device_failure:['Core-Router'],ddos:['Core-Router']};
    for(const cfg of configs){
      const t=(targets[cfg.type]||[])[0]||'';
      try{await fetch(`${SIM}/simulator/fault/inject?fault_type=${cfg.type}&target=${encodeURIComponent(t)}`,{method:'POST'});}catch(e){}
      await new Promise(r=>setTimeout(r,300));
    }
    log('<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--green)">check</span> 复合故障注入完成');
    try{await refreshTopoFromSimulator();}catch(e){}
    // 发送全域修复指令
    $('nlInput').value='华东链路拥塞、华北设备故障、华南DDoS攻击，请全部诊断修复';
    await sendNL();
  }catch(e){log('<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">error</span> 复合故障失败: '+esc(friendlyError(e)));}
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
/* ── Clock ── */
function updateClock(){$('clock').textContent=new Date().toLocaleTimeString();}

/* ── Init ── */
async function init(){
  // 首屏安全兜底：最多 3 秒后强制隐藏 loading（避免 CDN 异常导致白屏）
  setTimeout(()=>{const o=$('loadingOverlay');if(o){o.classList.add('fade-out');setTimeout(()=>o.remove(),600);}},3000);
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
  // 数据拉取完成后淡出 loading
  const overlay=$('loadingOverlay');if(overlay){overlay.classList.add('fade-out');setTimeout(()=>overlay.remove(),600);}
}
document.addEventListener('DOMContentLoaded', init);
