import sqlite3, os, time, asyncio, socket, ipaddress, concurrent.futures, threading, base64
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from collections import deque

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sigrs.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS cameras (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, ip TEXT, brand TEXT, rtsp_url TEXT, supports_ptz INTEGER DEFAULT 0, status TEXT DEFAULT 'OFFLINE', created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, severity TEXT, title TEXT, description TEXT, camera_id INTEGER, snapshot TEXT, status TEXT DEFAULT 'PENDENTE', timestamp TEXT DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT, source_id INTEGER, event_type TEXT, metadata TEXT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit(); conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; return conn

recent_events = deque(maxlen=100)
class Alert:
    def __init__(self, s, t, d): self.severity, self.title, self.description = s, t, d

def process_event(event):
    current_time = time.time()
    while recent_events and recent_events[0]['timestamp'] < current_time - 10: recent_events.popleft()
    recent_events.append(event)
    et = event.get('event_type')
    if et == 'CAMERA_OFFLINE' and any(e['event_type'] == 'DOOR_FORCED_OPEN' for e in recent_events): return Alert("CRITICO", "Possivel Sabotagem", "Camera caiu e porta forcada simultaneamente.")
    if et == 'DOOR_FORCED_OPEN' and any(e['event_type'] == 'CAMERA_OFFLINE' for e in recent_events): return Alert("CRITICO", "Possivel Sabotagem", "Porta forcada e camera offline.")
    if et == 'INVASAO_PERIMETRO':
        hour = time.localtime(current_time).tm_hour
        if hour >= 22 or hour <= 5: return Alert("ALTA", "Intrusao Noturna", "IA detectou invasao fora do horario.")
    if et == 'HIGH_BANDWIDTH': return Alert("MEDIO", "Gargalo de Rede", "Uso de banda critico.")
    return None

try:
    import cv2
    def get_snapshot(rtsp_url):
        try:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp"
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            ret, frame = cap.read()
            cap.release()
            if ret:
                frame = cv2.resize(frame, (640, 480))
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                return f"data:image/jpeg;base64,{base64.b64encode(buffer).decode('utf-8')}"
        except: pass
        return None
    def get_snapshot_jpeg(rtsp_url):
        try:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp"
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            ret, frame = cap.read()
            cap.release()
            if ret:
                frame = cv2.resize(frame, (640, 480))
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                return buffer.tobytes()
        except: pass
        return None
except:
    def get_snapshot(u): return None
    def get_snapshot_jpeg(u): return None

def verificar_porta(ip, porta, timeout=0.5):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout); return s.connect_ex((str(ip), porta)) == 0
    except: return False

def escanear_ip(ip):
    portas_abertas = [p for p in [554, 80, 8080, 5000, 8000] if verificar_porta(ip, p)]
    if portas_abertas:
        tipo = "Desconhecido"
        if 554 in portas_abertas: tipo = "RTSP"
        elif 5000 in portas_abertas: tipo = "ONVIF"
        elif 80 in portas_abertas or 8080 in portas_abertas: tipo = "Web/DVR"
        return {"ip": str(ip), "portas": portas_abertas, "tipo": tipo}
    return None

def descobrir_dispositivos(subnet="192.168.1.0/24"):
    rede = ipaddress.ip_network(subnet, strict=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as ex:
        return [r for r in ex.map(escanear_ip, rede.hosts()) if r]

pending_alerts = []
class CameraMonitor:
    def __init__(self): self.running = False
    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
    def _loop(self):
        while self.running:
            try:
                db = get_db()
                for cam in db.execute("SELECT * FROM cameras").fetchall():
                    new_status = 'ONLINE' if any(verificar_porta(cam['ip'], p, 1) for p in [80, 554, 5000]) else 'OFFLINE'
                    if cam['status'] != new_status:
                        db.execute("UPDATE cameras SET status=? WHERE id=?", (new_status, cam['id']))
                        db.commit()
                        et = 'CAMERA_ONLINE' if new_status == 'ONLINE' else 'CAMERA_OFFLINE'
                        evt = {'source_type':'CAMERA','source_id':cam['id'],'event_type':et,'timestamp':time.time()}
                        alert = process_event(evt)
                        if alert:
                            snap = get_snapshot(cam['rtsp_url']) if alert.severity == 'CRITICO' else None
                            db.execute("INSERT INTO alerts (severity, title, description, camera_id, snapshot) VALUES (?,?,?,?,?)", (alert.severity, alert.title, alert.description, cam['id'], snap))
                            db.commit()
                            pending_alerts.append({'severity':alert.severity,'title':alert.title,'description':alert.description,'timestamp':time.time(),'snapshot':snap,'camera_id':cam['id']})
                db.close()
            except: pass
            time.sleep(30)
monitor = CameraMonitor()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
active_connections = []

class EventInput(BaseModel):
    source_type: str; source_id: int = 0; event_type: str; timestamp: float = time.time(); metadata: dict = {}
class CameraInput(BaseModel):
    name: str; ip: str; brand: str = "GENERICA"; rtsp_url: str = ""; supports_ptz: bool = False
class PTZInput(BaseModel):
    action: str; direction: str = ""

@app.on_event("startup")
def startup(): init_db(); monitor.start()

@app.websocket("/ws/alerts")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept(); active_connections.append(websocket)
    try:
        while True:
            while pending_alerts:
                a = pending_alerts.pop(0)
                for c in active_connections:
                    try: await c.send_json(a)
                    except: pass
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        if websocket in active_connections: active_connections.remove(websocket)

@app.get("/api/v1/dashboard/metrics")
async def metrics():
    db = get_db(); res = {'cameras_total':0,'cameras_online':0,'cameras_offline':0,'alerts_total':0,'alerts_pending':0,'events_total':0}
    res['cameras_total'] = db.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
    res['cameras_online'] = db.execute("SELECT COUNT(*) FROM cameras WHERE status='ONLINE'").fetchone()[0]
    res['cameras_offline'] = res['cameras_total'] - res['cameras_online']
    res['alerts_total'] = db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    res['alerts_pending'] = db.execute("SELECT COUNT(*) FROM alerts WHERE status='PENDENTE'").fetchone()[0]
    res['events_total'] = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    db.close(); return res

@app.get("/api/v1/cameras")
async def list_cams():
    db = get_db(); cams = db.execute("SELECT * FROM cameras ORDER BY id DESC").fetchall(); db.close()
    return [dict(c) for c in cams]

@app.post("/api/v1/cameras")
async def add_cam(cam: CameraInput):
    db = get_db()
    cur = db.execute("INSERT INTO cameras (name, ip, brand, rtsp_url, supports_ptz, status) VALUES (?,?,?,?,?, 'OFFLINE')", (cam.name, cam.ip, cam.brand, cam.rtsp_url, 1 if cam.supports_ptz else 0))
    db.commit(); db.close(); return {"status":"created", "camera_id": cur.lastrowid}

@app.delete("/api/v1/cameras/{cid}")
async def del_cam(cid: int):
    db = get_db(); db.execute("DELETE FROM cameras WHERE id=?", (cid,)); db.commit(); db.close(); return {"status":"deleted"}

@app.get("/api/v1/cameras/{cid}/snapshot")
async def cam_snap(cid: int):
    db = get_db(); cam = db.execute("SELECT * FROM cameras WHERE id=?", (cid,)).fetchone(); db.close()
    if not cam or not cam['rtsp_url']: return Response(status_code=404)
    img = get_snapshot_jpeg(cam['rtsp_url'])
    if img: return Response(content=img, media_type="image/jpeg")
    return Response(status_code=404)

@app.post("/api/v1/cameras/{cid}/ptz")
async def ptz(cid: int, ptz: PTZInput):
    return {"status":"command_sent", "action":ptz.action, "direction":ptz.direction}

@app.get("/api/v1/network/scan")
async def scan_net():
    loop = asyncio.get_event_loop()
    devs = await loop.run_in_executor(None, descobrir_dispositivos, "192.168.1.0/24")
    return {"status":"success", "dispositivos": devs}

@app.post("/api/v1/events/ingest")
async def ingest(evt: EventInput):
    ed = evt.model_dump()
    db = get_db()
    db.execute("INSERT INTO events (source_type, source_id, event_type, metadata) VALUES (?,?,?,?)", (evt.source_type, evt.source_id, evt.event_type, str(evt.metadata)))
    db.commit(); db.close()
    alert = process_event(ed)
    if alert:
        snap = None
        if alert.severity == "CRITICO" and evt.source_type == "CAMERA":
            db = get_db(); cam = db.execute("SELECT * FROM cameras WHERE id=?", (evt.source_id,)).fetchone(); db.close()
            if cam and cam['rtsp_url']: snap = get_snapshot(cam['rtsp_url'])
        a_data = {"severity":alert.severity,"title":alert.title,"description":alert.description,"timestamp":time.time(),"snapshot":snap,"camera_id":evt.source_id}
        db = get_db()
        db.execute("INSERT INTO alerts (severity, title, description, camera_id, snapshot) VALUES (?,?,?,?,?)", (alert.severity, alert.title, alert.description, evt.source_id, snap))
        db.commit(); db.close()
        for c in active_connections:
            try: await c.send_json(a_data)
            except: pass
    return {"status":"received", "alert_generated": alert is not None}

@app.get("/api/v1/alerts")
async def list_alerts():
    db = get_db(); al = db.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 50").fetchall(); db.close()
    return [dict(a) for a in al]
