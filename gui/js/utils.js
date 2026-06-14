/* gui/js/utils.js — 通用工具函数 */

// DOM
function $(id){return document.getElementById(id)}

// XSS escape
function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=String(s);return d.innerHTML;}

// Operation depth — disable buttons during operation
let _opDepth=0;
function setAllBtns(disabled){document.querySelectorAll('.command-bar .btn').forEach(b=>b.disabled=disabled);}
function pushOp(){if(++_opDepth===1)setAllBtns(true);}
function popOp(){if(--_opDepth<=0){_opDepth=0;setAllBtns(false);}}

// Status log
function log(msg){$('cmdStatus').innerHTML=`<span style="color:#475569">[${new Date().toLocaleTimeString()}]</span> ${msg}`;}

// Toast
const TOAST_ICONS={success:'check_circle',error:'cancel',warning:'warning',info:'info'};
const TOAST_COLORS={success:'var(--green)',error:'var(--red)',warning:'var(--yellow)',info:'var(--accent)'};
function toast(msg,type='info'){
  const color=TOAST_COLORS[type]||TOAST_COLORS.info;
  const icon=TOAST_ICONS[type]||TOAST_ICONS.info;
  const el=document.createElement('div');el.className='toast-item';el.style.borderLeft=`3px solid ${color}`;
  el.innerHTML=`<span class="material-symbols-outlined" style="font-size:1em;vertical-align:middle;color:${color};margin-right:6px">${icon}</span>${msg}`;
  $('toast').prepend(el);setTimeout(()=>el.remove(),4000);
}

// Error decoding
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

// HTTP helpers
async function getJSON(url){
  const r=await fetch(url,{headers:AUTH});if(!r.ok)throw Error(r.status);return r.json();
}
async function postJSON(url,body){
  return fetch(url,{method:'POST',headers:{'Content-Type':'application/json; charset=utf-8',...AUTH},body:JSON.stringify(body)});
}
