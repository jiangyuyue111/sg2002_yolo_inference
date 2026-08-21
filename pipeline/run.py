#!/usr/bin/env python3
"""run.py v7 — Zero-copy TPU output → NMS, sequential pipeline"""
import sys, os, time, signal, struct, ctypes, subprocess

MODEL="/akars_tennis/model/yolov8n_tennis_v2.cvimodel"
C_LIB="/lib/preprocess_ops.so"
CAMERA="/guest/linux/2.camera"
W,H=640,480; TW,TH=640,640; CONF=0.5; IOU=0.45

# ══ Camera ══
FMT="<IIIIIIQ"; FSZ=struct.calcsize(FMT); MAGIC=0xC0C0C0C0
class Cam:
    def __init__(self,path,w=640,h=480):
        self.w,self.h=w,h
        self._p=subprocess.Popen([path],stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=open(os.devnull,"r"))
        self._b=b"";self._s=False
    def _sync(self):
        while len(self._b)<FSZ+128:
            c=self._p.stdout.read(65536)
            if not c:raise EOFError
            self._b+=c
        for i in range(len(self._b)-4):
            if struct.unpack("<I",self._b[i:i+4])[0]==MAGIC:self._b=self._b[i:];self._s=True;return
        raise EOFError("no magic")
    def get(self):
        if not self._s:self._sync()
        while len(self._b)<FSZ:
            c=self._p.stdout.read(65536)
            if not c:raise EOFError
            self._b+=c
        if struct.unpack("<I",self._b[:4])[0]!=MAGIC:self._b=self._b[1:];self._s=False;return self.get()
        h=struct.unpack(FMT,self._b[:FSZ]);pl=h[4];tot=FSZ+pl
        while len(self._b)<tot:
            c=self._p.stdout.read(max(65536,tot-len(self._b)))
            if not c:raise EOFError
            self._b+=c
        f=self._b[FSZ:tot];self._b=self._b[tot:];return bytes(f),self.w,self.h
    def close(self):
        if self._p and self._p.poll() is None:self._p.send_signal(signal.SIGTERM)
        try:self._p.wait(timeout=3)
        except:self._p.kill()

# ══ Preprocessor ══
class PP:
    def __init__(self,tw=640,th=640,lib=C_LIB):
        self.tw,self.th=tw,th;self.osz=tw*th*3
        self._l=ctypes.CDLL(lib)
        self._l.yuyv_resize_planar.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_int,ctypes.c_int]
        self._l.yuyv_resize_planar.restype=ctypes.c_int
    def run_direct(self,yuv,sw,sh,target_ptr):
        src=ctypes.c_char_p(yuv)
        self._l.yuyv_resize_planar(src,sw,sh,target_ptr,self.tw,self.th)

# ══ TPU ══
_cvi=None
def _gcvi():
    global _cvi
    if not _cvi:_cvi=ctypes.CDLL("libcviruntime.so")
    return _cvi
def _ts(t):return[struct.unpack("<i",ctypes.string_at(t+8+i*4,4))[0] for i in range(6)]
def _td(t):return struct.unpack("<Q",ctypes.string_at(t+64,8))[0]
def _tc(t):return struct.unpack("<Q",ctypes.string_at(t+48,8))[0]
class TPU:
    def __init__(self,path):
        if isinstance(path,str):path=path.encode()
        self.m=ctypes.c_void_p(0);self.it=ctypes.c_void_p(0);self.in_n=ctypes.c_int32(0)
        self.ot=ctypes.c_void_p(0);self.on_n=ctypes.c_int32(0)
        rc=_gcvi().CVI_RT_Init()
        if rc:raise RuntimeError("init:%d"%rc)
        rc=_gcvi().CVI_NN_RegisterModel(path,ctypes.byref(self.m))
        if rc:raise RuntimeError("reg:%d"%rc)
        rc=_gcvi().CVI_NN_GetInputOutputTensors(self.m,ctypes.byref(self.it),ctypes.byref(self.in_n),ctypes.byref(self.ot),ctypes.byref(self.on_n))
        if rc:raise RuntimeError("tensors:%d"%rc)
        ip=self.it.value;self.ish=_ts(ip);self.idata=_td(ip);self.isz=int(_tc(ip))
        op=self.ot.value;self.osh=_ts(op);self.odata=_td(op);self.ocnt=int(_tc(op));self.ob=4*self.ocnt;self.on=self.osh[2];self.oc=self.osh[1]
        self.in_ptr=ctypes.c_void_p(self.idata)
        self.out_ptr=ctypes.c_void_p(self.odata)  # direct output pointer
    def forward(self):
        rc=_gcvi().CVI_NN_Forward(self.m,self.it,self.in_n,self.ot,self.on_n)
        if rc:raise RuntimeError("fwd:%d"%rc)
    def close(self):
        if self.m and self.m.value:_gcvi().CVI_NN_CleanupModel(self.m);self.m.value=0

# ══ Inference (zero-copy NMS from TPU output) ══
class Inf:
    def __init__(self,m=MODEL,cf=CONF,iou=IOU,lib=C_LIB):
        self.e=TPU(m);self.cf,self.iou=cf,iou;self.iw=self.e.ish[3];self.ih=self.e.ish[2]
        self._l=ctypes.CDLL(lib)
        self._l.nms_decode.argtypes=[ctypes.POINTER(ctypes.c_float),ctypes.c_int,ctypes.c_float,ctypes.c_float,ctypes.c_int,ctypes.POINTER(ctypes.c_float)]
        self._l.nms_decode.restype=ctypes.c_int
        self._da=ctypes.c_float*(20*5)
        self.tm=0.0;self.nm=0.0
    def infer_direct(self,pp,yuv,sw,sh):
        """Preprocess→TPU→NMS, all zero-copy."""
        t0=time.time()
        pp.run_direct(yuv,sw,sh,self.e.in_ptr)  # YUYV → TPU input buffer
        t1=time.time()
        self.e.forward()  # TPU Forward (no memcpy)
        t2=time.time()
        # NMS directly from TPU output buffer (zero-copy!)
        det=self._da()
        out_ptr=ctypes.cast(self.e.out_ptr,ctypes.POINTER(ctypes.c_float))
        n=self._l.nms_decode(out_ptr,self.e.on,ctypes.c_float(self.cf),ctypes.c_float(self.iou),20,det)
        ds=[]
        for i in range(n):
            o=i*5;ds.append((float(det[o+4]),float(det[o+0]),float(det[o+1]),float(det[o+2]),float(det[o+3])))
        t3=time.time()
        self.tm=(t2-t1)*1000;self.nm=(t3-t2)*1000
        return ds,(t1-t0)*1000  # dets, pre_ms
    def close(self):self.e.close()

# ══ MAIN ══
def main():
    print("="*55)
    print("  SG2002 Pipeline v7 (zero-copy TPU→NMS)")
    print("  {}x{} YUYV → CHW {}x{} → TPU+NMS".format(W,H,TW,TH))
    print("="*55)
    pp=PP(TW,TH);print("  Preprocessor: C v4 (fused, no malloc)")
    inf=Inf(MODEL,CONF,IOU);print("  TPU: {}x{}  output→NMS zero-copy".format(inf.iw,inf.ih))
    print("  Camera: {}  64KB reads".format(CAMERA))
    print("="*55+"\nRunning... Ctrl+C to stop.\n")
    cam=Cam(CAMERA,W,H)
    fid,t0=0,time.time()
    cw,pw,tw,nw=[],[],[],[]
    def sd(*a):
        cam.close();inf.close();e=time.time()-t0
        if fid:
            def av(w):return sum(w)/len(w) if w else 0
            print("\n"+"="*55)
            print("  {} frames {:.1f}s avg {:.1f}fps".format(fid,e,fid/e))
            print("  cam:{:.0f}ms pre:{:.0f}ms tpu:{:.0f}ms nms:{:.0f}ms".format(av(cw),av(pw),av(tw),av(nw)))
            print("="*55)
        print("Shutdown.");sys.exit(0)
    signal.signal(signal.SIGINT,sd);signal.signal(signal.SIGTERM,sd)
    try:
        while True:
            tf=time.time()
            yuv,sw,sh=cam.get();cm=(time.time()-tf)*1000
            dets,pm=inf.infer_direct(pp,yuv,sw,sh);tm,nm=inf.tm,inf.nm
            total=(time.time()-tf)*1000
            fid+=1
            for l,v in[(cw,cm),(pw,pm),(tw,tm),(nw,nm)]:l.append(v);(len(l)>30)and l.pop(0)
            if fid%5==0 or (dets and fid<=5):
                def av(w):return sum(w)/len(w) if w else 0
                ds="{:.2f} @ ({:.0f},{:.0f})".format(dets[0][0],dets[0][1],dets[0][2]) if dets else "none"
                print("  [{:04d}] det={}  cam:{:.0f}ms pre:{:.0f}ms tpu:{:.0f}ms nms:{:.0f}ms total:{:.0f}ms".format(fid,ds,av(cw),av(pw),av(tw),av(nw),total))
    except EOFError as e:print("Camera: {}".format(e))
    finally:sd()

if __name__=="__main__":main()
