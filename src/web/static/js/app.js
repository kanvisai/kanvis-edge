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
/** interval por camera_id mientras broadcast activo */
const channelStatusPollers = new Map();

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

/** Mensaje fijo copiable (verde=ok, rojo=error) debajo de botones de acción. */
function setActionMsg(el, text, kind = "") {
  if (!el) return;
  const t = text == null ? "" : String(text);
  el.textContent = t;
  el.classList.remove("ok", "err", "hidden");
  if (!t) {
    el.classList.add("hidden");
    return;
  }
  if (kind === "ok") el.classList.add("ok");
  else if (kind === "err") el.classList.add("err");
}

function syncDeviceCredsFromDom(device) {
  const panel = document.querySelector(`.device-card[data-device-key="${device.key}"]`);
  if (!panel) return;
  const pass = panel.querySelector(".dev-pass")?.value;
  if (pass != null && pass !== "") device.password = pass;
  const user = panel.querySelector(".dev-user")?.value;
  if (user != null) device.username = user;
}

function resolveRtspPassword(device, cam, card) {
  const ov = card?.querySelector("[data-bc-override]")?.checked;
  if (ov) {
    const p = card.querySelector(".ov-pass")?.value;
    if (p) return p;
  }
  syncDeviceCredsFromDom(device);
  return device.password || cam?.source?.password || "";
}

function formatApiDetail(detail) {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => {
        if (typeof e === "string") return e;
        const loc = Array.isArray(e.loc) ? e.loc.join(".") : "";
        return e.msg ? `${loc}: ${e.msg}` : JSON.stringify(e);
      })
      .join(" · ");
  }
  if (typeof detail === "object" && detail.msg) return detail.msg;
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
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
      detail = formatApiDetail(j.detail ?? j.message ?? j);
    } catch (_) {}
    throw new Error(detail ? `${detail} (HTTP ${res.status})` : `HTTP ${res.status}`);
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

function channelSlug(channel) {
  const raw = String(channel ?? "101").trim() || "101";
  const slug = raw.toLowerCase().replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "");
  return slug || "101";
}

/** ID antiguo (solo dígitos): stream1→ch1, stream2→ch2 — compatibilidad */
function legacyMakeCameraId(host, channel) {
  const ch = String(channel || "101").replace(/\D/g, "") || "101";
  return `cam-${hostToSlug(host)}-ch${ch}`;
}

function makeCameraId(host, channel) {
  return `cam-${hostToSlug(host)}-ch-${channelSlug(channel)}`;
}

function findCameraByHostChannel(host, channel) {
  const hk = hostKey(host);
  const ch = String(channel);
  return camerasCache.find(
    (c) =>
      hostKey(c.source?.host || c.ip_address || "") === hk &&
      String(c.source?.channel ?? "") === ch
  );
}

function resolveCameraId(host, channel) {
  const found = findCameraByHostChannel(host, channel);
  if (found) return found.camera_id;
  const id = makeCameraId(host, channel);
  if (camerasCache.some((c) => c.camera_id === id)) return id;
  const legacy = legacyMakeCameraId(host, channel);
  if (camerasCache.some((c) => c.camera_id === legacy)) return legacy;
  return id;
}

function mergeChannelLists(apiChannels, draftChannels) {
  const byCh = new Map();
  for (const ch of apiChannels || []) {
    if (ch.saved !== false && ch.camera) {
      byCh.set(String(ch.channel), { ...ch, saved: true, camera: ch.camera });
    }
  }
  for (const ch of draftChannels || []) {
    if (!ch.saved) {
      const key = String(ch.channel);
      if (!byCh.has(key)) byCh.set(key, ch);
    }
  }
  return [...byCh.values()].sort((a, b) =>
    String(a.channel).localeCompare(String(b.channel))
  );
}

function syncDeviceChannelsFromCache(device) {
  if (!device?.host?.trim()) return;
  const hk = hostKey(device.host);
  const fromApi = camerasCache
    .filter((c) => hostKey(c.source?.host || "") === hk)
    .map((cam) => ({
      channel: cam.source?.channel || "101",
      saved: true,
      camera: cam,
    }));
  const unsaved = (device.channels || []).filter((c) => !c.saved);
  device.channels = mergeChannelLists(fromApi, unsaved);
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
        password: cam.source?.password || "",
        channels: [],
      });
    }
    const dev = map.get(key);
    if (cam.source?.brand && !dev.brand) dev.brand = cam.source.brand;
    if (cam.source?.password) dev.password = cam.source.password;
    if (cam.source?.username) dev.username = cam.source.username;
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

function upsertDeviceDraft(device) {
  const key = device.key || hostKey(device.host) || `draft-${Date.now()}`;
  device.key = key;
  const idx = draftDevices.findIndex((d) => d.key === key);
  const snap = { ...device, key };
  if (idx >= 0) draftDevices[idx] = { ...draftDevices[idx], ...snap };
  else draftDevices.push(snap);
}

function broadcastModeFromCamera(cam) {
  const out = cam?.output || {};
  if (out.webrtc?.enabled || out.protocol === "webrtc") return "webrtc";
  if (out.relay?.enabled || out.protocol === "rtsp") return "rtsp";
  return "rtsp";
}

function mergeDraftWithSaved(draft, dev) {
  return {
    ...dev,
    ...draft,
    key: draft.key || dev.key,
    host: draft.host ?? dev.host,
    port: draft.port ?? dev.port,
    brand: draft.brand ?? dev.brand,
    username: draft.username !== undefined ? draft.username : dev.username,
    password:
      draft.password !== undefined && draft.password !== ""
        ? draft.password
        : dev.password || "",
    probeChannel: draft.probeChannel ?? dev.probeChannel,
    broadcastMode: draft.broadcastMode ?? dev.broadcastMode,
    channels: mergeChannelLists(dev.channels, draft.channels),
    fromApi: draft.fromApi !== undefined ? draft.fromApi : dev.fromApi,
  };
}

function getAllDevices() {
  const saved = groupCamerasByHost(camerasCache);
  const list = [...draftDevices];
  for (const [key, dev] of saved) {
    const draft = list.find((d) => d.key === key);
    if (draft) {
      const i = list.indexOf(draft);
      list[i] = mergeDraftWithSaved(draft, dev);
    } else {
      const firstCam = dev.channels[0]?.camera;
      list.push({
        ...dev,
        fromApi: true,
        probeChannel: dev.probeChannel || firstCam?.source?.channel || "101",
        broadcastMode: broadcastModeFromCamera(firstCam),
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
  const id =
    opts.cameraId ?? resolveCameraId(device.host, channel) ?? makeCameraId(device.host, channel);
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

function showProbeCodecBadge(panel, probe) {
  const row = panel?.querySelector("[data-probe-codec-row]");
  const badge = panel?.querySelector("[data-probe-codec-badge]");
  const rec = panel?.querySelector("[data-probe-codec-rec]");
  if (!row || !badge) return;
  if (!probe?.codec) {
    row.classList.add("hidden");
    badge.textContent = "";
    if (rec) rec.textContent = probe?.metaError || "";
    return;
  }
  row.classList.remove("hidden");
  const reso = probe.resolution ? ` · ${probe.resolution}` : "";
  badge.textContent = `Códec: ${probe.codec.toUpperCase()}${reso}`;
  badge.classList.toggle("hevc", /hevc|h265|hev/i.test(probe.codec));
  badge.classList.toggle("h264", /h264|avc/i.test(probe.codec));
  if (rec) {
    const mode =
      probe.recommendation === "webrtc"
        ? "→ Usa broadcast WebRTC"
        : probe.recommendation === "rtsp"
          ? "→ Usa broadcast RTSP (relay)"
          : "";
    rec.textContent = [probe.hint, mode].filter(Boolean).join(" ");
  }
}

async function fetchProbeCodecMeta(body) {
  const paths = ["/api/v1/tools/rtsp-probe-meta", "/api/v1/rtsp/probe-meta"];
  let lastErr = "";
  for (const path of paths) {
    try {
      return await api(path, { method: "POST", json: body });
    } catch (err) {
      lastErr = err.message || String(err);
      const msg = String(lastErr);
      if (msg.includes("404") || msg.includes("405")) continue;
      return { ok: false, codec_detected: false, error: lastErr };
    }
  }
  return { ok: false, codec_detected: false, error: lastErr || "meta no disponible (deploy?)" };
}

async function probeDevice(device, channel) {
  const ch = String(channel ?? device.probeChannel ?? "").trim() || "101";
  device.probeChannel = ch;
  const body = {
    host: device.host.trim(),
    port: parseInt(device.port, 10) || 554,
    username: device.username || "",
    password: device.password || "",
    brand: (device.brand || "").trim(),
    channel: ch,
    transport: "tcp",
  };
  if (!body.brand) {
    throw new Error("Elige la marca (Annke, etc.) para armar la URL RTSP");
  }

  const meta = await fetchProbeCodecMeta(body);
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const imagePaths = [
    "/api/v1/tools/rtsp-probe",
    "/api/v1/rtsp/probe",
    "/api/v1/cameras/probe",
  ];
  let lastErr;
  for (const path of imagePaths) {
    try {
      const res = await fetch(path, { method: "POST", headers, body: JSON.stringify(body) });
      if (res.status === 401) {
        logout();
        throw new Error("Sesión expirada");
      }
      if (!res.ok) {
        let detail = res.statusText;
        try {
          const j = await res.json();
          detail = formatApiDetail(j.detail ?? j.message ?? j);
        } catch (_) {}
        throw new Error(detail ? `${detail} (HTTP ${res.status})` : `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const codec =
        meta.codec_name ||
        res.headers.get("X-Kanvis-Video-Codec") ||
        "";
      const rec =
        meta.recommendation ||
        res.headers.get("X-Kanvis-Broadcast-Recommendation") ||
        "";
      const hint =
        meta.recommendation_label ||
        res.headers.get("X-Kanvis-Codec-Hint") ||
        "";
      const reso =
        meta.resolution ||
        res.headers.get("X-Kanvis-Video-Resolution") ||
        "";
      return {
        objectUrl: URL.createObjectURL(blob),
        codec,
        recommendation: rec,
        hint,
        resolution: reso,
        metaError: meta.ok ? "" : meta.error || "",
      };
    } catch (err) {
      lastErr = err;
      const msg = String(err.message || "");
      if (!msg.includes("404") && !msg.includes("405")) throw err;
    }
  }
  throw new Error(
    `${lastErr?.message || "Probe no disponible"}. En el guardia ejecuta: sudo ./scripts/deploy.sh --yes y recarga la web (Ctrl+F5). Comprueba: curl http://127.0.0.1:8000/api/v1/health`
  );
}

function webrtcViewerHref(viewerUrl) {
  if (!viewerUrl) return "";
  const tok = getToken();
  if (!tok) return viewerUrl;
  const sep = viewerUrl.includes("?") ? "&" : "?";
  return `${viewerUrl}${sep}token=${encodeURIComponent(tok)}`;
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
    for (const cam of camerasCache) {
      const key = hostKey(cam.source?.host || "");
      const draft = draftDevices.find((d) => d.key === key);
      if (draft && cam.source?.password) draft.password = cam.source.password;
    }
    for (const d of draftDevices) syncDeviceChannelsFromCache(d);
    renderDevices();
  } catch (err) {
    toast(err.message, true);
  }
}

function selectDevice(key) {
  activeDeviceKey = key;
  renderDevices();
}

/** Actualiza pestaña y título sin re-renderizar el formulario (evita perder foco al escribir). */
function updateDeviceNavLabel(device) {
  const label = device.host?.trim() || "Nueva IP";
  $("#device-nav")?.querySelectorAll(".device-tab").forEach((btn) => {
    if (btn.classList.contains("active")) btn.textContent = label;
  });
  const badge = $("#device-panels")?.querySelector(".device-card .ip-badge");
  if (badge) badge.textContent = label === "Nueva IP" ? "Nueva cámara" : label;
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

function getSavedChannels(device) {
  syncDeviceChannelsFromCache(device);
  return device.channels.filter((c) => c.saved && c.camera?.camera_id);
}

function ensureActiveChannel(device) {
  const saved = getSavedChannels(device);
  if (!saved.length) {
    device.activeChannel = null;
    return;
  }
  if (!saved.some((c) => c.channel === device.activeChannel)) {
    device.activeChannel = saved[0].channel;
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function updateBroadcastModeUi(card, mode) {
  const label = card?.querySelector("[data-bc-override-label]");
  if (!label) return;
  label.textContent =
    mode === "webrtc"
      ? "Otros datos de ingesta (RTSP origen)"
      : "Otros datos RTSP al activar broadcast";
}

function renderConnectionHints(el, info, mode) {
  if (!el || !info) return;
  const s = info.source || {};
  const originRtsp = s.rtsp_url || info.device_rtsp_masked || "—";
  let html = "";
  if (info.preview) {
    html += `<p class="hint">Vista previa con los datos del formulario (sin guardar aún).</p>`;
  }
  const pu = info.panel_urls || {};
  if (pu.lan) {
    html += `<p class="hint"><strong>Panel en esta red (usa esta URL):</strong> <a class="conn-link" href="${escapeHtml(
      pu.lan
    )}/" target="_blank" rel="noopener">${escapeHtml(pu.lan)}/</a> <button type="button" class="btn-sm" data-copy-url="${escapeHtml(
      `${pu.lan}/`
    )}">Copiar</button></p>`;
  }
  if (pu.public_ip) {
    html += `<p class="hint"><strong>IP pública (desde internet):</strong> ${escapeHtml(
      pu.public_ip
    )} <button type="button" class="btn-sm" data-refresh-public-ip="">Actualizar IP</button></p>`;
  }
  if (pu.public && pu.lan && pu.public.replace(/\/$/, "") !== pu.lan.replace(/\/$/, "")) {
    html += `<p class="hint">Desde fuera / 4G: <code>${escapeHtml(pu.public)}/</code> — reenvío puerto 8000 en el router. Desde la misma WiFi la IP pública a menudo <strong>no abre</strong>; usa la URL LAN de arriba.</p>`;
  }
  if (pu.note) {
    html += `<p class="hint">${escapeHtml(pu.note)}</p>`;
  }
  if (pu.access && pu.public && pu.access !== pu.public) {
    html += `<p class="hint">Navegador actual: <code>${escapeHtml(pu.access)}</code></p>`;
  }
  html += `<p class="section-label">Datos de conexión</p>`;
  html += `<div class="conn-block"><strong>Origen cámara (RTSP)</strong><pre class="conn-pre">${escapeHtml(
    originRtsp
  )}</pre><p class="hint">${escapeHtml(s.mpv || "")}</p></div>`;

  if (mode === "rtsp") {
    const r = info.relay?.url_lan ? info.relay : info.relay_preview;
    if (r?.url_lan) {
      const running = r.running || info.relay?.running;
      html += `<div class="conn-block"><strong>Broadcast RTSP (relay)</strong><pre class="conn-pre">${escapeHtml(
        r.url_lan
      )}\nEn el guardia: ${escapeHtml(r.url_local || "")}</pre><p class="hint">${escapeHtml(
        r.mpv || ""
      )}</p>`;
      if (!running) {
        html += `<p class="hint">Activa broadcast para que el relay escuche en el puerto ${escapeHtml(
          String(r.listen_port || "?")
        )}.</p>`;
      } else {
        html += `<p class="hint">Relay activo — prueba con mpv/vlc en la misma LAN.</p>`;
      }
      html += `</div>`;
    } else if (r?.error) {
      html += `<p class="hint err-text">${escapeHtml(r.error)}</p>`;
    }
  } else {
    const w = info.webrtc || {};
    html += `<div class="conn-block"><strong>Cómo ver el vídeo (WebRTC)</strong><ol class="conn-steps">`;
    const steps = w.human_steps?.length
      ? w.human_steps
      : [
          "Activa broadcast: el reproductor de vídeo debe aparecer en esta misma tarjeta.",
          "Si no hay imagen, espera unos segundos y revisa «Ingesta OK».",
        ];
    for (const s of steps) {
      html += `<li>${escapeHtml(s)}</li>`;
    }
    html += `</ol>`;
    const viewerHref = webrtcViewerHref(w.viewer_url || "");
    if (viewerHref) {
      html += `<p class="hint"><strong>Ver vídeo (nueva pestaña):</strong></p>`;
      html += `<p><a class="conn-link" href="${escapeHtml(viewerHref)}" target="_blank" rel="noopener">${escapeHtml(
        viewerHref
      )}</a> <button type="button" class="btn-sm" data-copy-url="${escapeHtml(
        viewerHref
      )}">Copiar</button></p>`;
      html += `<p class="hint">Activa broadcast WebRTC antes. Si no llevas token, la pestaña pedirá login.</p>`;
    }
    if (w.panel_url) {
      html += `<p class="hint">Panel de configuración: <a class="conn-link" href="${escapeHtml(
        w.panel_url
      )}" target="_blank" rel="noopener">${escapeHtml(w.panel_url)}</a></p>`;
    }
    html += `</div>`;
    html += `<details class="conn-advanced"><summary>Detalles técnicos (desarrolladores)</summary><pre class="conn-pre">POST ${escapeHtml(
      w.whep_offer_url || "—"
    )}\nEstado: ${escapeHtml(w.status_url || "")}</pre>`;
    if (w.curl_check) {
      html += `<p class="hint">Comprobar API en terminal (opcional):</p><pre class="conn-pre conn-curl">${escapeHtml(
        w.curl_check
      )}</pre>`;
    }
    html += `</details></div>`;
  }
  el.innerHTML = html;
  el.querySelectorAll("[data-copy-url]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const url = btn.getAttribute("data-copy-url");
      try {
        await navigator.clipboard.writeText(url);
        toast("Enlace copiado");
      } catch {
        toast("No se pudo copiar", true);
      }
    });
  });
  el.querySelector("[data-refresh-public-ip]")?.addEventListener("click", async (e) => {
    const card = e.target.closest(".channel-card");
    const cameraId = card?.dataset?.cameraId;
    try {
      toast("Detectando IP pública…");
      await api("/api/v1/connectivity/public-ip/refresh", { method: "POST" });
      if (cameraId && card) {
        const deviceKey = card.closest(".device-card")?.dataset?.deviceKey;
        const devices = getAllDevices();
        const device = devices.find((d) => d.key === deviceKey);
        const cam = device?.channels?.find((c) => c.camera?.camera_id === cameraId)?.camera;
        if (device && cam) await loadAccessInfo(cameraId, card, device, cam);
      }
      toast("IP pública actualizada");
    } catch (err) {
      toast(err.message, true);
    }
  });
}

function debounce(fn, ms = 350) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function setChannelBusy(card, busy, message = "Conectando…") {
  if (!card) return;
  const btn = card.querySelector("[data-toggle-bc]");
  let overlay = card.querySelector(".channel-busy");
  if (busy) {
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "channel-busy";
      overlay.innerHTML = '<span class="spinner" aria-hidden="true"></span><span class="busy-msg"></span>';
      card.appendChild(overlay);
    }
    overlay.querySelector(".busy-msg").textContent = message;
    card.classList.add("is-busy");
    card.querySelectorAll("input, select, button").forEach((el) => {
      if (el === btn) return;
      el.disabled = true;
    });
    if (btn) {
      if (!btn.dataset.prevText) btn.dataset.prevText = btn.textContent;
      btn.disabled = true;
      btn.textContent = message;
      btn.classList.add("loading");
    }
  } else {
    card.classList.remove("is-busy");
    overlay?.remove();
    card.querySelectorAll("input, select, button").forEach((el) => {
      if (el.hasAttribute("data-del-ch")) return;
      el.disabled = false;
    });
    const playbackBtn = card.querySelector("[data-playback-test]");
    if (playbackBtn && card.dataset.broadcastOn !== "1") playbackBtn.disabled = true;
    if (btn) {
      btn.classList.remove("loading");
      btn.disabled = false;
      if (btn.dataset.prevText) {
        btn.textContent = btn.dataset.prevText;
        delete btn.dataset.prevText;
      }
    }
  }
}

async function loadAccessInfo(cameraId, card, device, cam) {
  const el = card?.querySelector(".conn-hints");
  if (!el) return;
  const scrollY = window.scrollY;
  const mode =
    card.querySelector(`input[name="bc-mode-${cameraId}"]:checked`)?.value || "rtsp";
  const useOverride = card.querySelector("[data-bc-override]")?.checked;
  el.innerHTML = '<p class="hint conn-loading">Actualizando datos de conexión…</p>';
  try {
    let info;
    if (useOverride && device && cam) {
      const ov = readBroadcastOverride(device, cam, card);
      info = await api(`/api/v1/cameras/${cameraId}/access-info/preview`, {
        method: "POST",
        json: {
          host: ov.host,
          port: ov.port,
          username: ov.username,
          password: ov.password ?? "",
          brand: ov.brand,
          channel: ov.channel,
        },
      });
    } else {
      info = await api(`/api/v1/cameras/${cameraId}/access-info`);
    }
    renderConnectionHints(el, info, mode);
    requestAnimationFrame(() => window.scrollTo(0, scrollY));
  } catch (err) {
    el.innerHTML = `<p class="hint err-text">${escapeHtml(err.message)}</p>`;
    requestAnimationFrame(() => window.scrollTo(0, scrollY));
  }
}

function bindOverrideAccessInfoRefresh(card, device, cam, cameraId) {
  const refresh = debounce(() => loadAccessInfo(cameraId, card, device, cam));
  const overrideCb = card.querySelector("[data-bc-override]");
  overrideCb?.addEventListener("change", (e) => {
    card.querySelector(".override-fields")?.classList.toggle("hidden", !e.target.checked);
    loadAccessInfo(cameraId, card, device, cam);
  });
  card.querySelectorAll(".ov-host, .ov-port, .ov-user, .ov-pass, .ov-channel").forEach((inp) => {
    inp.addEventListener("input", () => {
      if (overrideCb?.checked) refresh();
    });
  });
  card.querySelector(".ov-brand")?.addEventListener("change", () => {
    if (overrideCb?.checked) refresh();
  });
}

function readBroadcastOverride(device, cam, card) {
  if (!card.querySelector("[data-bc-override]")?.checked) {
    return {
      host: device.host,
      port: device.port,
      username: device.username,
      password: device.password,
      brand: device.brand,
      channel: cam?.source?.channel || device.probeChannel,
    };
  }
  return {
    host: card.querySelector(".ov-host")?.value?.trim() || device.host,
    port: parseInt(card.querySelector(".ov-port")?.value, 10) || device.port || 554,
    username: card.querySelector(".ov-user")?.value || device.username,
    password: card.querySelector(".ov-pass")?.value ?? device.password,
    brand: card.querySelector(".ov-brand")?.value || device.brand,
    channel: card.querySelector(".ov-channel")?.value?.trim() || cam?.source?.channel,
  };
}

function renderChannelPanel(device, chState, root) {
  const ch = chState.channel;
  let cam = chState.camera;
  if (!cam?.camera_id) {
    cam = findCameraByHostChannel(device.host, ch);
    if (cam) chState.camera = cam;
  }
  if (!cam?.camera_id) {
    root.innerHTML = `<p class="hint err-text">Canal ${escapeHtml(ch)} sin datos en el servidor. Vuelve a guardarlo con «+ Añadir canal».</p>`;
    return;
  }
  const cameraId = cam.camera_id;
  const mode = device.broadcastMode || "rtsp";
  const src = cam.source || {};

  const card = document.createElement("div");
  card.className = "channel-card";
  card.dataset.cameraId = cameraId;
  card.dataset.channelKey = ch;

  card.innerHTML = `
    <header>
      <strong>Canal ${escapeHtml(ch)}</strong>
      <span class="ch-status badge muted">—</span>
    </header>
    <p class="section-label">Broadcast (búfer 60 s)</p>
    <div class="mode-row">
      <label class="mode-opt"><input type="radio" name="bc-mode-${cameraId}" value="rtsp" ${mode === "rtsp" ? "checked" : ""}/> RTSP rebroadcast</label>
      <label class="mode-opt"><input type="radio" name="bc-mode-${cameraId}" value="webrtc" ${mode === "webrtc" ? "checked" : ""}/> WebRTC</label>
    </div>
    <label class="check-row">
      <input type="checkbox" data-bc-override="" />
      <span data-bc-override-label="">Otros datos RTSP al activar broadcast</span>
    </label>
    <div class="override-fields hidden">
      <div class="field-grid">
        <div class="span2"><label>IP</label><input class="ov-host" value="${escapeHtml(device.host || "")}" /></div>
        <div><label>Puerto</label><input class="ov-port" type="number" value="${device.port || 554}" /></div>
        <div><label>Canal</label><input class="ov-channel" value="${escapeHtml(src.channel || ch)}" /></div>
        <div><label>Usuario</label><input class="ov-user" value="${escapeHtml(device.username || "")}" /></div>
        <div><label>Contraseña</label><input class="ov-pass" type="text" value="${escapeHtml(device.password || "")}" /></div>
        <div class="span2"><label>Marca</label><select class="ov-brand">${brandOptionsHtml(device.brand)}</select></div>
      </div>
    </div>
    <button type="button" class="btn-block btn-toggle-bc off" data-toggle-bc="">Activar broadcast</button>
    <pre class="action-msg hidden" data-bc-msg="" aria-live="polite"></pre>
    <p class="buf-hint hint">El búfer solo se rellena con broadcast activo.</p>
    <button type="button" class="btn-block secondary" data-playback-test="" disabled>Probar playback (−${PLAYBACK_OFFSET_SEC} s)</button>
    <div class="playback-preview hidden">
      <p class="hint">Frame del búfer (~${PLAYBACK_OFFSET_SEC} s atrás):</p>
      <img alt="Playback" />
    </div>
    <video class="channel-video hidden" playsinline muted autoplay></video>
    <div class="conn-hints"></div>
    <button type="button" class="btn-sm danger" data-del-ch="">Eliminar este canal</button>
  `;

  bindOverrideAccessInfoRefresh(card, device, cam, cameraId);
  card.querySelectorAll(`input[name="bc-mode-${cameraId}"]`).forEach((r) => {
    r.addEventListener("change", () => {
      device.broadcastMode = r.value;
      updateBroadcastModeUi(card, r.value);
      loadAccessInfo(cameraId, card, device, cam);
    });
  });
  updateBroadcastModeUi(card, mode);
  card.querySelector("[data-toggle-bc]")?.addEventListener("click", () =>
    toggleBroadcast(device, cam, card)
  );
  card.querySelector("[data-playback-test]")?.addEventListener("click", () =>
    testPlaybackBuffer(cameraId, card)
  );
  card.querySelector("[data-del-ch]")?.addEventListener("click", () => deleteCamera(cameraId));
  refreshChannelStatus(cameraId, card, device, cam);
  loadAccessInfo(cameraId, card, device, cam);
  root.appendChild(card);
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
        <input class="dev-probe-ch" value="${device.probeChannel || "101"}" placeholder="101" inputmode="numeric" />
      </div>
      <div>
        <label>Usuario</label>
        <input class="dev-user" value="${device.username || ""}" autocomplete="username" />
      </div>
      <div class="span2">
        <label>Contraseña</label>
        <input class="dev-pass" type="text" value="${escapeHtml(device.password || "")}" autocomplete="off" />
        <p class="hint">Visible tras guardar (red local de instalación).</p>
      </div>
    </div>
    <p class="section-label">1 · Probar cámara</p>
    <div class="probe-box">
      <div class="probe-preview">
        <p class="probe-placeholder">Prueba la URL RTSP antes de guardar</p>
      </div>
      <div class="probe-codec-row hidden" data-probe-codec-row="">
        <span class="probe-codec-badge" data-probe-codec-badge=""></span>
        <span class="probe-codec-rec hint" data-probe-codec-rec=""></span>
      </div>
      <button type="button" class="btn-block secondary" data-probe="">Probar conexión</button>
      <pre class="action-msg hidden" data-probe-msg="" aria-live="polite"></pre>
      <p class="probe-net hint">La prueba RTSP la hace el <strong>servidor Kanvis</strong> (no el navegador). Debe poder abrir <code>host:puerto</code> desde ese equipo.</p>
    </div>
    <p class="section-label">2 · Guardar</p>
    <button type="button" class="btn-block primary" data-save-device="">Guardar cámara</button>
    <p class="save-hint hint">Guarda la IP y el canal de prueba (${device.probeChannel || "101"}).</p>
    <p class="section-label">3 · Canales guardados</p>
    <nav class="channel-sub-nav"></nav>
    <div class="channel-detail-root"></div>
    <div class="add-channel-row">
      <input class="add-ch-input" placeholder="ej. stream2" />
      <button type="button" class="btn-sm secondary" data-add-ch-save="">+ Añadir canal</button>
    </div>
  `;

  const bind = (sel, fn) => {
    const el = panel.querySelector(sel);
    if (el) el.addEventListener("input", fn);
    if (el) el.addEventListener("change", fn);
  };

  const persistFields = () => upsertDeviceDraft(device);

  bind(".dev-host", (e) => {
    device.host = e.target.value;
    updateDeviceNavLabel(device);
    persistFields();
  });
  bind(".dev-port", (e) => {
    device.port = e.target.value;
    persistFields();
  });
  bind(".dev-user", (e) => {
    device.username = e.target.value;
    persistFields();
  });
  bind(".dev-pass", (e) => {
    device.password = e.target.value;
    persistFields();
  });
  bind(".dev-probe-ch", (e) => {
    device.probeChannel = e.target.value.trim() || "101";
    persistFields();
  });
  panel.querySelector(".dev-brand")?.addEventListener("change", (e) => {
    device.brand = e.target.value;
    const defs = defaultChannelsForBrand(device.brand);
    device.probeChannel = defs[0]?.channel || "101";
    if (!device.channels.some((c) => c.saved)) {
      device.channels = defs.map((c) => ({
        channel: c.channel,
        label: c.label,
        saved: false,
        camera: null,
      }));
    }
    upsertDeviceDraft(device);
    renderDevices();
  });

  panel.querySelector("[data-probe]")?.addEventListener("click", async () => {
    const msg = panel.querySelector("[data-probe-msg]");
    const preview = panel.querySelector(".probe-preview");
    syncDeviceCredsFromDom(device);
    if (!device.host?.trim()) {
      return setActionMsg(msg, "Indica la IP", "err");
    }
    setActionMsg(msg, "Analizando códec y capturando frame…");
    panel.querySelector("[data-probe-codec-row]")?.classList.add("hidden");
    try {
      const probe = await probeDevice(device, device.probeChannel);
      preview.innerHTML = `<img src="${probe.objectUrl}" alt="Vista previa" />`;
      device.probeCodec = probe.codec || "";
      device.probeBroadcastRec = probe.recommendation || "";
      if (probe.recommendation === "webrtc" || probe.recommendation === "rtsp") {
        device.broadcastMode = probe.recommendation;
      }
      showProbeCodecBadge(panel, probe);
      const codecLine = probe.codec
        ? `Códec: ${probe.codec}${probe.resolution ? ` (${probe.resolution})` : ""}.`
        : probe.metaError
          ? `Códec no detectado: ${probe.metaError}`
          : "Códec no detectado.";
      const recLine = probe.hint || "";
      setActionMsg(
        msg,
        `RTSP OK (canal ${device.probeChannel || "—"}). ${codecLine} ${recLine} Guarda la cámara cuando quieras.`,
        probe.codec ? "ok" : "err"
      );
      const oldKey = device.key;
      device.key = hostKey(device.host);
      if (oldKey !== device.key) {
        const idx = draftDevices.findIndex((d) => d.key === oldKey);
        if (idx >= 0) draftDevices[idx].key = device.key;
        activeDeviceKey = device.key;
      }
    } catch (err) {
      preview.innerHTML = '<p class="probe-placeholder">Sin imagen</p>';
      setActionMsg(msg, err.message, "err");
    }
  });

  panel.querySelector("[data-save-device]")?.addEventListener("click", () =>
    saveCameraDevice(device, panel)
  );

  panel.querySelector("[data-add-ch-save]")?.addEventListener("click", async () => {
    const ch = panel.querySelector(".add-ch-input")?.value?.trim();
    if (!ch) return toast("Indica el canal (ej. stream2)", true);
    try {
      await persistCamera(device, ch, `${device.host} ch${ch}`);
      toast(`Canal ${ch} guardado`);
      await loadCameras();
      syncDeviceChannelsFromCache(device);
      upsertDeviceDraft(device);
      device.activeChannel = ch;
      activeDeviceKey = device.key;
      renderDevices();
    } catch (err) {
      toast(err.message, true);
    }
  });

  panel.querySelector("[data-remove-device]")?.addEventListener("click", () => {
    draftDevices = draftDevices.filter((d) => d.key !== device.key);
    if (activeDeviceKey === device.key) activeDeviceKey = null;
    renderDevices();
  });

  ensureActiveChannel(device);
  const saved = getSavedChannels(device);
  const subNav = panel.querySelector(".channel-sub-nav");
  const detailRoot = panel.querySelector(".channel-detail-root");
  subNav.innerHTML = "";
  detailRoot.innerHTML = "";
  if (!saved.length) {
    detailRoot.innerHTML =
      '<p class="hint">Guarda la cámara arriba. Luego añade más canales (stream1, stream2…).</p>';
  } else {
    for (const chState of saved) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `device-tab${chState.channel === device.activeChannel ? " active" : ""}`;
      btn.textContent = `Canal ${chState.channel}`;
      btn.addEventListener("click", () => {
        device.activeChannel = chState.channel;
        renderDevices();
      });
      subNav.appendChild(btn);
    }
    const active = saved.find((c) => c.channel === device.activeChannel) || saved[0];
    renderChannelPanel(device, active, detailRoot);
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
  const existing = findCameraByHostChannel(device.host, channel);
  const cameraId = existing?.camera_id ?? makeCameraId(device.host, channel);
  const payload = buildCameraPayload(device, channel, label, { cameraId });
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
    draftDevices = draftDevices.filter((d) => d.key !== device.key);
    device.key = hostKey(device.host);
    device.fromApi = true;
    device.probeOk = true;
    await loadCameras();
    syncDeviceChannelsFromCache(device);
    upsertDeviceDraft(device);
    activeDeviceKey = device.key;
    device.activeChannel = channel;
    toast(`Cámara guardada (canal ${channel}). Activa broadcast en el canal.`);
  } catch (err) {
    toast(err.message, true);
  }
}

/** Broadcast activado en el edge (ingesta solicitada), aunque RTSP aún falle. */
function isBroadcastActive(st) {
  return !!(
    st.broadcast_ingest_active ||
    st.relay?.running ||
    st.webrtc?.session?.connection_state === "connected"
  );
}

function hasWorkingIngest(st) {
  return !!(st.ingest?.connected && (st.buffer_span_seconds || 0) >= 0.35);
}

function stopChannelStatusPoll(cameraId) {
  const id = channelStatusPollers.get(cameraId);
  if (id != null) {
    clearInterval(id);
    channelStatusPollers.delete(cameraId);
  }
}

function startChannelStatusPoll(cameraId, card, device, cam) {
  stopChannelStatusPoll(cameraId);
  const tick = () => {
    if (card.dataset.broadcastOn !== "1") {
      stopChannelStatusPoll(cameraId);
      return;
    }
    refreshChannelStatus(cameraId, card, device, cam, { light: true });
  };
  channelStatusPollers.set(cameraId, setInterval(tick, 2500));
  tick();
}

async function refreshChannelStatus(cameraId, card, device, cam, opts = {}) {
  const badge = card.querySelector(".ch-status");
  const toggle = card.querySelector("[data-toggle-bc]");
  const playbackBtn = card.querySelector("[data-playback-test]");
  const bufHint = card.querySelector(".buf-hint");
  try {
    const st = await api(`/api/v1/cameras/${cameraId}/status`);
    const ing = hasWorkingIngest(st);
    const relay = st.relay?.running;
    const rtc = st.webrtc?.session?.connection_state === "connected";
    const span = st.buffer_span_seconds || 0;
    const maxDur = st.buffer_max_duration_seconds || 60;
    const pkt = st.buffer_packets || 0;
    const pktMax = st.buffer_packets_max || 0;
    const bcOn = isBroadcastActive(st);
    const nearCap =
      bcOn && ing && (span >= maxDur * 0.92 || (pktMax > 0 && pkt >= pktMax * 0.95));

    let html = ing
      ? '<span class="badge ok">Ingesta OK</span>'
      : st.broadcast_ingest_active
        ? '<span class="badge warn">Sin ingesta RTSP</span>'
        : '<span class="badge muted">Broadcast off</span>';
    if (relay) html += ' <span class="badge ok">Relay RTSP</span>';
    if (rtc) {
      html += ing
        ? ' <span class="badge ok">WebRTC</span>'
        : ' <span class="badge warn">WebRTC (sin vídeo)</span>';
    }
    html += ` <span class="badge muted">Búfer ${span.toFixed(1)}s</span>`;
    badge.innerHTML = html;

    if (toggle) {
      toggle.textContent = bcOn ? "Desactivar broadcast" : "Activar broadcast";
      toggle.classList.toggle("on", bcOn);
      toggle.classList.toggle("off", !bcOn);
    }
    if (bufHint) {
      if (!bcOn) {
        bufHint.textContent = "El búfer solo se rellena con broadcast activo.";
      } else if (!ing) {
        bufHint.textContent =
          st.ingest_hint ||
          "Broadcast activo pero sin vídeo: revisa IP, canal (stream1/stream2), marca y credenciales.";
      } else if (nearCap) {
        bufHint.textContent = `Búfer lleno: ${span.toFixed(1)}s / ${maxDur}s (${pkt} paquetes) — listo para playback`;
      } else {
        bufHint.textContent = `Búfer: ${span.toFixed(1)}s / ${maxDur}s — ingesta en marcha`;
      }
    }
    if (playbackBtn) {
      playbackBtn.disabled = !ing || span < PLAYBACK_OFFSET_SEC * 0.85;
    }
    card.dataset.broadcastOn = bcOn ? "1" : "0";
    if (!opts.light && device && cam) loadAccessInfo(cameraId, card, device, cam);
    if (!bcOn) stopChannelStatusPoll(cameraId);
  } catch {
    badge.innerHTML = '<span class="badge muted">Sin guardar / inactiva</span>';
    if (toggle) {
      toggle.textContent = "Activar broadcast";
      toggle.classList.add("off");
    }
    if (playbackBtn) playbackBtn.disabled = true;
  }
}

function syncBroadcastModeRadios(card, cameraId, mode) {
  if (!card) return;
  const rtsp = card.querySelector(`input[name="bc-mode-${cameraId}"][value="rtsp"]`);
  const webrtc = card.querySelector(`input[name="bc-mode-${cameraId}"][value="webrtc"]`);
  if (rtsp) rtsp.checked = mode === "rtsp";
  if (webrtc) webrtc.checked = mode === "webrtc";
  updateBroadcastModeUi(card, mode);
}

async function waitForIngest(cameraId, maxMs = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    const st = await api(`/api/v1/cameras/${cameraId}/status`);
    if (hasWorkingIngest(st)) return st;
    await new Promise((r) => setTimeout(r, 500));
  }
  let hint = "";
  try {
    const st = await api(`/api/v1/cameras/${cameraId}/status`);
    hint = st.ingest_hint || st.ingest?.last_error || "";
  } catch (_) {}
  throw new Error(
    hint ||
      "La ingesta RTSP no recibe vídeo; revisa IP, canal (stream1/stream2), marca y credenciales"
  );
}

async function toggleBroadcast(device, cam, card) {
  const bcMsg = card.querySelector("[data-bc-msg]");
  const cameraId =
    cam?.camera_id ||
    resolveCameraId(device.host, cam?.source?.channel || card.dataset.channelKey);
  if (!camerasCache.some((c) => c.camera_id === cameraId)) {
    const m = `Cámara no encontrada (${cameraId}). Guarda el canal con «+ Añadir canal».`;
    setActionMsg(bcMsg, m, "err");
    throw new Error(m);
  }
  if (cam && !cam.camera_id) cam.camera_id = cameraId;
  const mode =
    card.querySelector(`input[name="bc-mode-${cameraId}"]:checked`)?.value ||
    device.broadcastMode ||
    "rtsp";
  device.broadcastMode = mode;
  const isOn = card.dataset.broadcastOn === "1";

  setChannelBusy(card, true, isOn ? "Desactivando…" : "Activando broadcast…");
  setActionMsg(bcMsg, isOn ? "Desactivando broadcast…" : "Activando broadcast…");
  try {
    if (!isOn) {
      const ov = readBroadcastOverride(device, cam, card);
      const pwd = resolveRtspPassword(device, cam, card) || ov.password || "";
      if (!pwd) {
        const m =
          "Falta la contraseña RTSP. Rellénala arriba, pulsa «Guardar cámara», o márcala en «Otros datos RTSP».";
        setActionMsg(bcMsg, m, "err");
        throw new Error(m);
      }
      ov.password = pwd;
      const payload = buildCameraPayload(
        {
          host: ov.host,
          port: ov.port,
          username: ov.username,
          password: pwd,
          brand: ov.brand,
          broadcastMode: mode,
        },
        ov.channel,
        cam.label,
        { broadcastOn: true, broadcastMode: mode }
      );
      payload.camera_id = cameraId;
      const savedCam = await api(`/api/v1/cameras/${cameraId}`, {
        method: "PUT",
        json: payload,
      });
      const cacheIdx = camerasCache.findIndex((c) => c.camera_id === cameraId);
      if (cacheIdx >= 0) camerasCache[cacheIdx] = savedCam;
      else camerasCache.push(savedCam);
      const chEntry = device.channels.find(
        (c) => c.camera?.camera_id === cameraId || c.channel === cam.source?.channel
      );
      if (chEntry) chEntry.camera = savedCam;
      device.broadcastMode = mode;
      upsertDeviceDraft({ ...device, broadcastMode: mode });

      const url =
        mode === "webrtc"
          ? `/api/v1/cameras/${cameraId}/broadcast/start?mode=webrtc`
          : `/api/v1/cameras/${cameraId}/broadcast/start`;
      setChannelBusy(card, true, "Arrancando ingesta…");
      const startRes = await api(url, { method: "POST" });
      if (!startRes?.ingest_ready) {
        const err =
          startRes?.ingest_last_error ||
          "La cámara no envía vídeo RTSP al edge (revisa «Probar conexión»).";
        throw new Error(err);
      }

      if (mode === "webrtc") {
        setChannelBusy(card, true, "Conectando WebRTC…");
        setActionMsg(bcMsg, "Conectando WebRTC…");
        const video = card.querySelector("video");
        await KanvisWebRtcViewer.connect(cameraId, api, {
          video,
          onState: (s) => {
            if (s === "connected") setActionMsg(bcMsg, "WebRTC conectado. Rellenando búfer…", "ok");
          },
        });
        video?.classList.remove("hidden");
      }
      setActionMsg(
        bcMsg,
        `Broadcast activado (${mode}). Ingesta OK — búfer rellenándose (hasta 60 s).`,
        "ok"
      );
      card.dataset.broadcastOn = "1";
      startChannelStatusPoll(cameraId, card, device, cam);
    } else {
      stopChannelStatusPoll(cameraId);
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
      const savedCam = await api(`/api/v1/cameras/${cameraId}`, {
        method: "PUT",
        json: payload,
      });
      const cacheIdx = camerasCache.findIndex((c) => c.camera_id === cameraId);
      if (cacheIdx >= 0) camerasCache[cacheIdx] = savedCam;
      device.broadcastMode = mode;
      upsertDeviceDraft({ ...device, broadcastMode: mode });
      setActionMsg(bcMsg, "Broadcast desactivado.", "ok");
    }
    syncBroadcastModeRadios(card, cameraId, mode);
    await refreshChannelStatus(cameraId, card, device, cam);
    await loadAccessInfo(cameraId, card, device, cam);
  } catch (err) {
    setActionMsg(bcMsg, err.message, "err");
  } finally {
    setChannelBusy(card, false);
  }
}

async function testPlaybackBuffer(cameraId, card) {
  const box = card.querySelector(".playback-preview");
  const img = box?.querySelector("img");
  const bcMsg = card.querySelector("[data-bc-msg]");
  setActionMsg(bcMsg, `Capturando frame de hace ${PLAYBACK_OFFSET_SEC} s…`);
  try {
    const blob = await api(
      `/api/v1/cameras/${cameraId}/snapshot/buffer?offset_sec=${PLAYBACK_OFFSET_SEC}`
    );
    if (img) {
      img.src = URL.createObjectURL(blob);
      box.classList.remove("hidden");
    }
    setActionMsg(
      bcMsg,
      `Playback OK: frame de hace ~${PLAYBACK_OFFSET_SEC} s. Compara la hora en la imagen con la actual.`,
      "ok"
    );
  } catch (err) {
    setActionMsg(bcMsg, err.message, "err");
  }
}

async function deleteCamera(id) {
  const cam = camerasCache.find((c) => c.camera_id === id);
  const chLabel = cam?.source?.channel || id;
  if (!confirm(`¿Eliminar el canal ${chLabel}?`)) return;
  try {
    if (KanvisWebRtcViewer.getActiveCameraId() === id) {
      await KanvisWebRtcViewer.disconnect(api, id);
    }
    await api(`/api/v1/cameras/${id}`, { method: "DELETE" });
    camerasCache = camerasCache.filter((c) => c.camera_id !== id);
    const hk = cam ? hostKey(cam.source?.host || "") : null;
    for (const d of draftDevices) {
      if (hk && hostKey(d.host) !== hk) continue;
      d.channels = (d.channels || []).filter((c) => c.camera?.camera_id !== id);
      if (d.activeChannel === chLabel) {
        const rest = getSavedChannels(d);
        d.activeChannel = rest[0]?.channel ?? null;
      }
    }
    toast(`Canal ${chLabel} eliminado`);
    await loadCameras();
    renderDevices();
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
