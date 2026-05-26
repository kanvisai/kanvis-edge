"""Decodificación de paquetes H.264/HEVC del búfer RTSP (Annex-B / AVCC)."""

from __future__ import annotations

import logging
from fractions import Fraction

import av

from src.ingestion.buffer import RawPacket

logger = logging.getLogger(__name__)

_ANNEX_PREFIXES = (b"\x00\x00\x01", b"\x00\x00\x00\x01")


def is_annex_b(data: bytes) -> bool:
    return any(data.startswith(p) for p in _ANNEX_PREFIXES)


def iter_annex_b_nals(data: bytes):
    """Genera NAL units completas (incl. cabecera de tipo) en stream Annex-B."""
    if not data:
        return
    i = 0
    n = len(data)
    while i < n:
        sc_len = 0
        if i + 4 <= n and data[i : i + 4] == b"\x00\x00\x00\x01":
            sc_len = 4
        elif i + 3 <= n and data[i : i + 3] == b"\x00\x00\x01":
            sc_len = 3
        else:
            i += 1
            continue
        start = i + sc_len
        j = start
        while j < n - 2:
            if data[j : j + 4] == b"\x00\x00\x00\x01" or data[j : j + 3] == b"\x00\x00\x01":
                break
            j += 1
        else:
            j = n
        nal = data[start:j]
        if nal:
            yield nal
        i = j


def avcc_to_annex_b(data: bytes) -> bytes:
    """Convierte paquetes con prefijo de longitud (MP4/AVCC) a Annex-B."""
    out = bytearray()
    i = 0
    n = len(data)
    while i + 4 <= n:
        length = int.from_bytes(data[i : i + 4], "big")
        i += 4
        if length <= 0 or i + length > n:
            break
        out.extend(b"\x00\x00\x00\x01")
        out.extend(data[i : i + length])
        i += length
    return bytes(out) if out else data


def _bsf_to_annex_b(data: bytes, filter_name: str) -> bytes | None:
    try:
        from av.bitstream import BitStreamFilter
    except ImportError:
        return None
    try:
        bsf = BitStreamFilter(filter_name)
        pkt = av.Packet(data)
        filtered = bsf.filter(pkt)
        if not filtered:
            return None
        first = filtered[0] if isinstance(filtered, list) else filtered
        return bytes(first)
    except Exception:
        logger.debug("BitStreamFilter %s falló", filter_name, exc_info=True)
        return None


def to_annex_b(data: bytes, codec_name: str) -> bytes:
    if is_annex_b(data):
        return data
    name = codec_name
    if name == "h265":
        name = "hevc"
    bsf_name = "h264_mp4toannexb" if name == "h264" else "hevc_mp4toannexb" if name == "hevc" else None
    if bsf_name:
        converted = _bsf_to_annex_b(data, bsf_name)
        if converted and is_annex_b(converted):
            return converted
    converted = avcc_to_annex_b(data)
    return converted if is_annex_b(converted) else data


def h264_avcc_extradata_from_keyframe(data: bytes) -> bytes | None:
    """Construye extradata avcC a partir de SPS/PPS en un keyframe Annex-B."""
    annex = to_annex_b(data, "h264")
    sps = pps = None
    for nal in iter_annex_b_nals(annex):
        ntype = nal[0] & 0x1F
        if ntype == 7:
            sps = nal
        elif ntype == 8:
            pps = nal
    if not sps or not pps or len(sps) < 4:
        return None
    return b"".join(
        [
            b"\x01",
            bytes([sps[1], sps[2], sps[3]]),
            b"\xff",
            b"\xe1",
            len(sps).to_bytes(2, "big"),
            sps,
            b"\x01",
            len(pps).to_bytes(2, "big"),
            pps,
        ]
    )


def trim_packets_from_keyframe(packets: list[RawPacket]) -> list[RawPacket]:
    for i, pkt in enumerate(packets):
        if pkt.is_keyframe:
            return packets[i:]
    if not packets:
        return []
    for i, pkt in enumerate(packets):
        annex = to_annex_b(pkt.data, "h264")
        for nal in iter_annex_b_nals(annex):
            if nal and (nal[0] & 0x1F) == 5:
                return packets[i:]
    return []


def open_decoder(codec_name: str, extradata: bytes | None = None) -> av.codec.context.CodecContext | None:
    name = codec_name
    if name == "h265":
        name = "hevc"
    try:
        dec = av.CodecContext.create(name, "r")
    except av.AVError:
        return None
    if extradata and name in ("h264", "hevc"):
        try:
            dec.extradata = extradata
        except av.AVError:
            logger.debug("extradata no aceptada para %s", name)
    return dec


def build_av_packet(raw: RawPacket, codec_name: str) -> av.Packet:
    payload = to_annex_b(raw.data, codec_name)
    pkt = av.Packet(payload)
    if raw.pts is not None:
        pkt.pts = raw.pts
        pkt.dts = raw.dts
        pkt.time_base = Fraction(raw.time_base_num, raw.time_base_den)
    return pkt


def decode_packet(
    raw: RawPacket,
    decoder: av.codec.context.CodecContext | None,
    active_codec: str | None,
    codec_order: list[str],
    stream_extradata: bytes | None,
) -> tuple[list[av.VideoFrame], av.codec.context.CodecContext | None, str | None]:
    """
    Decodifica un paquete. Devuelve (frames, decoder, active_codec).
    Reinicia el decodificador en keyframes si hace falta extradata nueva.
    """
    codecs = list(codec_order)
    if active_codec and active_codec in codecs:
        codecs = [active_codec] + [c for c in codecs if c != active_codec]

    extradata = stream_extradata
    if raw.is_keyframe and not extradata:
        built = h264_avcc_extradata_from_keyframe(raw.data)
        if built:
            extradata = built

    if raw.is_keyframe:
        decoder = None
        active_codec = None

    for codec_name in codecs:
        if decoder is None or active_codec != codec_name:
            decoder = open_decoder(codec_name, extradata)
            if decoder is None:
                continue
            active_codec = codec_name
            if raw.is_keyframe and not extradata and codec_name == "h264":
                built = h264_avcc_extradata_from_keyframe(raw.data)
                if built:
                    try:
                        decoder.extradata = built
                    except av.AVError:
                        pass
        try:
            frames = list(decoder.decode(build_av_packet(raw, codec_name)))
            if frames:
                return frames, decoder, active_codec
        except av.AVError:
            decoder = None
            active_codec = None
            continue
        except Exception:
            logger.debug("decode falló (%s)", codec_name, exc_info=True)
            decoder = None
            active_codec = None

    return [], decoder, active_codec
