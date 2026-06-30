
// ---------- perspective grid ----------
function buildGrid(){
  const svg=document.getElementById('grid');
  const W=window.innerWidth,H=window.innerHeight;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const vx=W*0.5, vy=H*0.46;
  let p='';
  const N=26;
  for(let i=1;i<=N;i++){
    const t=i/N, e=Math.pow(t,1.7);
    const x0=vx-vx*e, y0=vy-vy*e, x1=vx+(W-vx)*e, y1=vy+(H-vy)*e;
    const op=(0.10+0.55*(1-t)).toFixed(3);
    p+=`<rect x="${x0.toFixed(1)}" y="${y0.toFixed(1)}" width="${(x1-x0).toFixed(1)}" height="${(y1-y0).toFixed(1)}" fill="none" stroke="var(--grid)" stroke-width="1" opacity="${op}"/>`;
  }
  // radial spokes to the window perimeter (even spacing along edges)
  const pts=[]; const stepX=W/26, stepY=H/18;
  for(let x=0;x<=W;x+=stepX){pts.push([x,0]);pts.push([x,H]);}
  for(let y=0;y<=H;y+=stepY){pts.push([0,y]);pts.push([W,y]);}
  for(const [x,y] of pts){
    p+=`<line x1="${vx}" y1="${vy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="var(--grid)" stroke-width="1" opacity="0.14"/>`;
  }
  // center glow
  p+=`<rect x="${(vx-70)}" y="${(vy-46)}" width="140" height="92" fill="url(#cg)"/>`;
  p=`<defs><radialGradient id="cg" cx="50%" cy="50%" r="60%"><stop offset="0%" stop-color="var(--glow)" stop-opacity="0.9"/><stop offset="60%" stop-color="var(--glow)" stop-opacity="0.25"/><stop offset="100%" stop-color="var(--glow)" stop-opacity="0"/></radialGradient></defs>`+p;
  svg.innerHTML=p;
}
buildGrid(); addEventListener('resize',buildGrid);

// ---------- theme ----------
document.getElementById('themebtn').onclick=()=>{
  const h=document.documentElement;
  h.dataset.theme = h.dataset.theme==='dark' ? 'light' : 'dark';
  buildGrid();
};

// ---------- show/hide password ----------
document.getElementById('eye').onclick=()=>{
  const i=document.getElementById('password');
  i.type = i.type==='password' ? 'text' : 'password';
};

// ---------- mode switch ----------
let mode='signin';
function setMode(m){
  mode=m; document.body.dataset.mode=m;
  const signup=m==='signup';
  document.getElementById('title').textContent = signup?'Create your account':'Welcome back';
  document.getElementById('csub').textContent = signup?'It only takes a moment to get started':'Sign in to continue to QA Studio';
  document.getElementById('btnlabel').textContent = signup?'Create account':'Sign in';
  document.getElementById('alttext').textContent = signup?'Already have an account?':'New to QA Studio?';
  document.getElementById('switch').textContent = signup?'Sign in':'Create one';
  document.getElementById('forgotrow').style.display = signup?'none':'flex';
  hideMsg();
}
document.getElementById('switch').onclick=()=>setMode(mode==='signin'?'signup':'signin');

// ---------- messages ----------
const msgEl=document.getElementById('msg');
function showMsg(kind,text){msgEl.className='msg show '+kind;msgEl.textContent=text;}
function hideMsg(){msgEl.className='msg';}
function setBusy(b){document.getElementById('submit').classList.toggle('busy',b);}

// ---------- bridge contract ----------
// The host (pywebview / Flet) injects window.__qaSubmit(payload) and may call
// window.__qaResult({ok,message,kind}) back. Without a host we run a demo.
function submitForm(){
  hideMsg();
  const email=document.getElementById('email').value.trim();
  const password=document.getElementById('password').value;
  const name=document.getElementById('name').value.trim();
  if(!email||!password){showMsg('err','Enter your email and password.');return;}
  setBusy(true);
  const payload={action:mode,email,password,name};
  if(window.__qaSubmit){ try{window.__qaSubmit(payload);}catch(e){setBusy(false);showMsg('err',''+e);} }
  else { setTimeout(()=>{setBusy(false);showMsg('ok','Demo mode — host bridge not connected. Payload: '+JSON.stringify({action:mode,email}));},700); }
}
window.__qaResult=function(r){ setBusy(false); if(r&&r.message) showMsg(r.kind||(r.ok?'ok':'err'), r.message); if(r&&r.ok&&r.action==='signup') setMode('signin'); };

document.getElementById('card').addEventListener('submit',e=>{e.preventDefault();submitForm();});
document.getElementById('forgot').onclick=()=>{
  const email=document.getElementById('email').value.trim();
  if(!email){showMsg('err','Enter your email above first, then tap Forgot password.');return;}
  setBusy(true);
  if(window.__qaForgot){try{window.__qaForgot({email});}catch(e){setBusy(false);showMsg('err',''+e);}}
  else setTimeout(()=>{setBusy(false);showMsg('ok','Demo — reset link would be sent to '+email);},700);
};
