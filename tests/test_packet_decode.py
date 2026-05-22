"""Pruebas de utilidades de decodificación de paquetes."""

from src.ingestion.packet_decode import (
    avcc_to_annex_b,
    h264_avcc_extradata_from_keyframe,
    is_annex_b,
    trim_packets_from_keyframe,
)
from src.ingestion.buffer import RawPacket


def test_is_annex_b():
    assert is_annex_b(b"\x00\x00\x00\x01\x67\x42")
    assert not is_annex_b(b"\x00\x00\x00\x05\x67")


def test_avcc_to_annex_b():
    nal = b"\x67\x42\x00\x1f\xe9\x00"
    length = len(nal).to_bytes(4, "big")
    converted = avcc_to_annex_b(length + nal)
    assert is_annex_b(converted)
    assert b"\x67" in converted


def test_extradata_from_keyframe():
    sps = b"\x67\x42\x00\x1f\xe9\x00\x00\x03\x00\x01"
    pps = b"\x68\xce\x38\x80"
    annex = b"\x00\x00\x00\x01" + sps + b"\x00\x00\x00\x01" + pps + b"\x00\x00\x00\x01\x65\xff"
    extra = h264_avcc_extradata_from_keyframe(annex)
    assert extra is not None
    assert extra[0] == 1


def test_trim_from_keyframe():
    packets = [
        RawPacket(data=b"p", pts=0, dts=0, is_keyframe=False),
        RawPacket(data=b"k", pts=1, dts=1, is_keyframe=True),
        RawPacket(data=b"p2", pts=2, dts=2, is_keyframe=False),
    ]
    trimmed = trim_packets_from_keyframe(packets)
    assert len(trimmed) == 2
    assert trimmed[0].is_keyframe
