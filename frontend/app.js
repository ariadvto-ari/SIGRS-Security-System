const API = 'http://localhost:8000/api/v1';
const WS_URL = 'ws://localhost:8000/ws/alerts';

const wsStatus = document.getElementById('ws-status');
const alertsContainer = document.getElementById('alerts-container');
const cameraGrid = document.getElementById('camera-grid');
const scanBtn = document.getElementById('scan-btn');
const scanResults = document.getElementById('scan-results');
const clockDiv = document.getElementById('clock');

let selectedCameraId = null;
let ws = null;

function setClock() {
    clockDiv.innerText = new Date().toLocaleTimeString('pt-BR');
}

setInterval(setClock, 1000);
setClock();

function connectWS() {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => {
        wsStatus.className = 'w-3 h-3 bg-green-500 rounded-full animate-pulse';
    };
    ws.onclose = () => {
        wsStatus.className = 'w-3 h-3 bg-red-500 rounded-full';
        setTimeout(connectWS, 3000);
    };
    ws.onmessage = (event) => {
        const alert = JSON.parse(event.data);
        addAlertToUI(alert);
        refreshMetrics();
    };
}

connectWS();

async function refreshMetrics() {
    try {
        const res = await fetch(`${API}/dashboard/metrics`);
        const m = await res.json();
        document.getElementById('m-total').innerText = m.cameras_total;
        document.getElementById('m-online').innerText = m.cameras_online;
        document.getElementById('m-offline').innerText = m.cameras_offline;
        document.getElementById('m-alerts').innerText = m.alerts_total;
        document.getElementById('m-pending').innerText = m.alerts_pending;
        document.getElementById('m-events').innerText = m.events_total;
    } catch (e) {
        console.error('Erro metricas:', e);
    }
}

async function loadCameras() {
    try {
        const res = await fetch(`${API}/cameras`);
        const cameras = await res.json();

        if (cameras.length === 0) {
            cameraGrid.innerHTML = '<p class="text-slate-600 text-sm col-span-full text-center py-8">Nenhuma camera cadastrada.</p>';
            return;
        }

        cameraGrid.innerHTML = cameras.map((cam) => {
            const statusColor = cam.status === 'ONLINE' ? 'bg-green-500' : 'bg-red-500';
            const statusText = cam.status === 'ONLINE' ? 'Online' : 'Offline';
            return `
                <div class="cam-card bg-slate-800 rounded-lg p-2 border border-slate-700 cursor-pointer" onclick="openCamModal(${cam.id}, ${JSON.stringify(cam.name || 'Camera')}, ${cam.supports_ptz ? 1 : 0})">
                    <div class="relative bg-black aspect-video rounded overflow-hidden mb-2">
                        <img src="${API}/cameras/${cam.id}/snapshot?t=${Date.now()}" alt="snapshot" class="w-full h-full object-cover" onerror="this.style.display='none'; this.parentElement.innerHTML='<p class=\'text-slate-600 text-xs text-center mt-8\'>Sem Sinal</p>'">
                        <span class="absolute top-1 right-1 w-2 h-2 ${statusColor} rounded-full"></span>
                    </div>
                    <p class="text-xs font-bold text-slate-300 truncate">${cam.name || 'Camera'}</p>
                    <p class="text-xs text-slate-500">${statusText} | ${cam.brand || 'GENERICA'}</p>
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error('Erro cameras:', e);
    }
}

setInterval(loadCameras, 10000);

function openAddModal() {
    document.getElementById('add-modal').classList.remove('hidden');
    document.getElementById('add-modal').classList.add('flex');
}

function closeAddModal() {
    document.getElementById('add-modal').classList.add('hidden');
    document.getElementById('add-modal').classList.remove('flex');
}

async function submitCamera() {
    const name = document.getElementById('add-name').value.trim();
    const ip = document.getElementById('add-ip').value.trim();
    const brand = document.getElementById('add-brand').value;
    const rtsp = document.getElementById('add-rtsp').value.trim();
    const ptz = document.getElementById('add-ptz').checked;

    if (!name || !ip) {
        alert('Nome e IP sao obrigatorios!');
        return;
    }

    let rtspUrl = rtsp;
    if (!rtspUrl) {
        if (brand === 'YOOSEE' || brand === 'VICOHOME') {
            rtspUrl = `rtsp://${ip}:554/h264_stream`;
        } else if (brand === 'V380_PRO' || brand === 'V720') {
            rtspUrl = `rtsp://${ip}:554/live/ch1`;
        } else {
            rtspUrl = `rtsp://${ip}:554/stream1`;
        }
    }

    try {
        await fetch(`${API}/cameras`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, ip, brand, rtsp_url: rtspUrl, supports_ptz: ptz })
        });

        closeAddModal();
        document.getElementById('add-name').value = '';
        document.getElementById('add-ip').value = '';
        document.getElementById('add-rtsp').value = '';
        document.getElementById('add-ptz').checked = false;
        loadCameras();
        refreshMetrics();
    } catch (e) {
        alert('Erro ao salvar camera.');
    }
}

function openCamModal(id, name, hasPtz) {
    selectedCameraId = id;
    document.getElementById('cam-modal-title').innerText = name;
    const img = document.getElementById('cam-modal-img');
    img.src = `${API}/cameras/${id}/snapshot?t=${Date.now()}`;
    if (hasPtz) {
        document.getElementById('ptz-controls').classList.remove('hidden');
    } else {
        document.getElementById('ptz-controls').classList.add('hidden');
    }
    document.getElementById('cam-modal').classList.remove('hidden');
    document.getElementById('cam-modal').classList.add('flex');
}

function closeCamModal() {
    document.getElementById('cam-modal').classList.add('hidden');
    document.getElementById('cam-modal').classList.remove('flex');
    selectedCameraId = null;
}

async function sendPTZ(direction) {
    if (!selectedCameraId) return;
    try {
        await fetch(`${API}/cameras/${selectedCameraId}/ptz`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'move', direction })
        });
    } catch (e) {
        console.error('Erro PTZ:', e);
    }
}

async function scanNetwork() {
    scanBtn.innerText = 'Escaneando...';
    scanBtn.disabled = true;
    scanResults.innerHTML = '<p class="text-slate-500">Buscando dispositivos...</p>';

    try {
        const res = await fetch(`${API}/network/scan`);
        const data = await res.json();
        scanResults.innerHTML = '';

        if (!data.dispositivos || data.dispositivos.length === 0) {
            scanResults.innerHTML = '<p class="text-red-400">Nenhum dispositivo encontrado.</p>';
            return;
        }

        data.dispositivos.forEach((dev) => {
            const div = document.createElement('div');
            div.className = 'p-2 bg-slate-800 rounded border-l-2 border-cyan-500 flex justify-between items-center gap-2';
            div.innerHTML = `
                <div>
                    <p class="text-cyan-400 font-bold text-xs">IP: ${dev.ip}</p>
                    <p class="text-slate-400 text-xs">Tipo: ${dev.tipo}</p>
                    <p class="text-slate-500 text-xs">Portas: ${dev.portas.join(', ')}</p>
                </div>
                <button onclick="quickAddCamera('${dev.ip}')" class="bg-green-700 hover:bg-green-600 text-white px-2 py-1 rounded text-xs">+ Add</button>
            `;
            scanResults.appendChild(div);
        });
    } catch (e) {
        scanResults.innerHTML = '<p class="text-red-400">Erro ao escanear.</p>';
    } finally {
        scanBtn.innerText = 'Escanear Rede 192.168.1.0/24';
        scanBtn.disabled = false;
    }
}

function quickAddCamera(ip) {
    document.getElementById('add-ip').value = ip;
    document.getElementById('add-name').value = `Camera ${ip}`;
    openAddModal();
}

async function sendEvent(sourceType, sourceId, eventType) {
    try {
        await fetch(`${API}/events/ingest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_type: sourceType, source_id: sourceId, event_type: eventType, metadata: {} })
        });
        refreshMetrics();
    } catch (e) {
        console.error('Erro evento:', e);
    }
}

function addAlertToUI(alert) {
    const firstPlaceholder = alertsContainer.children[0] && alertsContainer.children[0].classList.contains('text-slate-600');
    if (firstPlaceholder) {
        alertsContainer.innerHTML = '';
    }

    const severityColors = {
        CRITICO: 'bg-red-900/50 border-red-500 text-red-400 pulse-danger',
        ALTA: 'bg-orange-900/50 border-orange-500 text-orange-400',
        MEDIO: 'bg-blue-900/50 border-blue-500 text-blue-400'
    };

    const snapshotHtml = alert.snapshot ? `<img src="${alert.snapshot}" class="w-32 h-24 object-cover rounded border border-slate-700" alt="Evidence">` : '';
    const timeStr = new Date(alert.timestamp * 1000).toLocaleTimeString('pt-BR');
    const wrapper = document.createElement('div');
    wrapper.className = `p-3 border-l-4 rounded-lg flex gap-3 items-center ${severityColors[alert.severity] || 'bg-slate-800 border-slate-500 text-slate-300'}`;

    wrapper.innerHTML = `
        <div class="flex-1">
            <div class="flex justify-between items-start gap-2">
                <h3 class="font-bold text-sm">${alert.title}</h3>
                <div class="flex gap-2 items-center">
                    <span class="text-xs text-slate-500">${timeStr}</span>
                    <span class="text-xs font-mono bg-black/30 px-2 py-0.5 rounded">${alert.severity}</span>
                </div>
            </div>
            <p class="text-xs text-slate-300 mt-1">${alert.description}</p>
        </div>
        <div>${snapshotHtml}</div>
    `;

    alertsContainer.prepend(wrapper);
}

async function loadAlerts() {
    try {
        const res = await fetch(`${API}/alerts`);
        const alerts = await res.json();

        if (alerts.length === 0) {
            alertsContainer.innerHTML = '<p class="text-slate-600 text-sm text-center py-4">Aguardando anomalias...</p>';
            return;
        }

        alertsContainer.innerHTML = '';
        alerts.forEach((alert) => {
            addAlertToUI({
                severity: alert.severity,
                title: alert.title,
                description: alert.description,
                timestamp: new Date(alert.timestamp).getTime() / 1000,
                snapshot: alert.snapshot
            });
        });
    } catch (e) {
        console.error('Erro alerts:', e);
    }
}

loadCameras();
refreshMetrics();
loadAlerts();
setInterval(refreshMetrics, 15000);
