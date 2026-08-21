"""
web_server.py — Built-in web remote control server.
Zero dependencies beyond Python 3 stdlib.
Start with: python robot.py web
"""

import os
import sys
import time
import json
import struct
import socket
import base64
import hashlib
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# ═══════════════════════════════════════════════════════════════════════
# Full-featured single-page web app
# ═══════════════════════════════════════════════════════════════════════

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>SG2002 Hunter</title>
<style>
:root{--bg:#020617;--card:#0f172a;--border:#1e293b;--text:#e2e8f0;--dim:#64748b;--blue:#3b82f6;--green:#22c55e;--red:#ef4444;--orange:#f97316;--purple:#8b5cf6}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif;height:100dvh;display:flex;flex-direction:column;overflow:hidden;touch-action:manipulation;user-select:none;-webkit-user-select:none}
.tabs{display:flex;background:var(--card);border-bottom:1px solid var(--border);flex-shrink:0}
.tabs button{flex:1;padding:10px 0;background:none;border:none;color:var(--dim);font-size:11px;font-weight:600;cursor:pointer;transition:all 0.2s;border-bottom:2px solid transparent}
.tabs button.active{color:var(--blue);border-bottom-color:var(--blue)}
.top{display:flex;justify-content:space-between;align-items:center;padding:6px 12px;background:var(--card);border-bottom:1px solid var(--border);flex-shrink:0}
.top .title{font-size:13px;font-weight:700;letter-spacing:0.5px}
.top .info{font-size:10px;color:var(--dim);font-family:monospace}
.top .dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:4px}
.top .dot.on{background:var(--green);box-shadow:0 0 6px var(--green)}
.top .dot.off{background:var(--red)}
.page{flex:1;overflow-y:auto;overflow-x:hidden;display:none;flex-direction:column}
.page.active{display:flex}
.video-wrap{position:relative;background:#000;flex-shrink:0}
.video-wrap img,.video-wrap canvas{width:100%;height:100%;object-fit:contain;display:block}
.video-wrap .state-tag{position:absolute;top:6px;left:6px;padding:2px 8px;border-radius:8px;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px}
.video-wrap .fps-tag{position:absolute;top:6px;right:6px;font-size:9px;color:var(--dim);background:#00000088;padding:1px 6px;border-radius:6px}
.panel{padding:8px 12px;display:flex;flex-direction:column;gap:8px;align-items:center}
.dpad{display:grid;grid-template-columns:1fr 1fr 1fr;grid-template-rows:1fr 1fr 1fr;gap:4px;aspect-ratio:1;width:min(72vw,280px)}
.dpad button{width:100%;height:100%;border-radius:14px;border:1px solid var(--border);font-size:18px;font-weight:700;cursor:pointer;background:var(--card);color:var(--text);touch-action:none;transition:all 0.1s}
.dpad button:active{transform:scale(0.93)}
.dpad .up{grid-column:2;grid-row:1;background:#1e3a5f;color:#93c5fd}
.dpad .left{grid-column:1;grid-row:2;background:#1e3a5f;color:#93c5fd}
.dpad .stop{grid-column:2;grid-row:2;background:#3b1f1f;color:#fca5a5;font-size:11px}
.dpad .right{grid-column:3;grid-row:2;background:#1e3a5f;color:#93c5fd}
.dpad .down{grid-column:2;grid-row:3;background:#1e3a5f;color:#93c5fd}
.btn-row{display:flex;gap:6px;width:min(72vw,280px)}
.btn-row button{flex:1;padding:10px;border-radius:10px;border:none;font-size:12px;font-weight:700;cursor:pointer;color:#fff}
.btn-row .grab{background:var(--green)}
.btn-row .release{background:var(--blue)}
.btn-row .gravity{background:var(--orange);font-size:10px}
.speed-row{display:flex;align-items:center;gap:8px;width:min(72vw,280px)}
.speed-row label{font-size:9px;color:var(--dim);text-transform:uppercase}
.speed-row input{flex:1;accent-color:var(--blue)}
.speed-row .val{font-size:11px;font-weight:700;color:var(--blue);min-width:20px;text-align:right}
.mtr{display:flex;gap:6px;width:min(72vw,280px);font-size:10px}
.mtr>div{flex:1;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:6px 8px}
.mtr .label{color:var(--dim);font-size:8px;text-transform:uppercase}
.mtr .value{font-size:14px;font-weight:700;margin-top:2px}
.mtr .bar{height:3px;border-radius:2px;margin-top:4px;background:var(--border);overflow:hidden}
.mtr .fill{height:100%;border-radius:2px;transition:width 0.2s}
.mtr .fill.pos{background:var(--green)}
.mtr .fill.neg{background:var(--red)}
.rc-page{background:#000;flex:1;position:relative;overflow:hidden}
.rc-video{position:absolute;inset:0}
.rc-video img,.rc-video canvas{width:100%;height:100%;object-fit:cover}
.rc-hud{position:absolute;top:0;left:0;right:0;z-index:2;padding:8px 12px;display:flex;justify-content:space-between;align-items:flex-start;pointer-events:none}
.rc-hud>*{pointer-events:auto}
.rc-spd{display:flex;gap:10px;font-size:9px;color:#fff;text-shadow:0 1px 3px #000}
.rc-spd b{color:#4ade80}
.joy-ctr{position:absolute;z-index:3;pointer-events:none}
.joy-ctr>*{pointer-events:auto}
.joy-ctr.left{left:5%;bottom:10%}
.joy-ctr.right{right:5%;bottom:10%}
.joy{width:min(22vw,100px);height:min(22vw,100px);border-radius:50%;border:2px solid rgba(255,255,255,0.2);position:relative;touch-action:none;backdrop-filter:blur(4px)}
.joy .knob{position:absolute;width:40%;height:40%;border-radius:50%;background:rgba(255,255,255,0.5);top:50%;left:50%;transform:translate(-50%,-50%);transition:background 0.15s}
.joy.active .knob{background:var(--blue);box-shadow:0 0 16px var(--blue)}
.joy-lbl{text-align:center;font-size:9px;color:rgba(255,255,255,0.6);margin-top:4px}
.rc-btn{position:absolute;bottom:8%;left:50%;transform:translateX(-50%);z-index:3;display:flex;gap:8px}
.rc-btn button{padding:10px 16px;border-radius:10px;border:none;font-size:12px;font-weight:700;color:#fff;cursor:pointer}
.rc-btn .grab{background:var(--green)}
.rc-btn .release{background:var(--blue)}
.stat-page{padding:12px;overflow-y:auto}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px;margin-bottom:8px}
.stat-card h3{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}
.stat-r{display:flex;justify-content:space-between;font-size:12px;padding:3px 0}
.stat-r .k{color:var(--dim)}
.stat-r .v{font-weight:600;font-family:monospace}
.grav-overlay{display:none;position:fixed;inset:0;background:var(--bg);z-index:20;flex-direction:column;align-items:center;justify-content:center}
.grav-overlay.active{display:flex}
.grav-overlay .ring{width:140px;height:140px;border:2px solid var(--blue);border-radius:50%;position:relative;margin-bottom:16px}
.grav-overlay .ring:before,.grav-overlay .ring:after{content:'';position:absolute;background:rgba(59,130,246,0.3)}
.grav-overlay .ring:before{width:100%;height:1px;top:50%}
.grav-overlay .ring:after{width:1px;height:100%;left:50%}
.grav-overlay .dot{position:absolute;width:30px;height:30px;border-radius:50%;background:var(--blue);top:50%;left:50%;transform:translate(-50%,-50%)}
.grav-overlay .cmd{font-size:20px;font-weight:900;color:var(--blue);margin-bottom:20px}
.grav-overlay .back-btn{padding:10px 30px;border:1px solid var(--border);border-radius:20px;color:var(--text);background:var(--card);font-size:13px;cursor:pointer}
.cam-full{display:none;position:fixed;inset:0;background:#000;z-index:15;flex-direction:column}
.cam-full.active{display:flex}
.cam-full img,.cam-full canvas{flex:1;object-fit:contain}
.cam-full .close-btn{position:absolute;top:10px;right:10px;z-index:2;padding:6px 14px;border-radius:14px;border:1px solid rgba(255,255,255,0.2);background:rgba(0,0,0,0.5);color:#fff;font-size:11px;cursor:pointer}
</style>
</head>
<body>
<div class="tabs">
  <button class="active" data-tab="remote">[遥控器]</button>
  <button data-tab="rc">[RC摇杆]</button>
  <button data-tab="status">[状态]</button>
</div>

<!-- TAB 1: Remote control -->
<div class="page active" id="page-remote">
  <div class="video-wrap" ondblclick="$('cam-full-1').classList.add('active')">
    <img id="cam1" src="/stream" alt="camera">
    <div class="state-tag" id="tag1">CHASE</div>
    <div class="fps-tag" id="fps1">-- fps</div>
  </div>
  <div class="panel">
    <div class="dpad" id="dpad">
      <button class="up" data-dir="up"></button>
      <button class="left" data-dir="left"></button>
      <button class="stop" data-dir="stop">STOP</button>
      <button class="right" data-dir="right"></button>
      <button class="down" data-dir="down"></button>
    </div>
    <div class="btn-row">
      <button class="grab" id="btn-grab">GRAB</button>
      <button class="release" id="btn-release">RELEASE</button>
    </div>
    <div class="btn-row">
      <button class="gravity" id="btn-gravity">TILT</button>
    </div>
    <div class="speed-row">
      <label>SPD</label>
      <input type="range" id="speed-slider" min="30" max="70" value="50">
      <span class="val" id="speed-val">50</span>
    </div>
    <div class="mtr">
      <div><div class="label">L</div><div class="value" id="mtr-l">0.00</div><div class="bar"><div class="fill pos" id="bar-l" style="width:0%"></div></div></div>
      <div><div class="label">R</div><div class="value" id="mtr-r">0.00</div><div class="bar"><div class="fill pos" id="bar-r" style="width:0%"></div></div></div>
    </div>
  </div>
</div>

<!-- TAB 2: RC joysticks -->
<div class="page" id="page-rc">
  <div class="rc-page">
    <div class="rc-video" ondblclick="$('cam-full-2').classList.add('active')">
      <img id="cam2" src="/stream" alt="camera">
    </div>
    <div class="rc-hud">
      <span class="state-tag" id="tag2" style="position:static">CHASE</span>
      <div class="rc-spd">
        <span>L <b id="rc-l">+0.00</b></span>
        <span>R <b id="rc-r">+0.00</b></span>
        <span id="rc-fps" style="color:var(--dim)">--</span>
      </div>
    </div>
    <div class="joy-ctr left">
      <div class="joy" id="joy-thr"><div class="knob"></div></div>
      <div class="joy-lbl">THROTTLE</div>
    </div>
    <div class="joy-ctr right">
      <div class="joy" id="joy-str"><div class="knob"></div></div>
      <div class="joy-lbl">STEER</div>
    </div>
    <div class="rc-btn">
      <button class="grab">GRAB</button>
      <button class="release">RELEASE</button>
    </div>
  </div>
</div>

<!-- TAB 3: Status -->
<div class="page" id="page-status">
  <div class="stat-page">
    <div class="stat-card"><h3>STATE MACHINE</h3>
      <div class="stat-r"><span class="k">state</span><span class="v" id="st-state" style="color:var(--orange)">chase_tennis</span></div>
      <div class="stat-r"><span class="k">grab confirm</span><span class="v" id="st-grab">0/10</span></div>
      <div class="stat-r"><span class="k">elapsed</span><span class="v" id="st-time">0.0s</span></div>
      <div class="stat-r"><span class="k">frames</span><span class="v" id="st-frame">0</span></div>
    </div>
    <div class="stat-card"><h3>MOTORS</h3>
      <div class="stat-r"><span class="k">L PWM</span><span class="v" id="st-lpwm">0</span></div>
      <div class="stat-r"><span class="k">R PWM</span><span class="v" id="st-rpwm">0</span></div>
      <div class="stat-r"><span class="k">L RPM</span><span class="v" id="st-lrpm">--</span></div>
      <div class="stat-r"><span class="k">R RPM</span><span class="v" id="st-rrpm">--</span></div>
    </div>
    <div class="stat-card"><h3>CONNECTION</h3>
      <div class="stat-r"><span class="k">WebSocket</span><span class="v" id="st-ws">connecting...</span></div>
      <div class="stat-r"><span class="k">IP</span><span class="v" id="st-ip">--</span></div>
      <div class="stat-r"><span class="k">FPS</span><span class="v" id="st-fps">--</span></div>
    </div>
  </div>
</div>

<!-- Gravity overlay -->
<div class="grav-overlay" id="g-overlay">
  <div class="ring"><div class="dot" id="g-dot"></div></div>
  <div class="cmd" id="g-cmd">STANDBY</div>
  <button class="back-btn" id="g-back">BACK</button>
</div>

<!-- Fullscreen camera overlays -->
<div class="cam-full" id="cam-full-1"><img src="/stream" alt=""><button class="close-btn" onclick="$('cam-full-1').classList.remove('active')">CLOSE</button></div>
<div class="cam-full" id="cam-full-2"><img src="/stream" alt=""><button class="close-btn" onclick="$('cam-full-2').classList.remove('active')">CLOSE</button></div>

<script>
var speed=50, ws, online=false, jThr=0, jStr=0;
function $(id){return document.getElementById(id)}
function send(dir,active){
  var s=(dir==='left'||dir==='right')?25:speed;
  fetch('/api/control?action='+dir+'&speed='+s+'&active='+(active?1:0)).catch(function(){});
}
function sendArm(a){fetch('/api/arm?action='+a).catch(function(){})}
function motorCmd(thr,str){
  var l=Math.round(Math.max(-100,Math.min(100,thr+str)));
  var r=Math.round(Math.max(-100,Math.min(100,thr-str)));
  fetch('/api/motor?left='+l+'&right='+r).catch(function(){});
}

// Tabs
document.querySelectorAll('.tabs button').forEach(function(b){
  b.addEventListener('click',function(){
    document.querySelectorAll('.tabs button').forEach(function(x){x.classList.remove('active')});
    document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active')});
    b.classList.add('active');$('page-'+b.dataset.tab).classList.add('active');
  });
});

// D-pad
$('dpad').addEventListener('pointerdown',function(e){
  var d=e.target.dataset.dir;if(!d)return;
  e.target.setPointerCapture(e.pointerId);send(d,1);
});
$('dpad').addEventListener('pointerup',function(e){
  var d=e.target.dataset.dir;if(!d)return;send(d,0);
});

// Buttons
$('btn-grab').addEventListener('pointerdown',function(){sendArm('grab')});
$('btn-release').addEventListener('pointerdown',function(){sendArm('release')});
document.querySelectorAll('.rc-btn button').forEach(function(b){
  b.addEventListener('pointerdown',function(){
    if(b.classList.contains('grab'))sendArm('grab');else sendArm('release');
  });
});

// Speed
$('speed-slider').addEventListener('input',function(){speed=+this.value;$('speed-val').textContent=speed});

// Gravity
$('btn-gravity').addEventListener('click',function(){
  $('g-overlay').classList.add('active');
  if(typeof DeviceOrientationEvent!=='undefined'&&typeof DeviceOrientationEvent.requestPermission==='function')
    DeviceOrientationEvent.requestPermission();
});
$('g-back').addEventListener('click',function(){$('g-overlay').classList.remove('active');send('stop',0)});
var cmdL={up:'FWD',down:'BACK',left:'LEFT',right:'RIGHT',stop:'STOP'},lastG='stop';
window.addEventListener('deviceorientation',function(e){
  if(!$('g-overlay').classList.contains('active'))return;
  var b=e.beta||0,g=e.gamma||0;
  $('g-dot').style.transform='translate(calc(-50% + '+(g*2)+'px), calc(-50% + '+(-b*2)+'px))';
  var n='stop';
  if(b>18)n='down';else if(b<-18)n='up';
  else if(g>18)n='right';else if(g<-18)n='left';
  if(n!==lastG){send(lastG,0);send(n,1);lastG=n}
  $('g-cmd').textContent=cmdL[n]||'STANDBY';
});

// Joysticks
function setupJoy(el, cb, endCb){
  el.addEventListener('pointerdown',function(e){
    e.preventDefault();el.setPointerCapture(e.pointerId);el.classList.add('active');
    handleJoy(el,e,cb);
  });
  el.addEventListener('pointermove',function(e){if(el.classList.contains('active'))handleJoy(el,e,cb)});
  el.addEventListener('pointerup',function(){el.classList.remove('active');endCb()});
  el.addEventListener('pointerleave',function(){el.classList.remove('active');endCb()});
}
function handleJoy(el,e,cb){
  var r=el.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2;
  var dx=e.clientX-cx,dy=e.clientY-cy,maxD=r.width/2-12,dist=Math.sqrt(dx*dx+dy*dy);
  var sc=dist>0?Math.min(dist,maxD)/dist:0,x=dx*sc,y=dy*sc;
  el.querySelector('.knob').style.transform='translate(calc(-50% + '+x+'px), calc(-50% + '+y+'px))';
  cb(x/maxD,-y/maxD);
}
setupJoy($('joy-thr'),function(x,y){jThr=y*100},function(){jThr=0;motorCmd(0,0)});
setupJoy($('joy-str'),function(x,y){jStr=x*100},function(){jStr=0;motorCmd(0,0)});
setInterval(function(){if(jThr!==0||jStr!==0)motorCmd(jThr,jStr)},33);

// WebSocket
var wsTimer=null;
function cWS(){
  var p=location.protocol==='https:'?'wss:':'ws:';
  try{
    ws=new WebSocket(p+'//'+location.host+'/ws');ws.binaryType='arraybuffer';
    ws.onopen=function(){
      online=true;$('st-ws').textContent='ONLINE';$('st-ws').style.color='var(--green)';
      if(wsTimer){clearInterval(wsTimer);wsTimer=null}
    };
    ws.onclose=function(){
      online=false;$('st-ws').textContent='OFFLINE';$('st-ws').style.color='var(--red)';
      if(!wsTimer)wsTimer=setInterval(cWS,2000);
    };
    ws.onmessage=function(e){
      if(e.data instanceof ArrayBuffer&&e.data.byteLength>=5){
        var dv=new DataView(e.data);
        if(dv.getUint8(0)===0xBB){
          var l=(dv.getInt16(1,true)/1000).toFixed(2),r=(dv.getInt16(3,true)/1000).toFixed(2);
          $('mtr-l').textContent=l;$('mtr-r').textContent=r;
          $('rc-l').textContent=l;$('rc-r').textContent=r;
          $('bar-l').style.width=Math.abs(parseFloat(l)*100)+'%';
          $('bar-r').style.width=Math.abs(parseFloat(r)*100)+'%';
          $('bar-l').className='fill '+(parseFloat(l)>=0?'pos':'neg');
          $('bar-r').className='fill '+(parseFloat(r)>=0?'pos':'neg');
          $('st-lpwm').textContent=l;$('st-rpwm').textContent=r;
        }
      }else if(typeof e.data==='string'){
        try{var m=JSON.parse(e.data);
          if(m.type==='state'){
            var tags=[$('tag1'),$('tag2')];
            tags.forEach(function(t){t.textContent=m.state;t.style.background=(m.color||'#f97316')+'44';t.style.color=m.color||'#f97316'});
            $('st-state').textContent=m.state;$('st-state').style.color=m.color||'#f97316';
          }
          if(m.type==='ip')$('st-ip').textContent=m.ip;
          if(m.type==='fps'){$('fps1').textContent=m.fps+' fps';$('rc-fps').textContent=m.fps;$('st-fps').textContent=m.fps}
          if(m.type==='status'){$('st-grab').textContent=m.grab+'/'+m.grab_needed;$('st-time').textContent=m.elapsed+'s';$('st-frame').textContent=m.frame}
        }catch(ex){}
      }
    };
    ws.onerror=function(){ws.close()};
  }catch(ex){if(!wsTimer)wsTimer=setInterval(cWS,2000)}
}
cWS();
fetch('/api/ip').then(function(r){return r.json()}).then(function(d){$('st-ip').textContent=d.ip}).catch(function(){});
</script>
</body>
</html>"""

# ═══════════════════════════════════════════════════════════════════════
# Mock camera
# ═══════════════════════════════════════════════════════════════════════

class MockCamera:
    def __init__(self):
        self._n = 0
    def get_jpeg(self):
        self._n += 1
        return self._solid_jpeg(320, 240, (15, 23, 42))
    def _solid_jpeg(self, w, h, rgb):
        # Minimal valid JPEG (grayscale-ish, placeholder)
        return base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
            "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
            "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
            "MjIyMjIyMjIyMjIyMjL/wAARCACgATADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEA"
            "AAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIT"
            "MUEGUGEiMnGBBxRCkaEjUrHBCBUz0fDxCRYkQ2JykkThFxgZGiYzR0RVZnaGlKS0"
            "1RVU3Rlcnb/2gAMAwEAAhEDEQA/AO2/wCEa0P/AKBVj/34Wj/hGtD/AOgVY/8Afh"
            "aKKACj/hGtD/AOgVY/8AfhafB4c0aOdHTTbVXU5BEQyDRRQBraiiigAooooAKKKKA"
            "CiiigAooooAKKKKACiiigAooooAKKKKAP/9k="
        )

# ═══════════════════════════════════════════════════════════════════════
# HTTP handler + WebSocket
# ═══════════════════════════════════════════════════════════════════════

class RobotHandler(BaseHTTPRequestHandler):
    motor = None
    servo = None
    fsm = None
    camera = None
    state = {"car_left": 0, "car_right": 0, "state": "chase_tennis", "fps": "0"}
    ws_clients = []

    def log_message(self, f, *a): pass

    def do_GET(self):
        p = urlparse(self.path).path
        if p == '/' or p == '/index.html': self._html()
        elif p == '/stream': self._mjpeg()
        elif p == '/ws': self._ws_upgrade()
        elif p.startswith('/api/'): self._api()
        else: self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path.startswith('/api/'): self._api()
        else: self.send_error(404)

    def _html(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(INDEX_HTML.encode())

    def _mjpeg(self):
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            while not self.wfile.closed:
                jpg = self.__class__.camera.get_jpeg() if self.__class__.camera else b''
                f = (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                     str(len(jpg)).encode() + b'\r\n\r\n' + jpg + b'\r\n')
                self.wfile.write(f); self.wfile.flush()
                time.sleep(0.066)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _ws_upgrade(self):
        key = self.headers.get('Sec-WebSocket-Key', '')
        if not key: self.send_error(400); return
        accept = base64.b64encode(hashlib.sha1(
            key.encode() + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest()).decode()
        self.send_response(101)
        self.send_header('Upgrade', 'websocket')
        self.send_header('Connection', 'Upgrade')
        self.send_header('Sec-WebSocket-Accept', accept)
        self.end_headers()
        self.__class__.ws_clients.append(self)
        try:
            while True:
                st = self.__class__.state
                buf = struct.pack('<Bhh', 0xBB, int(st['car_left']*1000), int(st['car_right']*1000))
                self._ws_send(buf, 0x02)
                js = json.dumps({'type':'state','state':st['state'],'color':_state_color(st['state'])})
                self._ws_send(js.encode(), 0x01)
                js2 = json.dumps({'type':'fps','fps':st.get('fps','0')})
                self._ws_send(js2.encode(), 0x01)
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if self in self.__class__.ws_clients: self.__class__.ws_clients.remove(self)

    def _ws_send(self, data, opcode=0x02):
        if isinstance(data, str): data = data.encode()
        f = bytearray([0x80|opcode])
        n = len(data)
        if n < 126: f.append(n)
        elif n < 65536: f.extend([126]); f.extend(struct.pack('>H', n))
        else: f.extend([127]); f.extend(struct.pack('>Q', n))
        f.extend(data)
        self.wfile.write(bytes(f)); self.wfile.flush()

    def _api(self):
        p = urlparse(self.path).path
        q = parse_qs(urlparse(self.path).query)
        if p == '/api/ip': self._json({'ip': self._my_ip()})
        elif p == '/api/control': self._api_ctrl(q)
        elif p == '/api/arm': self._api_arm(q)
        elif p == '/api/motor': self._api_motor(q)
        elif p == '/api/status': self._json(self.__class__.state)
        else: self._json({'error':'unknown'})

    def _api_ctrl(self, q):
        act = q.get('action',['stop'])[0]; spd = int(q.get('speed',['50'])[0])
        ac = int(q.get('active',['0'])[0])
        m = {'up':(spd,spd),'down':(-spd,-spd),'left':(-spd,spd),'right':(spd,-spd),'stop':(0,0)}
        if ac and act in m:
            l,r = m[act]
            self.__class__.state['car_left']=l; self.__class__.state['car_right']=r
            if self.__class__.motor: self.__class__.motor.set_speeds(l,r)
        elif not ac:
            self.__class__.state['car_left']=0; self.__class__.state['car_right']=0
            if self.__class__.motor: self.__class__.motor.set_speeds(0,0)
        self._json({'status':'ok'})

    def _api_arm(self, q):
        act = q.get('action',['grab'])[0]
        s = self.__class__.servo
        if s:
            if act == 'grab': s.grab()
            elif act == 'release': s.release()
        self._json({'status':'ok'})

    def _api_motor(self, q):
        l = int(q.get('left',['0'])[0]); r = int(q.get('right',['0'])[0])
        self.__class__.state['car_left']=l; self.__class__.state['car_right']=r
        if self.__class__.motor: self.__class__.motor.set_speeds(l,r)
        self._json({'status':'ok'})

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)

    @staticmethod
    def _my_ip():
        try:
            s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80))
            ip=s.getsockname()[0]; s.close(); return ip
        except: return '127.0.0.1'

def _state_color(s):
    return {'chase_tennis':'#f97316','position_tennis':'#3b82f6','grab_tennis':'#22c55e',
            'chase_bucket':'#8b5cf6','release_tennis':'#ef4444'}.get(s,'#64748b')

# ═══════════════════════════════════════════════════════════════════════
# Server
# ═══════════════════════════════════════════════════════════════════════

class WebServer:
    def __init__(self, port=8080, mock=True):
        self.port = port; self.mock = mock

    def start(self, motor=None, servo=None, fsm=None):
        RobotHandler.motor = motor; RobotHandler.servo = servo; RobotHandler.fsm = fsm
        if self.mock: RobotHandler.camera = MockCamera()
        self._srv = HTTPServer(('0.0.0.0', self.port), RobotHandler)
        self._th = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._th.start()
        ip = self._my_ip()
        print(f"\n  Web UI: http://{ip}:{self.port}")
        print(f"  Phone: same WiFi, open browser -> http://{ip}:{self.port}")
        print(f"  Tabs: [遥控器] [RC摇杆] [状态]")
        print(f"  Double-click video -> fullscreen")
        print(f"  Ctrl+C to stop\n")

    def stop(self):
        if hasattr(self,'_srv'): self._srv.shutdown()
    def update_state(self,**kw):
        RobotHandler.state.update(kw)
    @staticmethod
    def _my_ip():
        try:
            s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80))
            ip=s.getsockname()[0]; s.close(); return ip
        except: return '127.0.0.1'

def main():
    import signal
    srv = WebServer(port=int(os.environ.get('PORT','8080')), mock=True)
    srv.start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        srv.stop(); print("\n  Done.")

if __name__ == '__main__':
    main()
