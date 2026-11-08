#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mp4_info.py — قراءة مدة الملف والترميزات مباشرة من الملف (بدون ffmpeg نهائياً).

يعيد dict بالشكل:
    {
        "duration_ms": <int>,          # أقصر مدة بين المسارات
        "video": "avc1" | None,        # ترميز الفيديو الأساسي (avc1 = H.264)
        "audio": "aac" | None,         # ترميز الصوت الأساسي (مُوحَّد: aac/mp3/ac3/...)
    }

يدعم:
- MP4/MOV/M4V: صندوق moov في أي مكان بالملف، أحجام 64-بت، نسختي mvhd/mdhd.
- MKV (Matroska): محلل EBML مصغّر يقرأ Info (Duration/TimecodeScale) و Tracks (CodecID).

ترميزات الفيديو المدعومة في حزم TS: H.264 فقط (avc1).
ترميزات الصوت المدعومة في TS: AAC، MP3، AC3 — أياً كان غيرها يُستبعد لاحقاً برسالة واضحة.
"""

import os
import struct

VIDEO_OK = {"avc1", "avc3"}          # H.264
VIDEO_KNOWN = VIDEO_OK | {"hev1", "hvc1", "hvc9"}  # H.265 غير مدعوم في TS
# تحويل وسوم MP4 إلى أسماء موحدة
_MP4_AUDIO_MAP = {
    "mp4a": "aac", "mp3 ": "mp3", ".mp3": "mp3", "ac-3": "ac3",
    "ec-3": "ec3", "opus": "opus", "fLaC": "flac",
}
# معرفات CodecID في MKV (مطابقة بالبادئة)
_MKV_VIDEO_MAP = {
    "V_MPEG4/ISO/AVC": "avc1",       # H.264
    "V_MPEGH/ISO/HEVC": "hvc1",      # H.265 — غير مدعوم في TS
}
_MKV_AUDIO_MAP = {
    "A_AAC": "aac", "A_AC3": "ac3", "A_MPEG/L3": "mp3",
}

# =====================================================================
#  MP4 / MOV / M4V
# =====================================================================


def _iter_boxes(f, start, end):
    """يمرّ على الصندوق المباشرة بين start و end في ملف مفتوح."""
    off = start
    while off + 8 <= end:
        f.seek(off)
        hdr = f.read(16)
        if len(hdr) < 8:
            break
        size, btype = struct.unpack_from(">I4s", hdr, 0)
        hlen = 8
        if size == 1:
            if len(hdr) < 16:
                break
            size = struct.unpack_from(">Q", hdr, 8)[0]
            hlen = 16
        elif size == 0:
            size = end - off
        if size < hlen or off + size > end:
            break
        yield btype, off + hlen, off + size
        off += size


def _find_box(f, start, end, wanted):
    for btype, bs, be in _iter_boxes(f, start, end):
        if btype == wanted:
            return bs, be
    return None


def _parse_fullbox_header(f, box_start):
    """يقرأ version+flags (4 بايت) من بداية محتوى الصندوق ويعيد الإزاحة بعد الترويسة."""
    f.seek(box_start)
    ver = f.read(1)[0]
    return ver, box_start + 4


def _read_mdhd(f, s, e):
    """يرجع (timescale, duration) من صندوق mdhd."""
    if s is None:
        return None
    ver, p = _parse_fullbox_header(f, s)
    if ver == 1:
        f.seek(p + 16)  # creation+modification (8+8)
        ts, dur = struct.unpack(">IQ", f.read(12))
    else:
        f.seek(p + 8)   # creation+modification (4+4)
        ts, dur = struct.unpack(">II", f.read(8))
    return ts, dur


def _probe_mp4(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        moov = _find_box(f, 0, size, b"moov")
        if moov is None:
            raise ValueError("لا يوجد صندوق moov")

        mvhd = _find_box(f, moov[0], moov[1], b"mvhd")
        mv = _read_mdhd(f, mvhd[0], mvhd[1]) if mvhd else None

        durations = []
        if mv and mv[0] and mv[1]:
            durations.append(float(mv[1]) / mv[0])

        video = None
        audio = None

        # المرور على كل المسارات
        pos = moov
        while True:
            trak = _find_box(f, pos[0], pos[1], b"trak")
            if trak is None:
                break
            mdia = _find_box(f, trak[0], trak[1], b"mdia")
            if mdia:
                mdhd = _find_box(f, mdia[0], mdia[1], b"mdhd")
                md = _read_mdhd(f, mdhd[0], mdhd[1]) if mdhd else None
                if md and md[0] and md[1]:
                    durations.append(float(md[1]) / md[0])
                minf = _find_box(f, mdia[0], mdia[1], b"minf")
                if minf:
                    stbl = _find_box(f, minf[0], minf[1], b"stbl")
                    if stbl:
                        stsd = _find_box(f, stbl[0], stbl[1], b"stsd")
                        if stsd:
                            # fullbox: version+flags (4) + entry_count (4)
                            f.seek(stsd[0] + 8)
                            raw = f.read(8)
                            if len(raw) == 8:
                                entry_size, fmt = struct.unpack(">I4s", raw)
                                if 8 <= entry_size <= stsd[1] - stsd[0]:
                                    tag = fmt.decode("latin-1")
                                    if tag in VIDEO_OK:
                                        video = "avc1"
                                    elif tag in VIDEO_KNOWN:
                                        video = video or tag
                                    elif tag in _MP4_AUDIO_MAP and audio is None:
                                        audio = _MP4_AUDIO_MAP[tag]
            pos = (trak[1], moov[1])

        if not durations:
            raise ValueError("لا توجد مدة صالحة")

        return {
            "duration_ms": int(min(durations) * 1000),
            "video": video,
            "audio": audio,
        }


# =====================================================================
#  MKV (Matroska) — محلل EBML مصغّر
# =====================================================================

EBML_SEGMENT = 0x18538067
EBML_INFO = 0x1549A966
EBML_TIMECODESCALE = 0x2AD7B1
EBML_DURATION = 0x4489
EBML_TRACKS = 0x1654AE6B
EBML_TRACKENTRY = 0xAE
EBML_TRACKTYPE = 0x83
EBML_CODECID = 0x86
EBML_CLUSTER = 0x1F43B675


def _read_vint_id(f):
    """يقرأ معرّف عنصر EBML ويعيد (القيمة، عدد البايتات) أو None عند نهاية الملف."""
    b = f.read(1)
    if not b:
        return None
    first = b[0]
    if first == 0:
        raise ValueError("معرّف EBML غير صالح")
    length = 1
    mask = 0x80
    while not (first & mask):
        mask >>= 1
        length += 1
        if length > 4:
            raise ValueError("معرّف EBML طويل جداً")
    val = first
    for _ in range(length - 1):
        nb = f.read(1)
        if not nb:
            raise ValueError("ملف مقطوع")
        val = (val << 8) | nb[0]
    return val, length


def _read_vint_size(f):
    """يقرأ حجم عنصر EBML ويعيد (القيمة، حجم_غير_معروف؟، عدد البايتات)."""
    b = f.read(1)
    if not b:
        return None
    first = b[0]
    length = 1
    mask = 0x80
    while not (first & mask):
        mask >>= 1
        length += 1
        if length > 8:
            raise ValueError("حجم EBML طويل جداً")
    val = first & (mask - 1)          # إزالة بت العلامة
    for _ in range(length - 1):
        nb = f.read(1)
        if not nb:
            raise ValueError("ملف مقطوع")
        val = (val << 8) | nb[0]
    unknown = val == (1 << (7 * length)) - 1   # كل البتات = 1
    return val, unknown, length


def _iter_ebml(f, start, end):
    """يمرّ على عناصر EBML المباشرة بين start و end. يتوقف عند حجم غير معروف."""
    off = start
    while off < end:
        f.seek(off)
        try:
            rid = _read_vint_id(f)
            if rid is None:
                return
            vs = _read_vint_size(f)
            if vs is None:
                return
        except ValueError:
            return
        size, unknown, szlen = vs
        hdr = rid[1] + szlen
        data_start = off + hdr
        if unknown:
            # حجم غير معروف: لا يمكن القفز بأمان — نسلّم ما تبقى ونتوقف
            yield rid[0], data_start, end, True
            return
        if data_start + size > end:
            size = end - data_start
            if size <= 0:
                return
        yield rid[0], data_start, data_start + size, False
        off = data_start + size


def _read_uint(f, s, e):
    f.seek(s)
    raw = f.read(min(e - s, 8))
    return int.from_bytes(raw, "big") if raw else 0


def _probe_mkv(path):
    size = os.path.getsize(path)
    duration = None
    timescale = 1000000  # القيمة الافتراضية 1ms
    video = None
    audio = None

    with open(path, "rb") as f:
        segment = None
        for eid, s, e, unk in _iter_ebml(f, 0, size):
            if eid == EBML_SEGMENT:
                segment = (s, e)
                break
        if segment is None:
            raise ValueError("لا يوجد عنصر Segment (ليس ملف MKV؟)")

        for eid, s, e, unk in _iter_ebml(f, segment[0], segment[1]):
            if eid == EBML_INFO:
                for cid, cs, ce, _ in _iter_ebml(f, s, e):
                    if cid == EBML_TIMECODESCALE:
                        timescale = _read_uint(f, cs, ce) or 1000000
                    elif cid == EBML_DURATION:
                        f.seek(cs)
                        raw = f.read(ce - cs)
                        if len(raw) == 4:
                            duration = struct.unpack(">f", raw)[0]
                        elif len(raw) == 8:
                            duration = struct.unpack(">d", raw)[0]
            elif eid == EBML_TRACKS:
                for tid, ts_, te, _ in _iter_ebml(f, s, e):
                    if tid != EBML_TRACKENTRY:
                        continue
                    ttype = None
                    codec = None
                    for cid, cs, ce, _ in _iter_ebml(f, ts_, te):
                        if cid == EBML_TRACKTYPE:
                            ttype = _read_uint(f, cs, ce)
                        elif cid == EBML_CODECID:
                            f.seek(cs)
                            raw = f.read(min(ce - cs, 64))
                            codec = raw.decode("latin-1").rstrip("\x00 ")
                    if ttype == 1 and codec and video is None:
                        for prefix, norm in _MKV_VIDEO_MAP.items():
                            if codec.startswith(prefix):
                                video = norm
                                break
                        else:
                            video = codec       # ترميز غير معروف — سيُستبعد لاحقاً برسالة
                    elif ttype == 2 and codec and audio is None:
                        for prefix, norm in _MKV_AUDIO_MAP.items():
                            if codec.startswith(prefix):
                                audio = norm
                                break
                        else:
                            audio = codec       # مثل A_DTS / A_TRUEHD — سيُستبعد لاحقاً
            elif eid == EBML_CLUSTER:
                # المقاطع الفعلية — لا حاجة لدخولها (المعلومات قبلها في الملفات السليمة)
                continue
            if duration is not None and video is not None:
                break

    if duration is None:
        raise ValueError("لا توجد مدة في MKV (تسجيل مباشر بلا حقل Duration؟)")
    duration_ms = int(duration * timescale / 1e6)
    return {"duration_ms": duration_ms, "video": video, "audio": audio}


# =====================================================================
#  الواجهة العامة
# =====================================================================


def probe(path):
    """يحلل ملف MP4/MOV/M4V/MKV ويعيد المدة والترميزات، أو يرفع استثناء عند الفشل."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mkv":
        return _probe_mkv(path)
    return _probe_mp4(path)


if __name__ == "__main__":
    import sys

    for p in sys.argv[1:]:
        try:
            print(p, probe(p))
        except Exception as exc:  # noqa: BLE001
            print(p, "ERROR:", exc)
