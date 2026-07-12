"""
Local-network desktop remote: watch and lightly control this PC from a phone browser.

Run:   python desktop_remote.py
Then on your phone (same wifi) open:  http://<this-pc-ip>:8000

Design: the phone tells the server which rectangle of the screen it is looking at.
When you zoom in, the server captures only that rectangle at native resolution, so
text stays crisp. Bandwidth is bounded because only one screen of pixels is ever sent.
"""

import argparse
import asyncio
import io
import json

import mss
import pyautogui
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from PIL import Image

# Remote clicks can land in a screen corner; pyautogui's corner failsafe would raise
# on that, so it is disabled deliberately for this local tool.
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

MONITOR = 1        # mss monitor index (1 = primary)
FPS = 10           # frames per second sent to the phone
QUALITY = 70       # JPEG quality
MAX_W = 1600       # downscale a sent frame if wider than this (bounds bandwidth when zoomed out)

app = FastAPI()

HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Desktop Remote</title>
<style>
  html,body{margin:0}
  body{display:flex;flex-direction:column;height:100dvh;background:#000;color:#ddd;font-family:system-ui,sans-serif;overflow:hidden;-webkit-user-select:none;user-select:none}
  #stage{flex:1;min-height:0;position:relative;display:flex;align-items:center;justify-content:center;touch-action:none;overflow:hidden}
  #screen{max-width:100%;max-height:100%;display:block;image-rendering:pixelated}
  #bar{flex:0 0 auto;background:#111;display:flex;flex-direction:column;gap:5px;padding:6px;box-sizing:border-box}
  .row{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
  #mons{display:flex;gap:5px}
  input#txt{flex:1 1 120px;min-width:0;font-size:16px;padding:8px;border-radius:6px;border:1px solid #444;background:#222;color:#eee}
  button{font-size:14px;padding:8px 10px;border-radius:6px;border:1px solid #444;background:#2a2a2a;color:#eee;white-space:nowrap;flex:0 0 auto}
  button.on{background:#2d6cdf;border-color:#2d6cdf}
  #hud{position:absolute;top:6px;left:6px;font-size:12px;background:rgba(0,0,0,.5);padding:2px 6px;border-radius:6px}
</style>
</head>
<body>
<div id="stage"><img id="screen" alt="screen"></div>
<div id="hud">connecting...</div>
<div id="bar">
  <div class="row">
    <input id="txt" placeholder="type here" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false">
    <button id="send">Send</button>
    <button id="enter">Enter</button>
  </div>
  <div class="row">
    <span id="mons"></span>
    <button id="click">Click: off</button>
    <button id="zout">-</button>
    <button id="reset">Fit</button>
    <button id="zin">+</button>
  </div>
  <div class="row">
    <button data-key="esc">Esc</button>
    <button data-key="tab">Tab</button>
    <button data-key="backspace">Bksp</button>
    <button data-key="up">Up</button>
    <button data-key="down">Down</button>
    <button data-key="left">Left</button>
    <button data-key="right">Right</button>
    <button data-hot="ctrl,c">^C</button>
  </div>
</div>
<script>
const img = document.getElementById('screen');
const hud = document.getElementById('hud');
let W=0, H=0;                 // full monitor size
let z=1, cx=0, cy=0;          // zoom factor and view center (monitor coords)
let clickMode=false;
let ws, lastUrl=null, viewTimer=null, lastTap=0;

function region(){
  let rw=W/z, rh=H/z;
  let x=Math.max(0, Math.min(W-rw, cx-rw/2));
  let y=Math.max(0, Math.min(H-rh, cy-rh/2));
  return {x,y,w:rw,h:rh};
}
function sendView(){
  if(!ws || ws.readyState!==1) return;
  const r=region();
  ws.send(JSON.stringify({type:'view',x:r.x,y:r.y,w:r.w,h:r.h}));
}
function scheduleView(){
  if(viewTimer) return;
  viewTimer=setTimeout(()=>{viewTimer=null; sendView();},50);
}
function clientToMonitor(px,py){
  const b=img.getBoundingClientRect();
  const fx=(px-b.left)/b.width, fy=(py-b.top)/b.height;
  const r=region();
  return {mx:r.x+fx*r.w, my:r.y+fy*r.h, inside: fx>=0&&fx<=1&&fy>=0&&fy<=1};
}
function setZoom(nz, atX, atY){
  z=Math.max(1, Math.min(24, nz));
  if(atX!==undefined){ cx=atX; cy=atY; }
  scheduleView();
}

// ---- connection ----
function connect(){
  ws=new WebSocket('ws://'+location.host+'/ws');
  ws.binaryType='blob';
  ws.onmessage=(e)=>{
    if(typeof e.data==='string'){
      const m=JSON.parse(e.data);
      if(m.type==='info'){ W=m.width; H=m.height; cx=W/2; cy=H/2; z=1; renderMons(m); sendView(); }
      return;
    }
    const url=URL.createObjectURL(e.data);
    img.src=url;
    if(lastUrl) URL.revokeObjectURL(lastUrl);
    lastUrl=url;
    hud.textContent = Math.round(z*100)/100 + 'x' + (clickMode?' click':' view');
  };
  ws.onclose=()=>{ hud.textContent='disconnected, retrying...'; setTimeout(connect,1000); };
  ws.onerror=()=>ws.close();
}
connect();

// ---- pointer / touch on the stage ----
const stage=document.getElementById('stage');
let drag=null, pinch=null;
function dist(t0,t1){return Math.hypot(t0.clientX-t1.clientX, t0.clientY-t1.clientY);}

stage.addEventListener('touchstart',(e)=>{
  if(e.touches.length===2){
    const p=clientToMonitor((e.touches[0].clientX+e.touches[1].clientX)/2,(e.touches[0].clientY+e.touches[1].clientY)/2);
    pinch={d:dist(e.touches[0],e.touches[1]), z:z, mx:p.mx, my:p.my};
    drag=null;
  } else if(e.touches.length===1){
    drag={x:e.touches[0].clientX,y:e.touches[0].clientY,sx:e.touches[0].clientX,sy:e.touches[0].clientY,moved:false,t:Date.now()};
  }
  e.preventDefault();
},{passive:false});

stage.addEventListener('touchmove',(e)=>{
  if(pinch && e.touches.length===2){
    const nd=dist(e.touches[0],e.touches[1]);
    setZoom(pinch.z*(nd/pinch.d), pinch.mx, pinch.my);
  } else if(drag && e.touches.length===1){
    panBy(e.touches[0].clientX,e.touches[0].clientY);
  }
  e.preventDefault();
},{passive:false});

stage.addEventListener('touchend',(e)=>{
  if(pinch && e.touches.length<2) pinch=null;
  if(drag && e.touches.length===0){ endTap(drag); drag=null; }
  e.preventDefault();
},{passive:false});

// mouse (for testing from a desktop browser)
stage.addEventListener('mousedown',(e)=>{ drag={x:e.clientX,y:e.clientY,sx:e.clientX,sy:e.clientY,moved:false,t:Date.now()}; });
window.addEventListener('mousemove',(e)=>{ if(drag) panBy(e.clientX,e.clientY); });
window.addEventListener('mouseup',()=>{ if(drag){ endTap(drag); drag=null; } });
stage.addEventListener('wheel',(e)=>{ const p=clientToMonitor(e.clientX,e.clientY); setZoom(z*(e.deltaY<0?1.15:0.87),p.mx,p.my); e.preventDefault(); },{passive:false});

function panBy(px,py){
  const b=img.getBoundingClientRect();
  const r=region();
  const dx=(px-drag.x)/b.width*r.w, dy=(py-drag.y)/b.height*r.h;
  cx-=dx; cy-=dy;
  drag.x=px; drag.y=py;
  if(Math.hypot(px-drag.sx,py-drag.sy)>8) drag.moved=true;
  scheduleView();
}
function endTap(d){
  if(d.moved) return;
  const now=Date.now();
  if(now-lastTap<300){ const p=clientToMonitor(d.sx,d.sy); setZoom(z*2,p.mx,p.my); lastTap=0; return; }
  lastTap=now;
  if(clickMode){
    const p=clientToMonitor(d.sx,d.sy);
    if(p.inside && ws.readyState===1) ws.send(JSON.stringify({type:'click',mx:p.mx,my:p.my,button:'left'}));
  }
}

// ---- controls ----
const txt=document.getElementById('txt');
function send(o){ if(ws && ws.readyState===1) ws.send(JSON.stringify(o)); }
function renderMons(m){
  const box=document.getElementById('mons');
  box.innerHTML='';
  m.monitors.forEach(d=>{
    const b=document.createElement('button');
    b.textContent='M'+d.index;
    b.classList.toggle('on', d.index===m.current);
    b.onclick=()=>send({type:'monitor',index:d.index});
    box.appendChild(b);
  });
}
document.getElementById('send').onclick=()=>{ if(txt.value){ send({type:'text',value:txt.value}); txt.value=''; } };
document.getElementById('enter').onclick=()=>send({type:'key',key:'enter'});
document.getElementById('click').onclick=(e)=>{ clickMode=!clickMode; e.target.textContent='Click: '+(clickMode?'on':'off'); e.target.classList.toggle('on',clickMode); };
document.getElementById('zin').onclick=()=>setZoom(z*1.4);
document.getElementById('zout').onclick=()=>setZoom(z/1.4);
document.getElementById('reset').onclick=()=>{ z=1; cx=W/2; cy=H/2; scheduleView(); };
document.querySelectorAll('[data-key]').forEach(b=>b.onclick=()=>send({type:'key',key:b.dataset.key}));
document.querySelectorAll('[data-hot]').forEach(b=>b.onclick=()=>send({type:'hotkey',keys:b.dataset.hot.split(',')}));
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return HTMLResponse(HTML)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    sct = mss.mss()
    # sct.monitors[0] is the virtual "all monitors" box; 1..N are the real displays.
    displays = [{"index": i, "width": m["width"], "height": m["height"]}
                for i, m in enumerate(sct.monitors) if i >= 1]

    st = {"mon": sct.monitors[MONITOR], "x": 0.0, "y": 0.0,
          "w": float(sct.monitors[MONITOR]["width"]), "h": float(sct.monitors[MONITOR]["height"])}

    def send_info():
        return websocket.send_text(json.dumps({
            "type": "info", "width": st["mon"]["width"], "height": st["mon"]["height"],
            "monitors": displays,
            "current": next(i for i, m in enumerate(sct.monitors) if m is st["mon"]),
        }))

    await send_info()

    async def sender():
        while True:
            mon = st["mon"]
            region = {
                "left": mon["left"] + int(st["x"]),
                "top": mon["top"] + int(st["y"]),
                "width": max(1, int(st["w"])),
                "height": max(1, int(st["h"])),
            }
            shot = sct.grab(region)
            img = Image.frombytes("RGB", shot.size, shot.rgb)
            if img.width > MAX_W:
                img = img.resize((MAX_W, round(img.height * MAX_W / img.width)), Image.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=QUALITY)
            await websocket.send_bytes(buf.getvalue())
            await asyncio.sleep(1 / FPS)

    async def handle(d):
        t = d["type"]
        mon = st["mon"]
        if t == "view":
            st["x"], st["y"], st["w"], st["h"] = d["x"], d["y"], d["w"], d["h"]
        elif t == "monitor":
            new = sct.monitors[d["index"]]
            st["mon"], st["x"], st["y"], st["w"], st["h"] = new, 0.0, 0.0, float(new["width"]), float(new["height"])
            await send_info()
        elif t == "click":
            pyautogui.click(x=round(mon["left"] + d["mx"]), y=round(mon["top"] + d["my"]), button=d["button"])
        elif t == "text":
            pyautogui.write(d["value"], interval=0.01)
        elif t == "key":
            pyautogui.press(d["key"])
        elif t == "hotkey":
            pyautogui.hotkey(*d["keys"])
        else:
            raise ValueError(f"unknown message type: {t}")

    send_task = asyncio.create_task(sender())
    try:
        while True:
            await handle(json.loads(await websocket.receive_text()))
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        sct.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local desktop remote for phones.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--monitor", type=int, default=MONITOR, help="mss monitor index (1=primary)")
    parser.add_argument("--fps", type=int, default=FPS)
    args = parser.parse_args()
    MONITOR = args.monitor
    FPS = args.fps
    print(f"Serving on http://0.0.0.0:{args.port}  (open http://<this-pc-ip>:{args.port} on your phone)")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning", ws_ping_interval=None, ws_ping_timeout=None)
