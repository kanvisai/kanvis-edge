/**
 * Kanvis Edge — panel móvil (cámaras por IP + canales)
 */

const TOKEN_KEY = "kanvis_token";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const PLAYBACK_OFFSET_SEC = 6;

let brandsCache = [];
let camerasCache = [];
/** Borradores por IP aún no guardados en API */
let draftDevices = [];
let activeDeviceKey = null;

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
  const id = window.KanvisWebRtcViewer?.getActiveCameraId?.();
  if (id) {
    window.KanvisWebRtcViewer.disconnect(api, id).catch(() => {});
  }
  setToken("");
  showApp(false);
}

function hostKey(host) {
  return (host || "").trim().toLowerCase();
}

function hostToSlug(host) {
  return hostKey(host).replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "");
}

function makeCameraId(host, channel) {
  const ch = String(channel || "101").replace(/\D/g, "") || "101";
  return `cam-${hostToSlug(host)}-ch${ch}`;
}

function groupCamerasByHost(cameras) {
  const map = new Map();
  for (const cam of cameras) {
    const host = cam.source?.host || cam.ip_address || "";
    if (!host) continue;
    const key = hostKey(host);
    if (!map.has(key)) {
      map.set(key, {
        key,
        host,
        brand: cam.source?.brand || "",
        port: cam.source?.port || 554,
        username: cam.source?.username || "",
        password: "",
        channels: [],
      });
    }
    const dev = map.get(key);
    if (cam.source?.brand && !dev.brand) dev.brand = cam.source.brand;
    dev.channels.push({
      channel: cam.source?.channel || "101",
      camera: cam,
      saved: true,
    });
  }
  for (const dev of map.values()) {
    dev.channels.sort((a, b) => String(a.channel).localeCompare(String(b.channel)));
  }
  return map;
}

function getAllDevices() {
  const saved = groupCamerasByHost(camerasCache);
  const list = [...draftDevices];
  for (const [key, dev] of saved) {
    const draft = list.find((d) => d.key === key);
    if (draft) {
      for (const ch of dev.channels) {
        if (!draft.channels.some((c) => c.channel === ch.channel)) {
          draft.channels.push({ ...ch });
        }
      }
      draft.brand = draft.brand || dev.brand;
    } else {
      const firstCam = dev.channels[0]?.camera;
      list.push({
        ...dev,
        fromApi: true,
        probeChannel: dev.probeChannel || firstCam?.source?.channel || "101",
        broadcastMode: firstCam?.output?.webrtc?.enabled ? "webrtc" : "rtsp",
        path: "/Streaming/Channels/101",
      });
    }
  }
  return list;
}

function brandOptionsHtml(selected = "") {
  let html = '<option value="">— Marca / plantilla RTSP —</option>';
  for (const b of brandsCache) {
    const sel = b.slug === selected ? " selected" : "";
    html += `<option value="${b.slug}"${sel}>${b.brand}</option>`;
  }
  return html;
}

function defaultChannelsForBrand(slug) {
  const b = brandsCache.find((x) => x.slug === slug);
  if (b?.default_channels?.length) {
    return b.default_channels.map((c) => ({ channel: c.id, label: c.label }));
  }
  return [{ channel: "101", label: "Principal" }, { channel: "102", label: "Sub" }];
}

function buildCameraPayload(device, channel, label, opts = {}) {
  const id = makeCameraId(device.host, channel);
  const mode = opts.broadcastMode || device.broadcastMode || "rtsp";
  const broadcastOn = !!opts.broadcastOn;
  const relayOn = broadcastOn && mode !== "webrtc";
  const webrtcOn = broadcastOn && mode === "webrtc";
  return {
    camera_id: id,
    label: label || `${device.host} ch${channel}`,
    enabled: true,
    source: {
      host: device.host.trim(),
      port: parseInt(device.port, 10) || 554,
      username: device.username || "",
      password: device.password || "",
      brand: device.brand || "",
      channel: String(channel),
      path: device.brand ? "" : device.path || "/Streaming/Channels/101",
      fps: 20,
      width: 1280,
      height: 720,
      transport: "tcp",
    },
    output: {
      protocol: broadcastOn ? (webrtcOn ? "webrtc" : "rtsp") : "none",
      gateway: { enabled: false, access_mode: "gateway", path: id },
      relay: {
        enabled: relayOn,
        mode: "listen",
        push_url: "",
        listen_port: 8554,
        path_suffix: id,
        iframe_interval_sec: 3,
        force_transcode_gop: false,
      },
      webrtc: {
        enabled: webrtcOn,
        mode: "whep",
        rewind_offset_sec: PLAYBACK_OFFSET_SEC,
      },
    },
    buffer: {
      duration_seconds: 60,
      default_playback_offset_sec: PLAYBACK_OFFSET_SEC,
      event_pre_seconds: 6,
      event_post_seconds: 24,
    },
  };
}

async function probeDevice(device, channel) {
  const body = {
    host: device.host.trim(),
    port: parseInt(device.port, 10) || 554,
    username: device.username || "",
    password: device.password || "",
    brand: device.brand || "",
    channel: String(channel || device.probeChannel || "101"),
    transport: "tcp",
  };
  const blob = await api("/api/v1/rtsp/probe", { method: "POST", json: body });
  return URL.createObjectURL(blob);
}

async function loadBrands() {
  try {
    const data = await api("/api/v1/brands");
    brandsCache = data.brands || [];
  } catch (err) {
    console.warn("brands", err);
  }
}

async function loadCameras() {
  try {
    camerasCache = await api("/api/v1/cameras");
    renderDevices();
  } catch (err) {
    toast(err.message, true);
  }
}

function selectDevice(key) {
  activeDeviceKey = key;
  renderDevices();
}

function addDraftDevice() {
  const key = `draft-${Date.now()}`;
  draftDevices.push({
    key,
    host: "",
    port: 554,
    username: "",
    password: "",
    brand: "",
    path: "/Streaming/Channels/101",
    probeChannel: "101",
    broadcastMode: "rtsp",
    channels: [{ channel: "101", label: "Principal", saved: false, camera: null }],
    fromApi: false,
  });
  activeDeviceKey = key;
  renderDevices();
}

function renderDeviceNav(devices) {
  const nav = $("#device-nav");
  nav.innerHTML = "";
  if (!devices.length) {
    $("#cameras-empty")?.classList.remove("hidden");
    return;
  }
  $("#cameras-empty")?.classList.add("hidden");
  if (!activeDeviceKey || !devices.some((d) => d.key === activeDeviceKey)) {
    activeDeviceKey = devices[0].key;
  }
  for (const dev of devices) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `device-tab${dev.key === activeDeviceKey ? " active" : ""}`;
    btn.textContent = dev.host?.trim() || "Nueva IP";
    btn.addEventListener("click", () => selectDevice(dev.key));
    nav.appendChild(btn);
  }
}

function renderChannelCard(device, chState, panel) {
  const ch = chState.channel;
  const cam = chState.camera;
  const cameraId = cam?.camera_id || makeCameraId(device.host, ch);
  const saved = chState.saved && !!cam;
  const mode = device.broadcastMode || "rtsp";

  const card = document.createElement("div");
  card.className = "channel-card";
  card.dataset.channel = ch;
  card.dataset.cameraId = cameraId;

  card.innerHTML = `
    <header>
      <strong>Canal ${ch}</strong>
      <span class="ch-status badge muted">—</span>
    </header>
    ${
      saved
        ? ""
        : `
      <label>Nº canal</label>
      <input type="text" class="ch-input" value="${ch}" inputmode="numeric" />
      <button type="button" class="btn-sm" data-save-extra="">Guardar este canal</button>
    `
    }
    ${
      saved
        ? `
      <p class="section-label">Broadcast (búfer 60 s)</p>
      <div class="mode-row">
        <label class="mode-opt"><input type="radio" name="bc-mode-${cameraId}" value="rtsp" ${mode === "rtsp" ? "checked" : ""}/> RTSP</label>
        <label class="mode-opt"><input type="radio" name="bc-mode-${cameraId}" value="webrtc" ${mode === "webrtc" ? "checked" : ""}/> WebRTC</label>
      </div>
      <button type="button" class="btn-block btn-toggle-bc off" data-toggle-bc="">Activar broadcast</button>
      <p class="buf-hint hint">El búfer solo se rellena con broadcast activo.</p>
      <button type="button" class="btn-block secondary" data-playback-test="" disabled>Probar playback (−${PLAYBACK_OFFSET_SEC} s)</button>
      <div class="playback-preview hidden">
        <p class="hint">Frame del búfer (comprueba la marca de agua / hora):</p>
        <img alt="Playback" />
      </div>
      <video class="channel-video hidden" playsinline muted autoplay></video>
      <button type="button" class="btn-sm danger" data-del-ch="">Eliminar canal</button>
    `
        : ""
    }
  `;

  if (!saved) {
    card.querySelector(".ch-input")?.addEventListener("change", (e) => {
      chState.channel = e.target.value.trim() || ch;
    });
    card.querySelector("[data-save-extra]")?.addEventListener("click", () =>
      saveExtraChannel(device, chState)
    );
  } else {
    card.querySelectorAll(`input[name="bc-mode-${cameraId}"]`).forEach((r) => {
      r.addEventListener("change", () => {
        device.broadcastMode = r.value;
      });
    });
    card.querySelector("[data-toggle-bc]")?.addEventListener("click", () =>
      toggleBroadcast(device, cam, card)
    );
    card.querySelector("[data-playback-test]")?.addEventListener("click", () =>
      testPlaybackBuffer(cameraId, card)
    );
    card.querySelector("[data-del-ch]")?.addEventListener("click", () => deleteCamera(cameraId));
    refreshChannelStatus(cameraId, card);
  }

  panel.appendChild(card);
}

function renderDevicePanel(device) {
  const panel = document.createElement("div");
  panel.className = "device-card";
  panel.dataset.deviceKey = device.key;

  panel.innerHTML = `
    <h2>
      <span class="ip-badge">${device.host?.trim() || "Nueva cámara"}</span>
      ${device.fromApi ? "" : '<button type="button" class="btn-sm danger" data-remove-device="">Eliminar</button>'}
    </h2>
    <div class="field-grid">
      <div class="span2">
        <label>Marca</label>
        <select class="dev-brand">${brandOptionsHtml(device.brand)}</select>
      </div>
      <div class="span2">
        <label>IP / host</label>
        <input class="dev-host" value="${device.host || ""}" placeholder="192.168.1.64" autocomplete="off" />
      </div>
      <div>
        <label>Puerto RTSP</label>
        <input class="dev-port" type="number" value="${device.port || 554}" />
      </div>
      <div>
        <label>Canal (prueba)</label>
        <input class="dev-probe-ch" value="${device.probeChannel || "101"}" inputmode="numeric" />
      </div>
      <div>
        <label>Usuario</label>
        <input class="dev-user" value="${device.username || ""}" autocomplete="username" />
      </div>
      <div>
        <label>Contraseña</label>
        <input class="dev-pass" type="password" value="${device.password || ""}" autocomplete="current-password" />
      </div>
    </div>
    <p class="section-label">1 · Conexión</p>
    <div class="probe-box">
      <div class="probe-preview">
        <p class="probe-placeholder">Prueba la URL RTSP antes de guardar</p>
      </div>
      <button type="button" class="btn-block secondary" data-probe="">Probar conexión</button>
      <p class="probe-msg hint"></p>
    </div>
    <p class="section-label">2 · Guardar</p>
    <button type="button" class="btn-block primary" data-save-device="">Guardar cámara</button>
    <p class="save-hint hint">Guarda la IP y el canal de prueba (${device.probeChannel || "101"}). Luego podrás activar broadcast y probar el búfer.</p>
    <p class="section-label">3 · Canales</p>
    <button type="button" class="btn-sm secondary" data-add-channel="">+ Añadir otro canal</button>
    <div class="channels-root"></div>
  `;

  const bind = (sel, fn) => {
    const el = panel.querySelector(sel);
    if (el) el.addEventListener("input", fn);
    if (el) el.addEventListener("change", fn);
  };

  bind(".dev-host", (e) => {
    device.host = e.target.value;
    renderDevices();
  });
  bind(".dev-port", (e) => {
    device.port = e.target.value;
  });
  bind(".dev-user", (e) => {
    device.username = e.target.value;
  });
  bind(".dev-pass", (e) => {
    device.password = e.target.value;
  });
  bind(".dev-probe-ch", (e) => {
    device.probeChannel = e.target.value;
  });
  panel.querySelector(".dev-brand")?.addEventListener("change", (e) => {
    device.brand = e.target.value;
    if (!device.channels.length) {
      device.channels = defaultChannelsForBrand(device.brand).map((c) => ({
        channel: c.channel,
        label: c.label,
        saved: false,
        camera: null,
      }));
    }
    renderDevices();
  });

  panel.querySelector("[data-probe]")?.addEventListener("click", async () => {
    const msg = panel.querySelector(".probe-msg");
    const preview = panel.querySelector(".probe-preview");
    if (!device.host?.trim()) {
      return toast("Indica la IP", true);
    }
    msg.textContent = "Capturando frame…";
    try {
      const url = await probeDevice(device, device.probeChannel);
      preview.innerHTML = `<img src="${url}" alt="Vista previa" />`;
      msg.textContent = "Cámara detectada";
      const oldKey = device.key;
      device.key = hostKey(device.host);
      if (oldKey !== device.key) {
        const idx = draftDevices.findIndex((d) => d.key === oldKey);
        if (idx >= 0) draftDevices[idx].key = device.key;
        activeDeviceKey = device.key;
      }
    } catch (err) {
      preview.innerHTML = '<p class="probe-placeholder">Sin imagen</p>';
      msg.textContent = err.message;
      toast(err.message, true);
    }
  });

  panel.querySelector("[data-save-device]")?.addEventListener("click", () =>
    saveCameraDevice(device, panel)
  );

  panel.querySelector("[data-add-channel]")?.addEventListener("click", () => {
    const existing = new Set(device.channels.map((c) => c.channel));
    let next = "101";
    for (const n of ["101", "102", "201", "202", "1", "2"]) {
      if (!existing.has(n)) {
        next = n;
        break;
      }
    }
    device.channels.push({ channel: next, saved: false, camera: null });
    renderDevices();
  });

  panel.querySelector("[data-remove-device]")?.addEventListener("click", () => {
    draftDevices = draftDevices.filter((d) => d.key !== device.key);
    if (activeDeviceKey === device.key) activeDeviceKey = null;
    renderDevices();
  });

  const chRoot = panel.querySelector(".channels-root");
  for (const chState of device.channels) {
    renderChannelCard(device, chState, chRoot);
  }

  return panel;
}

function renderDevices() {
  const devices = getAllDevices();
  renderDeviceNav(devices);
  const panels = $("#device-panels");
  panels.innerHTML = "";
  if (!devices.length) {
    $("#cameras-empty")?.classList.remove("hidden");
    return;
  }
  $("#cameras-empty")?.classList.add("hidden");
  const active = devices.find((d) => d.key === activeDeviceKey) || devices[0];
  panels.appendChild(renderDevicePanel(active));
}

async function persistCamera(device, channel, label) {
  const cameraId = makeCameraId(device.host, channel);
  const payload = buildCameraPayload(device, channel, label);
  payload.camera_id = cameraId;
  const existing = camerasCache.find((c) => c.camera_id === cameraId);
  if (existing) {
    await api(`/api/v1/cameras/${cameraId}`, { method: "PUT", json: payload });
  } else {
    await api("/api/v1/cameras", { method: "POST", json: payload });
  }
  return cameraId;
}

async function saveCameraDevice(device, panel) {
  if (!device.host?.trim()) return toast("Indica la IP", true);
  if (!device.brand) return toast("Elige la marca de la cámara", true);
  const channel = String(device.probeChannel || "101").trim();
  try {
    await persistCamera(device, channel, `${device.host} ch${channel}`);
    if (!device.channels.some((c) => c.channel === channel)) {
      device.channels.unshift({ channel, saved: true, camera: null });
    }
    draftDevices = draftDevices.filter((d) => d.key !== device.key);
    device.key = hostKey(device.host);
    device.fromApi = true;
    device.probeOk = true;
    await loadCameras();
    activeDeviceKey = device.key;
    toast(`Cámara guardada (canal ${channel})`);
    panel.querySelector(".save-hint").textContent =
      "Guardada. Activa broadcast en el canal para llenar el búfer de 60 s.";
  } catch (err) {
    toast(err.message, true);
  }
}

async function saveExtraChannel(device, chState) {
  if (!device.host?.trim()) return toast("Indica la IP", true);
  const channel = (chState.channel || "101").trim();
  try {
    await persistCamera(device, channel, `${device.host} ch${channel}`);
    toast(`Canal ${channel} guardado`);
    await loadCameras();
    activeDeviceKey = device.key;
  } catch (err) {
    toast(err.message, true);
  }
}

function isBroadcastRunning(st) {
  return !!(
    st.relay?.running ||
    st.webrtc?.session?.connection_state === "connected" ||
    (st.ingest?.connected && (st.buffer_span_seconds || 0) > 0.5)
  );
}

async function refreshChannelStatus(cameraId, card) {
  const badge = card.querySelector(".ch-status");
  const toggle = card.querySelector("[data-toggle-bc]");
  const playbackBtn = card.querySelector("[data-playback-test]");
  const bufHint = card.querySelector(".buf-hint");
  try {
    const st = await api(`/api/v1/cameras/${cameraId}/status`);
    const ing = st.ingest?.connected;
    const relay = st.relay?.running;
    const rtc = st.webrtc?.session?.connection_state === "connected";
    const span = st.buffer_span_seconds || 0;
    const bcOn = isBroadcastRunning(st);

    let html = ing
      ? '<span class="badge ok">Ingesta OK</span>'
      : '<span class="badge warn">Sin ingesta</span>';
    if (relay) html += ' <span class="badge ok">RTSP</span>';
    if (rtc) html += ' <span class="badge ok">WebRTC</span>';
    html += ` <span class="badge muted">Búfer ${span.toFixed(0)}s</span>`;
    badge.innerHTML = html;

    if (toggle) {
      toggle.textContent = bcOn ? "Desactivar broadcast" : "Activar broadcast";
      toggle.classList.toggle("on", bcOn);
      toggle.classList.toggle("off", !bcOn);
    }
    if (bufHint) {
      bufHint.textContent = bcOn
        ? `Búfer: ${span.toFixed(0)}s / ${st.buffer_max_duration_seconds || 60}s`
        : "El búfer solo se rellena con broadcast activo.";
    }
    if (playbackBtn) {
      playbackBtn.disabled = !bcOn || span < PLAYBACK_OFFSET_SEC * 0.85;
    }
    card.dataset.broadcastOn = bcOn ? "1" : "0";
  } catch {
    badge.innerHTML = '<span class="badge muted">Sin guardar / inactiva</span>';
    if (toggle) {
      toggle.textContent = "Activar broadcast";
      toggle.classList.add("off");
    }
    if (playbackBtn) playbackBtn.disabled = true;
  }
}

async function toggleBroadcast(device, cam, card) {
  const cameraId = cam.camera_id;
  const mode =
    card.querySelector(`input[name="bc-mode-${cameraId}"]:checked`)?.value ||
    device.broadcastMode ||
    "rtsp";
  device.broadcastMode = mode;
  const isOn = card.dataset.broadcastOn === "1";

  try {
    if (!isOn) {
      const payload = buildCameraPayload(
        {
          host: cam.source.host,
          port: cam.source.port,
          username: cam.source.username,
          password: device.password || "",
          brand: cam.source.brand,
          broadcastMode: mode,
        },
        cam.source.channel,
        cam.label,
        { broadcastOn: true, broadcastMode: mode }
      );
      payload.camera_id = cameraId;
      await api(`/api/v1/cameras/${cameraId}`, { method: "PUT", json: payload });

      const url =
        mode === "webrtc"
          ? `/api/v1/cameras/${cameraId}/broadcast/start?mode=webrtc`
          : `/api/v1/cameras/${cameraId}/broadcast/start`;
      await api(url, { method: "POST" });

      if (mode === "webrtc") {
        const video = card.querySelector("video");
        await KanvisWebRtcViewer.connect(cameraId, api, {
          video,
          onState: (s) => {
            if (s === "connected") toast("WebRTC conectado");
          },
        });
        video?.classList.remove("hidden");
      }
      toast("Broadcast activado — rellenando búfer…");
      let polls = 0;
      const pollId = setInterval(() => {
        refreshChannelStatus(cameraId, card);
        if (++polls >= 20) clearInterval(pollId);
      }, 2000);
    } else {
      if (KanvisWebRtcViewer.getActiveCameraId() === cameraId) {
        await KanvisWebRtcViewer.disconnect(api, cameraId);
        const video = card.querySelector("video");
        if (video) {
          video.srcObject = null;
          video.classList.add("hidden");
        }
      }
      await api(`/api/v1/cameras/${cameraId}/broadcast/stop`, { method: "POST" });
      const payload = buildCameraPayload(
        {
          host: cam.source.host,
          port: cam.source.port,
          username: cam.source.username,
          password: device.password || "",
          brand: cam.source.brand,
          broadcastMode: mode,
        },
        cam.source.channel,
        cam.label,
        { broadcastOn: false }
      );
      payload.camera_id = cameraId;
      await api(`/api/v1/cameras/${cameraId}`, { method: "PUT", json: payload });
      toast("Broadcast desactivado");
    }
    await loadCameras();
    await refreshChannelStatus(cameraId, card);
  } catch (err) {
    toast(err.message, true);
  }
}

async function testPlaybackBuffer(cameraId, card) {
  const box = card.querySelector(".playback-preview");
  const img = box?.querySelector("img");
  try {
    toast(`Capturando frame de hace ${PLAYBACK_OFFSET_SEC}s…`);
    const blob = await api(
      `/api/v1/cameras/${cameraId}/snapshot/buffer?offset_sec=${PLAYBACK_OFFSET_SEC}`
    );
    if (img) {
      img.src = URL.createObjectURL(blob);
      box.classList.remove("hidden");
    }
    toast("Compara la marca de agua con la hora actual");
  } catch (err) {
    toast(err.message, true);
  }
}

async function deleteCamera(id) {
  if (!confirm(`¿Eliminar canal ${id}?`)) return;
  try {
    if (KanvisWebRtcViewer.getActiveCameraId() === id) {
      await KanvisWebRtcViewer.disconnect(api, id);
    }
    await api(`/api/v1/cameras/${id}`, { method: "DELETE" });
    toast("Canal eliminado");
    await loadCameras();
  } catch (err) {
    toast(err.message, true);
  }
}

/* —— Navegación inferior —— */
$$(".bottom-nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".bottom-nav button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $$(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${tab}`));
    if (tab === "cameras") loadCameras();
    if (tab === "system") {
      loadSystem();
      loadOperatingSchedule();
    }
  });
});

$("#btn-add-device")?.addEventListener("click", addDraftDevice);

/* —— Login —— */
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

/* —— Horario —— */
const SCHED_DAY_LABELS = [
  { v: 0, l: "Lun" },
  { v: 1, l: "Mar" },
  { v: 2, l: "Mié" },
  { v: 3, l: "Jue" },
  { v: 4, l: "Vie" },
  { v: 5, l: "Sáb" },
  { v: 6, l: "Dom" },
];

function renderScheduleWindows(windows) {
  const root = $("#sched-windows");
  if (!root) return;
  root.innerHTML = "";
  const list = windows?.length ? windows : [];
  if (!list.length) {
    root.innerHTML =
      '<p class="hint">Sin franjas. Añade una (ej. 08:50–14:05 lun–sáb).</p>';
    return;
  }
  list.forEach((win, idx) => {
    const row = document.createElement("div");
    row.className = "schedule-window-row";
    const days = win.days || [];
    const dayChecks = SCHED_DAY_LABELS.map(
      (d) =>
        `<label><input type="checkbox" data-day="${d.v}" ${days.includes(d.v) ? "checked" : ""}/> ${d.l}</label>`
    ).join("");
    row.innerHTML = `
      <div class="actions" style="margin-bottom:0.5rem">
        <label>Inicio <input type="time" data-field="start" value="${win.start || "08:00"}" /></label>
        <label>Fin <input type="time" data-field="end" value="${win.end || "18:00"}" /></label>
        <button type="button" class="btn-sm danger secondary" data-remove="${idx}">Quitar</button>
      </div>
      <div class="sched-days">${dayChecks}</div>
    `;
    root.appendChild(row);
  });
  root.querySelectorAll("[data-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = parseInt(btn.dataset.remove, 10);
      const next = collectScheduleFromUi().windows;
      next.splice(i, 1);
      renderScheduleWindows(next);
    });
  });
}

function collectScheduleFromUi() {
  const enabled = $("#sched-enabled")?.checked ?? false;
  const timezone = ($("#sched-timezone")?.value || "").trim();
  const windows = [];
  $$("#sched-windows .schedule-window-row").forEach((row) => {
    const startRaw = row.querySelector('[data-field="start"]')?.value || "08:00";
    const endRaw = row.querySelector('[data-field="end"]')?.value || "18:00";
    const days = [];
    row.querySelectorAll("[data-day]").forEach((cb) => {
      if (cb.checked) days.push(parseInt(cb.dataset.day, 10));
    });
    windows.push({
      start: startRaw.length === 5 ? startRaw : startRaw.slice(0, 5),
      end: endRaw.length === 5 ? endRaw : endRaw.slice(0, 5),
      days,
    });
  });
  return { enabled, timezone, windows };
}

function updateScheduleStatusLine(status) {
  const el = $("#sched-status-line");
  if (!el || !status) return;
  const active = status.is_active_now;
  const badge = active
    ? '<span class="badge ok">Activo ahora</span>'
    : '<span class="badge warn">Inactivo ahora</span>';
  const when = status.local_time
    ? ` · ${status.weekday_label || ""} ${status.local_time.replace("T", " ")}`
    : "";
  el.innerHTML = `${badge}${when}`;
}

async function loadOperatingSchedule() {
  try {
    const data = await api("/api/v1/operating-schedule");
    $("#sched-enabled").checked = !!data.schedule?.enabled;
    $("#sched-timezone").value = data.schedule?.timezone || "";
    renderScheduleWindows(data.schedule?.windows || []);
    updateScheduleStatusLine(data.status);
  } catch (err) {
    $("#sched-status-line").textContent = err.message;
  }
}

$("#btn-sched-add-window")?.addEventListener("click", () => {
  const cur = collectScheduleFromUi().windows;
  cur.push({ start: "08:50", end: "14:05", days: [0, 1, 2, 3, 4, 5] });
  renderScheduleWindows(cur);
});

$("#btn-sched-save")?.addEventListener("click", async () => {
  const body = collectScheduleFromUi();
  if (body.enabled && !body.windows.length) {
    return toast("Añade al menos una franja o desactiva el horario", true);
  }
  for (const w of body.windows) {
    if (!w.days.length) {
      return toast("Cada franja debe tener al menos un día", true);
    }
  }
  try {
    const r = await api("/api/v1/operating-schedule", { method: "PUT", json: body });
    updateScheduleStatusLine(r.status);
    toast("Horario guardado");
    await loadCameras();
  } catch (err) {
    toast(err.message, true);
  }
});

async function loadSystem() {
  try {
    const [sys, conn] = await Promise.all([
      api("/api/v1/system/info"),
      api("/api/v1/connectivity/status").catch(() => ({ state: null })),
      loadOperatingSchedule(),
    ]);
    $("#connectivity-info").textContent = JSON.stringify(conn, null, 2);
    $("#sys-device-name").textContent = sys.device_name || "—";
    $("#sys-device-id").textContent = sys.device_id || "—";
    updateDeviceHeader(null, sys);
  } catch (err) {
    toast(err.message, true);
  }
}

function updateDeviceHeader(session, sys) {
  const name = sys?.device_name || session?.device_name;
  const id = sys?.device_id || session?.device_id;
  const el = $("#device-label");
  if (!el) return;
  if (name) {
    el.textContent = id ? `${name} (${id})` : name;
  } else if (id) {
    el.textContent = id;
  } else {
    el.textContent = "";
  }
}

async function initApp() {
  const session = await api("/api/v1/webui/session");
  updateDeviceHeader(session, null);
  await loadBrands();
  await loadCameras();
}

$("#btn-wan-sync")?.addEventListener("click", async () => {
  try {
    const r = await api("/api/v1/connectivity/sync?force=true", { method: "POST" });
    $("#connectivity-info").textContent = JSON.stringify(r, null, 2);
    toast("IP sincronizada");
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
