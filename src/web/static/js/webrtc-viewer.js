/**
 * Visor WebRTC (WHEP) — preview en navegador.
 * options: { video: HTMLVideoElement, onState?: (state) => void }
 */
(function (global) {
  const STUN_DEFAULT = [{ urls: "stun:stun.l.google.com:19302" }];

  let pc = null;
  let activeCameraId = null;
  let activeVideo = null;
  let displayFpsRaf = null;
  let onStateCb = null;

  function stopDisplayFps() {
    if (displayFpsRaf && activeVideo) {
      activeVideo.cancelVideoFrameCallback?.(displayFpsRaf);
    }
    displayFpsRaf = null;
  }

  function startDisplayFps(video) {
    stopDisplayFps();
    if (!video?.requestVideoFrameCallback) return;
    const tick = () => {
      if (pc && video.srcObject) {
        displayFpsRaf = video.requestVideoFrameCallback(tick);
      }
    };
    displayFpsRaf = video.requestVideoFrameCallback(tick);
  }

  function notifyState() {
    if (onStateCb && pc) {
      onStateCb(pc.connectionState);
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

  async function connect(cameraId, apiFn, options = {}) {
    if (!cameraId) throw new Error("Indica el ID de la cámara");
    const video =
      options.video ||
      (options.videoId ? document.getElementById(options.videoId) : null);
    if (!video) throw new Error("Elemento de vídeo no encontrado");
    await disconnect(apiFn);

    activeCameraId = cameraId;
    activeVideo = video;
    onStateCb = options.onState || null;

    const stun = options.stunUrls;
    const iceServers = (stun && stun.length ? stun : STUN_DEFAULT).map((u) =>
      typeof u === "string" ? { urls: u } : u
    );

    pc = new RTCPeerConnection({
      iceServers,
      bundlePolicy: "max-bundle",
      rtcpMuxPolicy: "require",
    });

    pc.ontrack = (ev) => {
      const stream = ev.streams[0] || new MediaStream([ev.track]);
      video.srcObject = stream;
      video.classList.remove("hidden");
      video.playsInline = true;
      video.muted = true;
      const play = () => video.play().catch(() => {});
      play();
      video.onloadeddata = () => {
        if (onStateCb) onStateCb("video");
      };
      startDisplayFps(video);
    };

    pc.onconnectionstatechange = () => notifyState();
    pc.oniceconnectionstatechange = () => notifyState();

    const offer = await pc.createOffer({ offerToReceiveVideo: true });
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
    notifyState();
    try {
      await rewind(cameraId, options.rewindSec ?? 3, apiFn);
    } catch (_) {}
  }

  async function disconnect(apiFn, cameraId) {
    stopDisplayFps();
    const id = cameraId || activeCameraId;
    if (id && apiFn) {
      try {
        await apiFn(`/api/v1/webrtc/${id}`, { method: "DELETE" });
      } catch (_) {}
    }

    if (pc) {
      pc.close();
      pc = null;
    }
    if (activeVideo) {
      activeVideo.srcObject = null;
    }
    activeCameraId = null;
    activeVideo = null;
    onStateCb = null;
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
    getActiveCameraId: () => activeCameraId,
  };
})(window);
