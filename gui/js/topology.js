/* gui/js/topology.js — 拓扑图渲染、指标刷新、故障指示器 */

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
  topoNetwork.on('stabilizationIterationsDone',()=>{
    const lbl=$('topoLabel');
    if(lbl)lbl.textContent='拓扑已稳定';
  });
}

function deviceToNodeId(target){
  if(!target)return null;
  if(target==='Core-Router')return 'Core';
  if(EDGE_MAP[target])return EDGE_MAP[target];
  if(target.startsWith('srv-'))return target;
  return null;
}

function linkToEdgeIds(target){
  if(!target)return null;
  const parts=target.split('->');
  if(parts.length!==2)return null;
  const from=deviceToNodeId(parts[0]), to=deviceToNodeId(parts[1]);
  if(!from||!to)return null;
  const edgeId=`e-${from}-${to}`;
  return {edgeId,from,to};
}

function updateTopoStatus(regions,faults,agents){
  if(!topoNodeDS||!topoEdgeDS)return;
  const hasFaults=faults&&faults.length>0;
  const defaultCoreBg='#1e3a5f',defaultCoreBorder='#38bdf8';
  topoNodeDS.update({id:'Core',color:{background:defaultCoreBg,border:defaultCoreBorder},font:{color:'#e2e8f0',size:13,weight:'bold'}});
  for(const d of DOMAINS){
    const eid='Edge-'+d; const c=COLORS[d];
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
  const faultedNodes=new Set();
  const faultedEdges=new Set();
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
        if(li.to&&!faultedNodes.has(li.to)){
          topoNodeDS.update({id:li.to,color:{background:'#ef4444',border:'#f87171'},size:28,font:{color:'#fff',size:12,weight:'bold'}});
          faultedNodes.add(li.to);
        }
        if(li.from&&!faultedNodes.has(li.from)){
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
  } else {updateFaultIndicator([]);}
  if(regions){
    for(const d of DOMAINS){
      const r=regions[d];if(!r)continue;
      const eid='Edge-'+d;
      if(faultedNodes.has(eid))continue;
      const lat=r.latency_ms||0, loss=(r.packet_loss||0)*100, bw=(r.bandwidth_util||0)*100;
      const worst=(lat>=100||bw>=90||loss>=5)?'crit':(lat>=50||bw>=70||loss>=1)?'warn':'ok';
      if(worst!=='ok'){
        const bg=worst==='crit'?'#ef4444':'#f59e0b';
        const border=worst==='crit'?'#f87171':'#fbbf24';
        topoNodeDS.update({id:eid,color:{background:bg,border},size:26,font:{color:'#fff',size:11,weight:'bold'}});
      }
      const edgeId=`e-Core-${eid}`;
      if(!faultedEdges.has(edgeId)&&worst!=='ok'){
        const c=worst==='crit'?'#ef4444':'#f59e0b';
        topoEdgeDS.update({id:edgeId,color:{color:c,opacity:0.8},width:worst==='crit'?4:3});
      }
    }
  }
}

async function refreshTopoFromSimulator(){
  for(let attempt=0;attempt<3;attempt++){
    if(attempt>0) await new Promise(r=>setTimeout(r,500));
    try{
      const [metricsResp, faultsResp] = await Promise.all([
        fetch(`${SIM}/simulator/metrics`).then(r => r.ok ? r.json() : null),
        fetch(`${SIM}/simulator/faults`).then(r => r.ok ? r.json() : null)
      ]);
      if(metricsResp){
        updateMetrics(metricsResp);
        updateTopoStatus(metricsResp.metrics||{}, faultsResp?.faults||[], null);
      }
      return;
    }catch(e){console.warn('refreshTopo attempt',attempt+1,'failed:',friendlyError(e));}
  }
}

let _topoRefreshTimer = null;
function startTopoRefreshLoop(){
  if(_topoRefreshTimer) return;
  _topoRefreshTimer = setInterval(refreshTopoFromSimulator, 5000);
}

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

function level(v,lo,hi){return v>=hi?'crit':v>=lo?'warn':'ok';}
let _lastMetricValues = {};
function flashMetrics(regions){
  if(!regions)return;
  for(const d of DOMAINS){
    const r=regions[d];if(!r)continue;
    ['latency_ms','packet_loss','bandwidth_util'].forEach(k=>{
      const cellId = 'm-'+d+'-'+k, el = $(cellId);
      if(el){ el.classList.remove('metric-flash'); void el.offsetWidth; el.classList.add('metric-flash'); }
    });
    _lastMetricValues[d] = {latency_ms:r.latency_ms||0, bandwidth_util:r.bandwidth_util||0, packet_loss:r.packet_loss||0};
  }
}
