const API=window.location.origin||'http://127.0.0.1:8000';
const SIM=API.replace(/:\d+$/,'') + ':8001' || 'http://127.0.0.1:8001';
// Auth token - disabled (IOA_AUTH_ENABLED=false)
const AUTH_TOKEN = '';
const AUTH = {};

async function ensureAuth(){ return true; }
const DOMAINS=['east-china','north-china','south-china','west-china'];
const DOMAIN_CN={'east-china':'华东','north-china':'华北','south-china':'华南','west-china':'西南'};
const COLORS={'east-china':'#3b82f6','north-china':'#22c55e','south-china':'#f59e0b','west-china':'#a855f7'};

// XSS escape
function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=String(s);return d.innerHTML;}

let expandedDags={}, dagData={}, chart=null, topoNetwork=null;
let topoNodeDS=null, topoEdgeDS=null;   // global refs for live update
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

// Detect manual scroll up to pause auto-scroll
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

/* ── Helpers ── */
function $(id){return document.getElementById(id)}
let _opDepth=0;
function setAllBtns(disabled){document.querySelectorAll('.command-bar .btn').forEach(b=>b.disabled=disabled);}
function pushOp(){if(++_opDepth===1)setAllBtns(true);}
function popOp(){if(--_opDepth<=0){_opDepth=0;setAllBtns(false);}}
function log(msg){$('cmdStatus').innerHTML=`<span style="color:#475569">[${new Date().toLocaleTimeString()}]</span> ${msg}`;}
const TOAST_ICONS={success:'check_circle',error:'cancel',warning:'warning',info:'info'};
const TOAST_COLORS={success:'var(--green)',error:'var(--red)',warning:'var(--yellow)',info:'var(--accent)'};
function toast(msg,type='info'){
  const color=TOAST_COLORS[type]||TOAST_COLORS.info;
  const icon=TOAST_ICONS[type]||TOAST_ICONS.info;
  const el=document.createElement('div');el.className='toast-item';el.style.borderLeft=`3px solid ${color}`;
  el.innerHTML=`<span class="material-symbols-outlined" style="font-size:1em;vertical-align:middle;color:${color};margin-right:6px">${icon}</span>${msg}`;
  $('toast').prepend(el);setTimeout(()=>el.remove(),4000);
}
function friendlyError(e){
  const msg=e.message||String(e);
  const m=msg.match(/HTTP\s*(\d+)/);
  if(m){
    const code=+m[1];
    const map={401:'认证失败，请检查 Token 配置',403:'权限不足',404:'接口不存在，请检查服务状态',408:'请求超时',429:'请求过于频繁，请稍后重试',500:'服务器内部错误',502:'网关错误',503:'服务不可用，请检查后端是否启动',504:'网关超时'};
    return map[code]||`HTTP ${code} 错误`;
  }
  if(msg.includes('Failed to fetch')||msg.includes('NetworkError'))return '网络连接失败，请检查后端服务是否运行';
  if(msg.includes('ECONNREFUSED'))return '连接被拒绝，请检查服务端口';
  return msg;
}
async function getJSON(url){
  const r=await fetch(url,{headers:AUTH});if(!r.ok)throw Error(r.status);return r.json();
}
async function postJSON(url,body){
  return fetch(url,{method:'POST',headers:{'Content-Type':'application/json; charset=utf-8',...AUTH},body:JSON.stringify(body)});
}

/* ── NL / Fault ── */
async function sendNL(){
  const txt=$('nlInput').value.trim();if(!txt)return;
  pushOp();
  log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle">progress_activity</span> 发送指令: ${esc(txt)}`);
  try{
    // 记录当前 DAG 数量
    const beforeDags=Object.keys(dagData).length;
    const beforeDagsSet=new Set(Object.keys(dagData));
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

    // 轮询等待新 DAG 出现（最多 30 秒，orchestrator 含 LLM 解析可能较慢）
    let foundDag=null;
    for(let i=0;i<30;i++){
      const remain=30-i;
      log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--yellow)">progress_activity</span> 等待 orchestrator 处理... <span style="color:var(--muted)">(剩余 ${remain}s)</span>`);
      await new Promise(r=>setTimeout(r,1000));
      await loadDags();
      const dags=Object.keys(dagData);
      if(dags.length>beforeDags){
        // 找到最新的 DAG
        const newDag=dags.find(id=>!beforeDagsSet.has(id));
        foundDag=newDag||dags[dags.length-1];
        break;
      }
      // 检查是否有 DAG 变成 completed
      for(const id of dags){
        const d=dagData[id];
        if(d&&d.status==='completed'&&!d._notified){
          d._notified=true;
          foundDag=id;
          break;
        }
      }
      if(foundDag)break;
    }

    if(foundDag){
      const d=dagData[foundDag];
      const st=d?d.status:'unknown';
      if(st==='completed'){
        log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--green)">check_circle</span> DAG <b>${esc(foundDag)}</b> 已完成！点击查看详情`);
        toast(`DAG ${foundDag} 执行完成`,'success');
        // 自动展开这个 DAG
        expandedDags[foundDag]=true;
        renderDags(Object.values(dagData).map(d=>({dag_id:d.dag_id,status:d.status,definition:d.definition,description:d.description,submitted_at_ms:d.submitted_at_ms,finished_at_ms:d.finished_at_ms})));
      }else if(st==='running'||st==='pending'){
        log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--yellow)">schedule</span> DAG <b>${esc(foundDag)}</b> 执行中 (${st})...`);
        toast(`DAG ${foundDag} 执行中`,'warning');
        expandedDags[foundDag]=true;
      }else{
        log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">error</span> DAG <b>${esc(foundDag)}</b> 状态: ${st}`);
      }
    }else{
      log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--muted)">info</span> 指令已投递（未检测到新 DAG，orchestrator 可能直接处理）`);
    }
  }catch(e){
    const errMsg=friendlyError(e);
    log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">cancel</span> ${esc(errMsg)}`);
    toast('发送失败: '+errMsg,'error');
  }finally{
    popOp();
  }
}

async function demoFault(){
  pushOp();
  const ft=$('faultType').value, dom=$('faultDomain').value;
  // Map domain to device/link target based on fault type
  const DEVICE_MAP={'east-china':'Edge-R1','north-china':'Edge-R2','south-china':'Edge-R3','west-china':'Edge-R4'};
  const device=DEVICE_MAP[dom]||dom;
  const DEVICE_FAULTS=['cpu_overload','misconfig','device_failure','ddos'];
  const target=DEVICE_FAULTS.includes(ft) ? device : `Core-Router->${device}`;
  try{
    const r=await fetch(`${SIM}/simulator/fault/inject?fault_type=${ft}&target=${encodeURIComponent(target)}`,{method:'POST'});
    if(!r.ok)throw Error(r.status);
    const d=await r.json();
    if(d.success||d.status==='ok'){
      log(`<span class="material-symbols-outlined" style="font-size:1em;vertical-align:middle;color:var(--yellow)">bolt</span> 已注入 ${ft} → ${device} (${DOMAIN_CN[dom]||dom})`);
      toast(`故障注入: ${ft} @ ${DOMAIN_CN[dom]||dom}`,'warning');
    }else{
      throw Error(d.error||d.message||'unknown');
    }
  }catch(e){
    const errMsg=friendlyError(e);
    log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--red)">cancel</span> 注入失败: ${esc(errMsg)}`);
  }finally{
    popOp();
  }
}

async function clearAllFaults(skipConfirm){
  if(!skipConfirm && !confirm('确认清除所有故障？'))return;
  try{
    // 先查询当前活跃故障数量
    let activeCount=0;
    try{const f=await getJSON(`${SIM}/simulator/faults`);activeCount=(f.faults||f.active||[]).length;}catch(e){}
    await getJSON(`${SIM}/simulator/fault/clear_all`);
    if(activeCount>0){
      log(`<span class="material-symbols-outlined" style="font-size:1em;vertical-align:middle;color:var(--green)">delete_sweep</span> 已清除 ${activeCount} 个故障`);
      toast(`已清除 ${activeCount} 个故障`,'success');
      // 立即刷新拓扑（不等 Simulator WS 下一轮推送）
      setTimeout(refreshTopoFromSimulator, 500);
    }else{
      log('<span class="material-symbols-outlined" style="font-size:1em;vertical-align:middle;color:var(--muted)">info</span> 当前无活跃故障');
      toast('当前无故障','info');
    }
  }catch(e){log('清除故障失败: '+esc(e.message));}
}

async function runDemo(){
  if(!confirm('即将执行一键演示（清除故障→注入链路拥塞→全流程诊断修复），是否继续？'))return;
  pushOp();
  toast('一键演示启动: 清除故障→注入→执行→报告','info');
  try{
  await clearAllFaults(true);
  await new Promise(r=>setTimeout(r,800));
  $('faultType').value='link_congestion';
  $('faultDomain').value='east-china';
  await demoFault();
  await new Promise(r=>setTimeout(r,1000));
  $('nlInput').value='华东地区网络延迟异常，请全流程诊断修复';
  await sendNL();
  }catch(e){
    toast('演示执行失败: '+e.message,'error');
  }finally{
    popOp();
  }
}

// 复合故障场景演示
async function runMultiFaultDemo(){
  if(!confirm('即将注入多域复合故障（华东链路拥塞 + 华北DDoS + 华南配置错误），是否继续？'))return;
  pushOp();
  try{
    toast('复合故障演示: 多域同时故障→逐域修复','warning');
    log('<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle">warning</span> 开始复合故障演示...');
    try{await clearAllFaults(true);}catch(e){log('清除故障失败: '+esc(e.message));}
    await new Promise(r=>setTimeout(r,500));

    // Inject all faults first
    $('faultType').value='link_congestion'; $('faultDomain').value='east-china';
    try{await demoFault();}catch(e){log('华东故障注入失败: '+esc(e.message));}
    await new Promise(r=>setTimeout(r,300));

    $('faultType').value='ddos'; $('faultDomain').value='north-china';
    try{await demoFault();}catch(e){log('华北故障注入失败: '+esc(e.message));}
    await new Promise(r=>setTimeout(r,300));

    $('faultType').value='misconfig'; $('faultDomain').value='south-china';
    try{await demoFault();}catch(e){log('华南故障注入失败: '+esc(e.message));}
    await new Promise(r=>setTimeout(r,800));

    // Send individual NL commands for each faulted region
    const regions = [
      {domain:'east-china', cn:'华东', fault:'链路拥塞'},
      {domain:'north-china', cn:'华北', fault:'DDoS攻击'},
      {domain:'south-china', cn:'华南', fault:'配置错误'}
    ];
    for (const r of regions) {
      log(`<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--yellow)">send</span> 正在修复 ${r.cn}地区${r.fault}...`);
      $('nlInput').value = r.cn + '地区' + r.fault + '，请全流程诊断修复';
      await sendNL();
      // Wait for DAG to complete before next
      await new Promise(resolve=>setTimeout(resolve, 8000));
    }

    log('<span class="material-symbols-outlined" style="font-size:0.9em;vertical-align:middle;color:var(--green)">check_circle</span> 复合故障演示完成！');
    toast('复合故障演示完成','success');
  }catch(e){
    toast('复合故障演示失败: '+e.message,'error');
  }finally{
    popOp();
  }
}

/* ── Topology ── */
const EDGE_MAP={'Core-Router':'Core','Edge-R1':'Edge-east-china','Edge-R2':'Edge-north-china','Edge-R3':'Edge-south-china','Edge-R4':'Edge-west-china'};
const REVERSE_EDGE_MAP=Object.fromEntries(Object.entries(EDGE_MAP).map(([k,v])=>[v,k]));

function initTopo(){
  const nodes=[],edges=[];
  nodes.push({id:'Core',label:'Core-Router',shape:'box',size:30,
    color:{background:'#1e3a5f',border:'#38bdf8'},font:{color:'#e2e8f0',size:13,weight:'bold'}});
  for(const d of DOMAINS){
    const eid='Edge-'+d;
    const lbl=DOMAIN_CN[d]+' Edge';
    const color=COLORS[d];
    nodes.push({id:eid,label:lbl,shape:'box',size:22,
      color:{background:color,border:color},font:{color:'#e2e8f0',size:11}});
    edges.push({id:`e-Core-${eid}`,from:'Core',to:eid,arrows:'to',color:{color:color,opacity:0.5},width:2.5,arrowStrikethrough:false});
    for(let i=1;i<=3;i++){
      const sid='srv-'+d+'-'+i;
      nodes.push({id:sid,label:'Srv'+i,shape:'dot',size:10,
        color:{background:color+'55',border:color},font:{color:'#64748b',size:8}});
      edges.push({id:`e-${eid}-${sid}`,from:eid,to:sid,color:{color:'#1e293b'},width:1,dashes:[4,4]});
    }
  }
  topoNodeDS=new vis.DataSet(nodes);
  topoEdgeDS=new vis.DataSet(edges);
  topoNetwork=new vis.Network($('topo'),{nodes:topoNodeDS,edges:topoEdgeDS},{
    physics:{solver:'barnesHut',barnesHut:{gravitationalConstant:-1800,centralGravity:0.3,springLength:120,springConstant:0.05,damping:0.09},stabilization:{iterations:120}},
    interaction:{hover:true,zoomView:true,dragView:true},
    edges:{arrows:{to:{enabled:true,scaleFactor:0.6}},smooth:{type:'curvedCW',roundness:0.2}}
  });
  // 物理模拟完成后提示用户拓扑已稳定
  topoNetwork.on('stabilizationIterationsDone',()=>{
    const lbl=$('topoLabel');
    if(lbl)lbl.textContent='拓扑已稳定';
  });
}

/* Map simulator device/link IDs → GUI topology node IDs */
function deviceToNodeId(target){
  if(!target)return null;
  if(target==='Core-Router')return 'Core';
  if(EDGE_MAP[target])return EDGE_MAP[target];
  // srv-east-china-1 → same format
  if(target.startsWith('srv-'))return target;
  return null;
}

function linkToEdgeIds(target){
  // "Core-Router->Edge-R1" → {from:'Core',to:'Edge-east-china'}
  if(!target)return null;
  const parts=target.split('->');
  if(parts.length!==2)return null;
  const from=deviceToNodeId(parts[0]), to=deviceToNodeId(parts[1]);
  if(!from||!to)return null;
  // Find edge id
  const edgeId=`e-${from}-${to}`;
  return {edgeId,from,to};
}

/* ── Update topo visual from metrics + faults ── */
function updateTopoStatus(regions,faults,agents){
  if(!topoNodeDS||!topoEdgeDS)return;
  const hasFaults=faults&&faults.length>0;

  // 1. Reset all to default first
  const defaultCoreBg='#1e3a5f',defaultCoreBorder='#38bdf8';
  topoNodeDS.update({id:'Core',color:{background:defaultCoreBg,border:defaultCoreBorder},font:{color:'#e2e8f0',size:13,weight:'bold'}});
  for(const d of DOMAINS){
    const eid='Edge-'+d;
    const c=COLORS[d];
    topoNodeDS.update({id:eid,color:{background:c,border:c},font:{color:'#e2e8f0',size:11},size:22});
    for(let i=1;i<=3;i++){
      const sid='srv-'+d+'-'+i;
      topoNodeDS.update({id:sid,color:{background:c+'55',border:c},font:{color:'#64748b',size:8},size:10});
    }
    topoEdgeDS.update({id:`e-Core-${eid}`,color:{color:c,opacity:0.8},width:2.5,dashes:false});
    for(let i=1;i<=3;i++){
      topoEdgeDS.update({id:`e-${eid}-srv-${d}-${i}`,color:{color:'#1e293b'},width:1,dashes:[4,4]});
    }
  }

  // 2. Collect fault-affected nodes/edges so we skip metrics-only coloring for them
  const faultedNodes=new Set();
  const faultedEdges=new Set();

  // 3. Apply fault overlays first (higher priority)
  if(hasFaults){
    updateFaultIndicator(faults);
    for(const f of faults){
      const tgt=f.target||'';
      const li=linkToEdgeIds(tgt);
      if(li){
        const c='#ef4444';
        const w=f.type==='link_outage'?5:4;
        topoEdgeDS.update({id:li.edgeId,color:{color:c,opacity:1},width:w,dashes:f.type==='link_outage'?[2,4]:false});
        faultedEdges.add(li.edgeId);
        if(li.to && !faultedNodes.has(li.to)){
          topoNodeDS.update({id:li.to,color:{background:'#ef4444',border:'#f87171'},size:28,font:{color:'#fff',size:12,weight:'bold'}});
          faultedNodes.add(li.to);
        }
        if(li.from && !faultedNodes.has(li.from)){
          topoNodeDS.update({id:li.from,color:{background:'#7f1d1d',border:'#ef4444'},size:26,font:{color:'#fca5a5',size:12,weight:'bold'}});
          faultedNodes.add(li.from);
        }
      }
      const ni=deviceToNodeId(tgt);
      if(ni){
        topoNodeDS.update({id:ni,color:{background:'#ef4444',border:'#f87171'},size:28,font:{color:'#fff',weight:'bold'}});
        faultedNodes.add(ni);
      }
    }
  } else {
    updateFaultIndicator([]);
  }

  // 4. Apply metrics severity to non-faulted edge nodes
  if(regions){
    for(const d of DOMAINS){
      const r=regions[d];if(!r)continue;
      const eid='Edge-'+d;
      if(faultedNodes.has(eid))continue;  // fault styling takes priority
      const lat=r.latency_ms||0, loss=(r.packet_loss||0)*100, bw=(r.bandwidth_util||0)*100;
      const worst=(lat>=100||bw>=90||loss>=5)?'crit':(lat>=50||bw>=70||loss>=1)?'warn':'ok';
      if(worst!=='ok'){
        const bg=worst==='crit'?'#ef4444':'#f59e0b';
        const border=worst==='crit'?'#f87171':'#fbbf24';
        topoNodeDS.update({id:eid,color:{background:bg,border},size:26,font:{color:'#fff',size:11,weight:'bold'}});
      }
      // Also tint the Core→Edge link by severity (if not faulted)
      const edgeId=`e-Core-${eid}`;
      if(!faultedEdges.has(edgeId)&&worst!=='ok'){
        const c=worst==='crit'?'#ef4444':'#f59e0b';
        topoEdgeDS.update({id:edgeId,color:{color:c,opacity:0.8},width:worst==='crit'?4:3});
      }
    }
  }
}


/* ── Metrics ── */
function level(v,lo,hi){return v>=hi?'crit':v>=lo?'warn':'ok';}

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
    // History
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
      // Parse capabilities - might be string or array
      let caps=[];
      try{
        caps=typeof a.capabilities==='string'?JSON.parse(a.capabilities):(a.capabilities||[]);
      }catch(e){caps=[];}
      const capsHtml=caps.map(c=>`<span class="agent-cap">${esc(c)}</span>`).join('');
      const load=((a.load||0)*100).toFixed(0);
      // 若负载为 0% 但 agent 在线，显示为"待命中"而非 0%
      const loadDisplay = a.load > 0 ? `${load}%` :
                          `<span style="color:var(--muted);font-style:italic">待命中</span>`;
      html+=`<tr><td><span class="agent-dot ${st}"></span>${st==='up'?'在线':'离线'}</td>
        <td>${esc(a.agent_id)}</td><td>${esc(DOMAIN_CN[a.domain]||a.domain)}</td>
        <td>${capsHtml}</td><td>${loadDisplay}</td></tr>`;
    }
    html+='</table>';
    $('agentTable').innerHTML=html;
  }catch(e){console.warn('loadAgents error:',friendlyError(e));}
}

/* ── DAG ── */
async function loadDags(){
  try{
    const d=await getJSON(`${API}/dag?limit=20`);
    const dags=d.dags||[];
    $('dagCount').textContent=dags.length;
    // 使用批量接口一次性获取所有 DAG 详情（避免 N+1 查询）
    dagData={};
    if(dags.length>0){
      const ids=dags.map(g=>g.dag_id).join(',');
      try{
        const batch=await getJSON(`${API}/dag/batch?ids=${encodeURIComponent(ids)}`);
        const details=batch.dags||[];
        for(const dt of details){
          dagData[dt.dag_id]=dt;
        }
      }catch(e){
        console.error('DAG batch error:',e);
        // 降级：逐个获取
        for(const g of dags){
          try{
            const detail=await getJSON(`${API}/dag/${g.dag_id}`);
            dagData[g.dag_id]=detail;
          }catch(e2){
            console.error('DAG detail error:',g.dag_id,e2);
          }
        }
      }
    }
    renderDags(dags);
  }catch(e){
    console.warn('loadDags error:',friendlyError(e));
    $('dagList').innerHTML=`<div style="color:var(--red);font-size:0.8em;padding:10px">加载失败: ${esc(friendlyError(e))}</div>`;
  }
}

function renderDags(dags){
  let html='';
  for(const g of dags){
    const st=g.status||'pending';
    const expanded=expandedDags[g.dag_id];
    const detail=dagData[g.dag_id];
    const nodes=detail?detail.nodes||[]:[];
    // 解析 definition JSON 获取 description
    let desc=g.description||'';
    if(!desc&&g.definition){
      try{desc=JSON.parse(g.definition).description||'';}catch(e){}
    }
    // 解析 result JSON 获取报告
    let resultObj=null;
    const rawResult=detail?.result||g.result||null;
    if(rawResult){
      try{resultObj=typeof rawResult==='string'?JSON.parse(rawResult):rawResult;}catch(e){}
    }
    // 计算耗时
    let duration='';
    if(g.submitted_at_ms){
      const end=g.finished_at_ms||Date.now();
      const ms=end-g.submitted_at_ms;
      duration=ms>1000?(ms/1000).toFixed(1)+'s':ms+'ms';
    }
    html+=`<div class="dag-item ${expanded?'active':''}" onclick="toggleDag('${esc(g.dag_id)}')">
      <div class="dag-header">
        <span class="dag-id">${esc(g.dag_id)}</span>
        <span class="dag-status ${st}">${st}</span>
        ${duration?`<span style="font-size:0.65em;color:var(--muted)">${duration}</span>`:''}
      </div>
      <div class="dag-desc" title="${esc(desc)}">${esc(desc)}</div>`;
    // 失败时显示错误
    if(st==='failed'&&rawResult){
      const errText=typeof rawResult==='string'?rawResult:(rawResult.error||rawResult.message||'');
      if(errText)html+=`<div style="font-size:0.6em;color:var(--red);margin-top:2px">${esc(errText)}</div>`;
    }
    if(expanded){
      // 显示执行报告摘要（narrative）
      if(resultObj){
        // 从 nodes 中提取报告节点
        const nodes=resultObj.nodes||{};
        const reportNode=Object.values(nodes).find(n=>n.narrative||n.summary);
        if(reportNode){
          const narrative=reportNode.narrative||'';
          const summary=reportNode.summary||{};
          html+=`<div style="margin-top:8px;padding:8px;background:#0a0f1e;border-radius:6px;border:1px solid #1e3a5f;font-size:0.7em">
            <div style="color:var(--accent);font-weight:600;margin-bottom:4px;display:flex;align-items:center;gap:4px">
              <span class="material-symbols-outlined" style="font-size:1em">summarize</span> 执行报告
            </div>
            <div style="color:var(--text);line-height:1.5">${esc(narrative)}</div>`;
          // 显示诊断摘要
          if(summary.diagnosis_description){
            html+=`<div style="margin-top:6px;color:var(--muted)">
              <b>诊断:</b> ${esc(summary.diagnosis_description)} <span style="color:var(--accent)">(${((summary.diagnosis_confidence||0)*100).toFixed(0)}% 置信度)</span>
            </div>`;
          }
          // 显示修复状态
          if(summary.repair_action!==undefined){
            const repairOk=summary.repair_success;
            html+=`<div style="margin-top:3px;color:${repairOk?'var(--green)':'var(--yellow)'}">
              <b>修复:</b> ${summary.repair_action?esc(summary.repair_action):'无需修复'} ${repairOk?'✅':'⚠️'}
            </div>`;
          }
          html+='</div>';
        }
        // 显示各节点执行详情
        const nodeEntries=Object.entries(nodes).filter(([k,v])=>!k.startsWith('rpt-'));
        if(nodeEntries.length){
          html+=`<div style="margin-top:6px;font-size:0.65em">
            <div style="color:var(--muted);margin-bottom:3px;font-weight:600">节点详情:</div>`;
          for([key,n] of nodeEntries){
            const nodeType=key.includes('mon-')?'📡 监控':key.includes('diag-')?'🔍 诊断':key.includes('fix-')?'🔧 修复':key.includes('ver-')?'✅ 验证':'📋 节点';
            let nodeInfo='';
            if(n.metrics)nodeInfo=`延迟 ${n.metrics.latency_ms?.toFixed(1)||'-'}ms`;
            if(n.diagnosis)nodeInfo=`${n.diagnosis.description||n.diagnosis.fault_type||'-'}`;
            if(n.repair_result)nodeInfo=`${n.repair_result.message||n.repair_strategy_used||'-'}`;
            if(n.verdict)nodeInfo=`${n.verdict} ${n.message||''}`;
            if(nodeInfo)html+=`<div style="color:var(--text);margin:2px 0">${nodeType}: ${esc(nodeInfo)}</div>`;
          }
          html+='</div>';
        }
      }
      // 显示节点流程图
      if(nodes.length){
        html+='<div class="dag-nodes" style="margin-top:6px">';
        for(let i=0;i<nodes.length;i++){
          const n=nodes[i];
          const nst=n.status||'pending';
          html+=`<div class="dag-node ${nst}">
            <div class="dag-node-name">${esc(n.node_id)}</div>
            <div class="dag-node-agent">${esc(n.assigned_agent||'-')}</div>
          </div>`;
          if(i<nodes.length-1)html+='<span class="dag-arrow">→</span>';
        }
        html+='</div>';
      }
      // 显示验证结果
      if(detail&&detail.verifications&&detail.verifications.length){
        html+='<div style="margin-top:6px;font-size:0.7em">';
        for(const v of detail.verifications){
          const vc=v.verdict==='pass'?'var(--green)':v.verdict==='fail'?'var(--red)':'var(--yellow)';
          html+=`<div style="color:${vc}">✓ 验证: ${esc(v.verdict||'unknown')} | 修复前: ${esc(v.metric_before||'-')} → 修复后: ${esc(v.metric_after||'-')}</div>`;
        }
        html+='</div>';
      }
    }
    html+='</div>';
  }
  $('dagList').innerHTML=html||'<div style="color:var(--muted);font-size:0.8em;padding:10px">暂无 DAG 记录</div>';
}

function toggleDag(id){
  expandedDags[id]=!expandedDags[id];
  // 只重新渲染，不重新请求（dagData 已缓存）
  const dags=Object.values(dagData).sort((a,b)=>(b.submitted_at_ms||0)-(a.submitted_at_ms||0));
  renderDags(dags);
}

/* ── Messages ── */
async function loadMessages(){
  try{
    const d=await getJSON(`${API}/messages?limit=50`);
    const msgs=d.messages||[];
    msgBuffer=msgs.reverse();
    renderMessages();
  }catch(e){
    console.warn('loadMessages error:',friendlyError(e));
    $('msgFlow').innerHTML=`<div style="color:var(--red);font-size:0.8em;padding:10px">加载失败: ${esc(friendlyError(e))}</div>`;
  }
}

function renderMessages(){
  let html='';
  const filtered=currentFilter==='all'?msgBuffer:msgBuffer.filter(m=>m.intent_type===currentFilter);
  for(const m of filtered.slice(-30)){
    const tp=m.intent_type||'task';
    html+=`<div class="msg-item">
      <span class="msg-type ${tp}">${tp}</span>
      <span class="msg-from">${esc(m.from_agent||'')}</span>
      <span class="msg-arrow">→</span>
      <span class="msg-to">${esc(m.to_agent||'*')}</span>
      <span class="msg-time">${m.ts_ms?new Date(m.ts_ms).toLocaleTimeString():''}</span>
    </div>`;
  }
  $('msgFlow').innerHTML=html||'<div style="color:var(--muted);font-size:0.8em;padding:10px">暂无消息</div>';
  $('msgCount').textContent=filtered.length;
  // 自动滚动到最新消息（仅在用户未手动向上滚动时）
  if(_autoScroll)$('msgFlow').scrollTop=$('msgFlow').scrollHeight;
}

function filterMsgs(type,el){
  currentFilter=type;
  document.querySelectorAll('.msg-filter').forEach(b=>b.classList.remove('active'));
  if(el)el.classList.add('active');
  renderMessages();
}

/* ── WebSocket ── */
let _dagLoadTimer=null;
function debouncedLoadDags(){
  clearTimeout(_dagLoadTimer);
  _dagLoadTimer=setTimeout(loadDags,300);
}

let _wsReconnDelay=3000;
function connectWS(){
  const wsUrl=`${API.replace('http','ws')}/ws/dashboard?token=${AUTH_TOKEN}`;
  const ws=new WebSocket(wsUrl);
  ws.onopen=()=>{
    $('wsDot').className='ok';
    $('wsLabel').textContent='控制';
    _wsReconnDelay=3000;
    toast('WebSocket 已连接','success');
  };
  ws.onmessage=(e)=>{
    try{
      const d=JSON.parse(e.data);
      if(d.type==='dashboard_ping'){
        $('wsDot').className='ok';
      }else       if(d.type==='dag_update'){
        debouncedLoadDags();
        if(d.status==='running'){
          showStageTimeline();
        }else if(d.status==='completed'){
          toast(`DAG ${d.dag_id} 已完成`,'success');
          refreshTopoFromSimulator();
          hideStageTimeline();
        }else if(d.status==='failed'){
          toast(`DAG ${d.dag_id} 失败`,'error');
          hideStageTimeline();
        }
      }else if(d.type==='node_update'){
        debouncedLoadDags();
        if(d.node_type && d.node_status){
          updateStage(d.node_type, d.node_status);
        }
      }
    }catch(err){console.warn('Dashboard WS parse error:',err);}
  };
  ws.onclose=()=>{
    $('wsDot').className='off';
    $('wsLabel').textContent='重连中...';
    toast('WebSocket 断开，正在重连...','warning');
    setTimeout(connectWS,_wsReconnDelay);
    _wsReconnDelay=Math.min(_wsReconnDelay*2,30000); // 指数退避，上限30s
  };
  ws.onerror=()=>ws.close();
}

/* ── Simulator WS ── */
let _simReconnDelay=3000;
function connectSimWS(){
  const ws=new WebSocket(`${API.replace('http','ws').replace(/:\d+$/,':8001')}/simulator/ws`);
  ws.onopen=()=>{
    console.log('Simulator WS connected');
    $('simDot').className='ok';
    $('simLabel').textContent='模拟器';
    _simReconnDelay=3000;
    startTopoRefreshLoop();
  };
  ws.onmessage=(e)=>{
    try{
      const d=JSON.parse(e.data);
      if(d.type==='metrics'&&d.data){
        const regions=d.data.regions||d.data;
        const faults=d.data.faults||[];
        updateMetrics(regions);
        updateTopoStatus(regions,faults,null);
      }
    }catch(err){console.warn('Simulator WS parse error:',err);}
  };
  ws.onclose=()=>{
    $('simDot').className='off';
    $('simLabel').textContent='重连中...';
    setTimeout(connectSimWS,_simReconnDelay);
    _simReconnDelay=Math.min(_simReconnDelay*2,30000);
  };
  ws.onerror=()=>ws.close();
}

/* ── Active topology refresh ── */
async function refreshTopoFromSimulator(){
  for(let attempt=0;attempt<3;attempt++){
    if(attempt>0) await new Promise(r=>setTimeout(r,500));
    try{
      const [metricsResp, faultsResp] = await Promise.all([
        fetch(`${SIM}/simulator/metrics`).then(r => r.ok ? r.json() : null),
        fetch(`${SIM}/simulator/faults`).then(r => r.ok ? r.json() : null)
      ]);
      const regions = metricsResp ? (metricsResp.metrics || metricsResp) : {};
      const faults = faultsResp ? (faultsResp.faults || []) : [];
      updateTopoStatus(regions, faults, null);
      return;
    }catch(e){
      if(attempt>=2)console.warn('refreshTopoFromSimulator failed');
    }
  }
}
let _topoRefreshTimer = null;
function startTopoRefreshLoop(){
  if(_topoRefreshTimer) return;
  _topoRefreshTimer = setInterval(refreshTopoFromSimulator, 5000);
}

/* ── Fault Indicator ── */
function updateFaultIndicator(faults){
  const fi = $('faultIndicator'), fc = $('faultCount');
  if(!fi||!fc)return;
  const count = faults ? faults.length : 0;
  if(count > 0){
    fi.className = 'fault-indicator active';
    fc.innerHTML = '<span class="dot fault-pulse"></span> ' + count + '个活跃故障';
  } else {
    fi.className = 'fault-indicator clear';
    fc.innerHTML = '无故障';
  }
}

/* ── Stage Timeline ── */
let _stageStartMs = 0, _stageTimer = null;
function showStageTimeline(){
  const sb = $('stageBar');
  if(sb){ sb.style.display = 'flex'; sb.querySelectorAll('.stage-item').forEach(el=>el.className='stage-item'); }
  _stageStartMs = Date.now();
  if(_stageTimer) clearInterval(_stageTimer);
  _stageTimer = setInterval(()=>{
    const el = $('stageElapsed');
    if(el) el.textContent = ((Date.now()-_stageStartMs)/1000).toFixed(1)+'s';
  }, 200);
}
function updateStage(nodeType, nodeStatus){
  const sb = $('stageBar');
  if(!sb)return;
  const stageMap = {monitor:'monitor',diagnose:'diagnose',repair:'repair',verify:'verify',report:'report'};
  const stageKey = stageMap[nodeType];
  if(!stageKey)return;
  const el = sb.querySelector('[data-stage="'+stageKey+'"]');
  if(!el)return;
  if(nodeStatus === 'running') el.className = 'stage-item active';
  else if(nodeStatus === 'completed') el.className = 'stage-item done';
  else if(nodeStatus === 'failed') el.className = 'stage-item failed';
}
function hideStageTimeline(){
  if(_stageTimer){ clearInterval(_stageTimer); _stageTimer = null; }
  const el = $('stageElapsed');
  if(el) el.textContent = ((Date.now()-_stageStartMs)/1000).toFixed(1)+'s';
  setTimeout(()=>{ const sb = $('stageBar'); if(sb) sb.style.display = 'none'; }, 3000);
}

/* ── Metrics Flash ── */
let _lastMetricValues = {};
function flashMetrics(regions){
  for(const d of DOMAINS){
    const r = regions[d]; if(!r)continue;
    const prev = _lastMetricValues[d]||{};
    ['latency_ms','bandwidth_util','packet_loss'].forEach(k=>{
      const newVal = r[k]||0, oldVal = prev[k]||0;
      const change = oldVal>0 ? Math.abs(newVal-oldVal)/oldVal : 0;
      if(change > 0.15){
        const cellId = 'm-'+d+'-'+k, el = $(cellId);
        if(el){ el.classList.remove('metric-flash'); void el.offsetWidth; el.classList.add('metric-flash'); }
      }
    });
    _lastMetricValues[d] = {latency_ms:r.latency_ms||0, bandwidth_util:r.bandwidth_util||0, packet_loss:r.packet_loss||0};
  }
}

/* ── Clock ── */
function updateClock(){
  $('clock').textContent=new Date().toLocaleTimeString();
}

/* ── Init ── */
async function init(){
  try{
    if(typeof vis!=='undefined')initTopo();
    else{$('topo').innerHTML='<div style="color:var(--red);padding:20px;text-align:center">拓扑图库加载失败，请刷新页面</div>';}
  }catch(e){console.warn('initTopo failed:',e);}
  try{
    if(typeof Chart!=='undefined')initChart();
    else{document.querySelector('.chart-container').innerHTML='<div style="color:var(--red);padding:20px;text-align:center">图表库加载失败</div>';}
  }catch(e){console.warn('initChart failed:',e);}
  updateClock();
  setInterval(updateClock,1000);
  setInterval(loadAgents,10000);
  setInterval(loadDags,5000);
  setInterval(loadMessages,3000);

  try{await Promise.all([loadAgents(),loadDags(),loadMessages()]);}catch(e){console.warn('init load failed:',e);}
  connectWS();
  connectSimWS();

  // Fade out loading overlay
  const overlay=$('loadingOverlay');
  if(overlay){
    overlay.classList.add('fade-out');
    setTimeout(()=>overlay.remove(),600);
  }
}

init();
