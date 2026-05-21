/**
 * Kanvis Edge — panel de configuración (Fase 4)
 */

const TOKEN_KEY = "kanvis_token";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function getToken() {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}

function setToken(t) {
  if (t) sessionStorage.setItem(TOKEN_KEY, t);
  else sessionStorage.removeItem(TOKEN_KEY);
}

function toast(msg, isErr = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", isErr);
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 4000);
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.json) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) {
    logout();
    throw new Error("Sesión expirada");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch (_) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.blob();
}

function showApp(show) {
  $("#login-screen").classList.toggle("hidden", show);
  $("#app").classList.toggle("hidden", !show);
}

async function checkSession() {
  if (!getToken()) return false;
  try {
    await api("/api/v1/webui/session");
    return true;
  } catch {
    setToken("");
    return false;
  }
}

function logout() {
  if (window.KanvisWebRtcViewer?.isConnected?.()) {
    window.KanvisWebRtcViewer.disconnect(api).catch(() => {});
  }
  setToken("");
  showApp(false);
}

$("#login-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const user = $("#login-user").value.trim();
  const pass = $("#login-pass").value;
  try {
    const data = await fetch("/api/v1/webui/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: user, password: pass }),
    }).then(async (r) => {
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.detail || "Login fallido");
      }
      return r.json();
    });
    setToken(data.access_token);
    showApp(true);
    await initApp();
    toast("Sesión iniciada");
  } catch (err) {
    toast(err.message, true);
  }
});

$("#btn-logout")?.addEventListener("click", logout);

$$(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $$(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${tab}`));
    if (tab === "cameras") loadCameras();
    if (tab === "system") loadSystem();
  });
});

let camerasCache = [];

async function loadCameras() {
  const list = $("#camera-list");
  list.innerHTML = "<p>Cargando…</p>";
  try {
    camerasCache = await api("/api/v1/cameras");
    if (!camerasCache.length) {
      list.innerHTML = "<p>No hay cámaras. Añade una abajo.</p>";
      return;
    }
    list.innerHTML = "";
    for (const cam of camerasCache) {
      const card = document.createElement("div");
      card.className = "camera-card";
      const id = cam.camera_id;
      let statusHtml = "";
      try {
        const st = await api(`/api/v1/cameras/${id}/status`);
        const ing = st.ingest || {};
        const conn = ing.connected
          ? '<span class="badge ok">Conectada</span>'
          : '<span class="badge warn">Sin vídeo</span>';
        statusHtml = `${conn} Búfer: ${st.buffer_span_seconds || 0}s / ${st.buffer_max_duration_seconds || 60}s`;
        if (st.relay?.running) statusHtml += ' <span class="badge ok">Relay ON</span>';
        if (st.webrtc?.session?.connection_state === "connected") {
          statusHtml += ' <span class="badge ok">WebRTC</span>';
        }
      } catch {
        statusHtml = '<span class="badge err">Inactiva</span>';
      }
      card.innerHTML = `
        <h3>${cam.label || id}</h3>
        <div>${statusHtml}</div>
        <div style="color:var(--muted);font-size:0.85rem">${cam.source?.host || cam.ip_address}:${cam.source?.port || 554}</div>
        <div class="actions">
          <button type="button" data-test="${id}">Probar</button>
          <button type="button" class="secondary" data-edit="${id}">Editar</button>
          <button type="button" class="danger" data-del="${id}">Eliminar</button>
        </div>
      `;
      list.appendChild(card);
    }
    list.querySelectorAll("[data-test]").forEach((b) =>
      b.addEventListener("click", () => openTestPanel(b.dataset.test))
    );
    list.querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => fillForm(camerasCache.find((c) => c.camera_id === b.dataset.edit)))
    );
    list.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => deleteCamera(b.dataset.del))
    );
  } catch (err) {
    list.innerHTML = `<p class="err">${err.message}</p>`;
  }
}

function fillForm(cam) {
  if (!cam) return clearForm();
  $("#cam-id").value = cam.camera_id;
  $("#cam-id").disabled = true;
  $("#cam-label").value = cam.label || "";
  $("#cam-enabled").checked = cam.enabled !== false;
  const s = cam.source || cam;
  $("#cam-host").value = s.host || s.ip_address || "";
  $("#cam-port").value = s.port || s.rtsp_port || 554;
  $("#cam-user").value = s.username || "";
  $("#cam-pass").value = s.password || "";
  $("#cam-path").value = s.path || s.rtsp_path || "/Streaming/Channels/101";
  $("#cam-fps").value = s.fps || 20;
  const g = cam.output?.gateway || {};
  $("#gateway-enabled").checked = !!g.enabled;
  $("#gateway-path").value = g.path || cam.camera_id;
  $("#gateway-access").value = g.access_mode || "gateway";
  const r = cam.output?.relay || {};
  $("#relay-enabled").checked = !!r.enabled;
  $("#relay-port").value = r.listen_port || 8554;
  $("#relay-path").value = r.path_suffix || cam.camera_id;
  $("#relay-mode").value = r.mode || "listen";
  $("#relay-push").value = r.push_url || "";
  const w = cam.output?.webrtc || {};
  $("#webrtc-enabled").checked = !!w.enabled;
  $("#webrtc-mode").value = w.mode || "whep";
  const b = cam.buffer || {};
  $("#buf-duration").value = b.duration_seconds ?? 60;
  $("#buf-offset").value = b.default_playback_offset_sec ?? 6;
  $("#form-title").textContent = "Editar cámara";
}

function clearForm() {
  $("#camera-form").reset();
  $("#cam-id").disabled = false;
  $("#form-title").textContent = "Añadir cámara";
}

$("#btn-clear-form")?.addEventListener("click", clearForm);

$("#camera-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("#cam-id").value.trim();
  const payload = {
    camera_id: id,
    label: $("#cam-label").value.trim(),
    enabled: $("#cam-enabled").checked,
    source: {
      host: $("#cam-host").value.trim(),
      port: parseInt($("#cam-port").value, 10) || 554,
      username: $("#cam-user").value,
      password: $("#cam-pass").value,
      path: $("#cam-path").value.trim(),
      fps: parseInt($("#cam-fps").value, 10) || 20,
      width: 1280,
      height: 720,
    },
    output: {
      protocol:
        $("#gateway-enabled").checked || $("#relay-enabled").checked ? "rtsp" : "none",
      gateway: {
        enabled: $("#gateway-enabled").checked,
        access_mode: $("#gateway-access").value,
        path: $("#gateway-path").value.trim() || id,
      },
      relay: {
        enabled: $("#relay-enabled").checked,
        mode: $("#relay-mode").value,
        push_url: $("#relay-push").value.trim(),
        listen_port: parseInt($("#relay-port").value, 10) || 8554,
        path_suffix: $("#relay-path").value.trim() || id,
        iframe_interval_sec: 3,
        force_transcode_gop: false,
      },
      webrtc: {
        enabled: $("#webrtc-enabled").checked,
        mode: $("#webrtc-mode").value,
        rewind_offset_sec: 3,
      },
    },
    buffer: {
      duration_seconds: parseFloat($("#buf-duration").value) || 60,
      default_playback_offset_sec: parseFloat($("#buf-offset").value) || 6,
      event_pre_seconds: 6,
      event_post_seconds: 24,
    },
  };
  try {
    const editing = $("#cam-id").disabled;
    if (editing) {
      await api(`/api/v1/cameras/${id}`, { method: "PUT", json: payload });
      toast("Cámara actualizada");
    } else {
      await api("/api/v1/cameras", { method: "POST", json: payload });
      toast("Cámara creada");
    }
    clearForm();
    await loadCameras();
  } catch (err) {
    toast(err.message, true);
  }
});

async function deleteCamera(id) {
  if (!confirm(`¿Eliminar ${id}?`)) return;
  try {
    await api(`/api/v1/cameras/${id}`, { method: "DELETE" });
    toast("Eliminada");
    await loadCameras();
  } catch (err) {
    toast(err.message, true);
  }
}

function openTestPanel(cameraId) {
  $$(".tabs button").forEach((b) => b.classList.remove("active"));
  $('button[data-tab="test"]').classList.add("active");
  $$(".tab-panel").forEach((p) => p.classList.add("hidden"));
  $("#tab-test").classList.remove("hidden");
  $("#test-camera-id").value = cameraId;
  $("#test-snapshots").innerHTML = "";
  $("#test-log").textContent = "";
}

$("#btn-discovery")?.addEventListener("click", async () => {
  try {
    toast("Escaneando red…");
    const r = await api("/api/v1/discovery/scan", { method: "POST" });
    toast(`Descubiertas: ${r.discovered}, nuevas: ${r.provisioned_new}`);
    await loadCameras();
  } catch (err) {
    toast(err.message, true);
  }
});

function logTest(msg) {
  const el = $("#test-log");
  el.textContent += `[${new Date().toLocaleTimeString()}] ${msg}\n`;
  el.scrollTop = el.scrollHeight;
}

$("#btn-snap-source")?.addEventListener("click", async () => {
  const id = $("#test-camera-id").value;
  if (!id) return toast("Selecciona cámara", true);
  try {
    const blob = await api(`/api/v1/cameras/${id}/snapshot/source`);
    showSnapshot(blob, "Origen");
    logTest("Snapshot origen OK");
  } catch (err) {
    logTest("Origen: " + err.message);
    toast(err.message, true);
  }
});

$("#btn-snap-relay")?.addEventListener("click", async () => {
  const id = $("#test-camera-id").value;
  if (!id) return toast("Selecciona cámara", true);
  try {
    const blob = await api(`/api/v1/cameras/${id}/snapshot/relay`);
    showSnapshot(blob, "Relay");
    logTest("Snapshot relay OK");
  } catch (err) {
    logTest("Relay: " + err.message);
    toast(err.message, true);
  }
});

function showSnapshot(blob, label) {
  const box = $("#test-snapshots");
  const url = URL.createObjectURL(blob);
  const wrap = document.createElement("div");
  wrap.innerHTML = `<div style="color:var(--muted);margin-bottom:0.25rem">${label}</div>`;
  const img = document.createElement("img");
  img.src = url;
  wrap.appendChild(img);
  box.appendChild(wrap);
}

$("#btn-broadcast-start")?.addEventListener("click", async () => {
  const id = $("#test-camera-id").value;
  try {
    const r = await api(`/api/v1/cameras/${id}/broadcast/start`, { method: "POST" });
    logTest("Broadcast iniciado: " + JSON.stringify(r.relay?.mode || "ok"));
    toast("Broadcast iniciado");
  } catch (err) {
    logTest(err.message);
    toast(err.message, true);
  }
});

$("#btn-broadcast-stop")?.addEventListener("click", async () => {
  const id = $("#test-camera-id").value;
  try {
    await api(`/api/v1/cameras/${id}/broadcast/stop`, { method: "POST" });
    logTest("Broadcast detenido");
    toast("Broadcast detenido");
  } catch (err) {
    toast(err.message, true);
  }
});

$("#btn-test-playback")?.addEventListener("click", async () => {
  const id = $("#test-camera-id").value;
  const offset = parseFloat($("#test-offset").value) || 3;
  try {
    const meta = await api(`/api/v1/cameras/${id}/test/playback`, {
      method: "POST",
      json: { offset_sec: offset, live_tail: false },
    });
    logTest("Playback meta: " + JSON.stringify(meta));
    const blob = await api(
      `/api/v1/cameras/${id}/test/playback/stream?offset_sec=${offset}`
    );
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `playback-${id}-${offset}s.kanv`;
    a.click();
    toast("Descarga de stream iniciada");
  } catch (err) {
    toast(err.message, true);
  }
});

$("#btn-webrtc-connect")?.addEventListener("click", async () => {
  const id = $("#test-camera-id").value?.trim();
  if (!id) return toast("Indica ID de cámara", true);
  try {
    logTest("WebRTC: negociando WHEP…");
    await KanvisWebRtcViewer.connect(id, api);
    const badge = $("#webrtc-live-badge");
    badge.textContent = "LIVE";
    badge.classList.add("ok");
    logTest("WebRTC: conectado");
    toast("Visor WebRTC conectado");
  } catch (err) {
    logTest("WebRTC: " + err.message);
    toast(err.message, true);
    await KanvisWebRtcViewer.disconnect(api).catch(() => {});
    const badge = $("#webrtc-live-badge");
    badge.textContent = "OFF";
    badge.classList.remove("ok");
  }
});

$("#btn-webrtc-disconnect")?.addEventListener("click", async () => {
  try {
    await KanvisWebRtcViewer.disconnect(api);
    const badge = $("#webrtc-live-badge");
    badge.textContent = "OFF";
    badge.classList.remove("ok");
    logTest("WebRTC desconectado");
    toast("Visor cerrado");
  } catch (err) {
    toast(err.message, true);
  }
});

$("#btn-webrtc-rewind")?.addEventListener("click", async () => {
  const id = $("#test-camera-id").value?.trim();
  const offset = parseFloat($("#test-offset").value) || 3;
  if (!id || !KanvisWebRtcViewer.isConnected()) {
    return toast("Conecta WebRTC antes de rewind", true);
  }
  try {
    const r = await KanvisWebRtcViewer.rewind(id, offset, api);
    logTest(`Rewind ${offset}s: ${r.packets_queued} paquetes`);
    toast(`Rewind: ${r.packets_queued} paquetes en cola`);
  } catch (err) {
    logTest("Rewind: " + err.message);
    toast(err.message, true);
  }
});

$("#btn-refresh-status")?.addEventListener("click", async () => {
  const id = $("#test-camera-id").value;
  try {
    const st = await api(`/api/v1/cameras/${id}/status`);
    logTest(JSON.stringify(st, null, 2));
  } catch (err) {
    toast(err.message, true);
  }
});

async function loadSystem() {
  try {
    const [cfg, sys, conn] = await Promise.all([
      api("/api/v1/config"),
      api("/api/v1/system/info"),
      api("/api/v1/connectivity/status").catch(() => ({ state: null })),
    ]);
    $("#connectivity-info").textContent = JSON.stringify(conn, null, 2);
    $("#system-info").textContent = JSON.stringify({ config: cfg, system: sys }, null, 2);
    if (sys.webui_url) {
      toast(`Panel instalación: ${sys.webui_url} · WiFi ${sys.ap_ssid_hint}`);
    }
  } catch (err) {
    $("#system-info").textContent = err.message;
  }
}

async function initApp() {
  const session = await api("/api/v1/webui/session");
  $("#user-label").textContent = session.username || "";
  await loadCameras();
}

$("#btn-wan-sync")?.addEventListener("click", async () => {
  try {
    const r = await api("/api/v1/connectivity/sync?force=true", { method: "POST" });
    $("#connectivity-info").textContent = JSON.stringify(r, null, 2);
    toast("IP sincronizada (DDNS + nube)");
  } catch (err) {
    toast(err.message, true);
  }
});

(async function boot() {
  if (await checkSession()) {
    showApp(true);
    await initApp();
  } else {
    showApp(false);
  }
})();
