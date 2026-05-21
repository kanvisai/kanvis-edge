/**
 * Visor WebRTC (WHEP) — preview en navegador con métricas.
 */
(function (global) {
  const STUN_DEFAULT = [{ urls: "stun:stun.l.google.com:19302" }];

  let pc = null;
  let statsTimer = null;
  let statusTimer = null;
  let displayFpsRaf = null;
  let lastInboundFrames = 0;
  let lastInboundTs = 0;
  let lastIngestPackets = 0;
  let lastIngestTs = 0;

  function $(id) {
    return document.getElementById(id);
  }

  function setStat(id, value) {
    const el = $(id);
    if (el) el.textContent = value;
  }

  function stopDisplayFps() {
    if (displayFpsRaf && $("webrtc-video")) {
      $("webrtc-video").cancelVideoFrameCallback?.(displayFpsRaf);
    }
    displayFpsRaf = null;
  }

  function startDisplayFps(video) {
    stopDisplayFps();
    let frames = 0;
    let windowStart = performance.now();
    const tick = (now) => {
      frames += 1;
      if (now - windowStart >= 1000) {
        setStat("stat-display-fps", String(frames));
        frames = 0;
        windowStart = now;
      }
      if (pc && video.srcObject) {
        displayFpsRaf = video.requestVideoFrameCallback(tick);
      }
    };
    if (video.requestVideoFrameCallback) {
      displayFpsRaf = video.requestVideoFrameCallback(tick);
    }
  }

  async function waitIceGathering(peer, timeoutMs = 8000) {
    if (peer.iceGatheringState === "complete") return;
    await new Promise((resolve) => {
      const t = setTimeout(resolve, timeoutMs);
      peer.addEventListener("icegatheringstatechange", () => {
        if (peer.iceGatheringState === "complete") {
          clearTimeout(t);
          resolve();
        }
      });
    });
  }

  async function pollIngestAndWebRTC(cameraId, apiFn) {
    try {
      const [camStatus, rtcStatus] = await Promise.all([
        apiFn(`/api/v1/cameras/${cameraId}/status`),
        apiFn(`/api/v1/webrtc/${cameraId}/status`).catch(() => null),
      ]);
      const ing = camStatus.ingest || {};
      const now = Date.now();
      if (lastIngestTs > 0 && ing.packets_total != null) {
        const dt = (now - lastIngestTs) / 1000;
        if (dt > 0) {
          const pps = (ing.packets_total - lastIngestPackets) / dt;
          setStat("stat-ingest-pps", pps.toFixed(1));
        }
      }
      lastIngestPackets = ing.packets_total ?? 0;
      lastIngestTs = now;

      setStat(
        "stat-ingest",
        ing.connected ? "Conectada" : "Sin vídeo"
      );
      setStat("stat-buffer", `${camStatus.buffer_span_seconds ?? 0}s / ${camStatus.buffer_max_duration_seconds ?? 60}s`);

      if (rtcStatus) {
        setStat("stat-rtc-mode", rtcStatus.mode || "—");
        setStat("stat-rewind-pending", String(rtcStatus.rewind_packets_pending ?? 0));
      }
    } catch (_) {
      /* cámara inactiva */
    }
  }

  async function updateRtcStats() {
    if (!pc) return;
    const reports = await pc.getStats();
    let inbound = null;
    let trackReport = null;
    reports.forEach((r) => {
      if (r.type === "inbound-rtp" && r.kind === "video") inbound = r;
      if (r.type === "track" && r.kind === "video") trackReport = r;
    });
    if (inbound) {
      const frames = inbound.framesDecoded ?? inbound.framesReceived ?? 0;
      const now = performance.now();
      if (lastInboundTs > 0) {
        const dt = (now - lastInboundTs) / 1000;
        if (dt > 0) {
          const fps = (frames - lastInboundFrames) / dt;
          setStat("stat-rtc-fps", fps.toFixed(1));
        }
      }
      lastInboundFrames = frames;
      lastInboundTs = now;
      setStat("stat-frames-decoded", String(frames));
      setStat("stat-bytes-rx", formatBytes(inbound.bytesReceived || 0));
      if (inbound.jitter != null) {
        setStat("stat-jitter", `${(inbound.jitter * 1000).toFixed(1)} ms`);
      }
      if (inbound.framesDropped != null) {
        setStat("stat-dropped", String(inbound.framesDropped));
      }
    }
    if (trackReport) {
      setStat(
        "stat-resolution",
        trackReport.frameWidth && trackReport.frameHeight
          ? `${trackReport.frameWidth}×${trackReport.frameHeight}`
          : "—"
      );
    }
    setStat("stat-ice", pc.iceConnectionState || "—");
    setStat("stat-connection", pc.connectionState || "—");
  }

  function formatBytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(2)} MB`;
  }

  function clearStats() {
    [
      "stat-display-fps",
      "stat-rtc-fps",
      "stat-resolution",
      "stat-frames-decoded",
      "stat-bytes-rx",
      "stat-jitter",
      "stat-dropped",
      "stat-ice",
      "stat-connection",
      "stat-ingest",
      "stat-ingest-pps",
      "stat-buffer",
      "stat-rtc-mode",
      "stat-rewind-pending",
    ].forEach((id) => setStat(id, "—"));
    lastInboundFrames = 0;
    lastInboundTs = 0;
    lastIngestPackets = 0;
    lastIngestTs = 0;
  }

  async function connect(cameraId, apiFn, stunUrls) {
    if (!cameraId) throw new Error("Indica el ID de la cámara");
    await disconnect(apiFn);

    const video = $("webrtc-video");
    const iceServers = (stunUrls && stunUrls.length ? stunUrls : STUN_DEFAULT).map((u) =>
      typeof u === "string" ? { urls: u } : u
    );

    pc = new RTCPeerConnection({ iceServers });
    pc.addTransceiver("video", { direction: "recvonly" });

    pc.ontrack = (ev) => {
      const stream = ev.streams[0] || new MediaStream([ev.track]);
      video.srcObject = stream;
      video.play().catch(() => {});
      startDisplayFps(video);
    };

    pc.onconnectionstatechange = () => {
      setStat("stat-connection", pc.connectionState);
      $("webrtc-live-badge")?.classList.toggle("ok", pc.connectionState === "connected");
    };
    pc.oniceconnectionstatechange = () => setStat("stat-ice", pc.iceConnectionState);

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await waitIceGathering(pc);

    const answer = await apiFn(`/api/v1/webrtc/${cameraId}/offer`, {
      method: "POST",
      json: {
        sdp: pc.localDescription.sdp,
        type: pc.localDescription.type,
      },
    });

    await pc.setRemoteDescription({ type: answer.type, sdp: answer.sdp });

    statsTimer = setInterval(() => updateRtcStats(), 1000);
    statusTimer = setInterval(() => pollIngestAndWebRTC(cameraId, apiFn), 2000);
    await pollIngestAndWebRTC(cameraId, apiFn);
    await updateRtcStats();

    $("webrtc-viewer-panel")?.classList.remove("hidden");
    $("btn-webrtc-connect")?.setAttribute("disabled", "true");
    $("btn-webrtc-disconnect")?.removeAttribute("disabled");
    $("btn-webrtc-rewind")?.removeAttribute("disabled");
  }

  async function disconnect(apiFn) {
    stopDisplayFps();
    if (statsTimer) clearInterval(statsTimer);
    if (statusTimer) clearInterval(statusTimer);
    statsTimer = null;
    statusTimer = null;

    const cameraId = $("test-camera-id")?.value?.trim();
    if (cameraId && apiFn) {
      try {
        await apiFn(`/api/v1/webrtc/${cameraId}`, { method: "DELETE" });
      } catch (_) {}
    }

    if (pc) {
      pc.close();
      pc = null;
    }
    const video = $("webrtc-video");
    if (video) {
      video.srcObject = null;
    }
    $("webrtc-live-badge")?.classList.remove("ok");
    $("btn-webrtc-connect")?.removeAttribute("disabled");
    $("btn-webrtc-disconnect")?.setAttribute("disabled", "true");
    $("btn-webrtc-rewind")?.setAttribute("disabled", "true");
    clearStats();
  }

  async function rewind(cameraId, offsetSec, apiFn) {
    return apiFn(`/api/v1/webrtc/${cameraId}/rewind?offset_sec=${offsetSec}`, {
      method: "POST",
    });
  }

  global.KanvisWebRtcViewer = {
    connect,
    disconnect,
    rewind,
    isConnected: () => pc != null && pc.connectionState !== "closed",
  };
})(window);
