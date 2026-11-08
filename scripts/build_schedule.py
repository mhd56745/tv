#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_schedule.py — يولّد خريطة البث الحي (JSON mapping) لقناة الحلقة 24/7 بدون ffmpeg.

الفكرة (نفس خوارزمية test/playlist.php المرجعية لوحدة nginx-vod-module):
- كل الفيديوهات في media/ تُعرض بالترتيب، واحدة تلو الأخرى، في حلقة زمنية لا نهائية
  تبدأ من SCHEDULE_EPOCH_MS (نقطة زمنية ثابتة).
- عند كل تشغيل، يكتب السكربت ملف JSON يغطي النافذة الزمنية [الآن - هامش، الآن + هامش]،
  وتقرأه وحدة vod لكل طلب بث في وضع mapped.
- الحساب حتمي (deterministic) اعتماداً على ساعة الحاوية فقط، لذا الأرقام التسلسلية
  (initialClipIndex / initialSegmentIndex) تبقى متسقة عبر التجديدات وإعادات التشغيل.

المتغيرات:
    MEDIA_DIR           مجلد الفيديوهات            (افتراضي /media)
    SCHEDULE_OUT        مسار ملف الخريطة الناتج    (افتراضي /var/vod/maps/main)
    CACHE_DIR           مجلد كاش المدد             (افتراضي /var/vod/cache)
    SEGMENT_DURATION    مدة المقطع بالمللي ثانية   (افتراضي 10000)
    LIVE_WINDOW         نافذة البث بالمللي ثانية   (افتراضي 60000)
    SCHEDULE_EPOCH_MS   بداية الخط الزمني للقناة   (افتراضي 2026-01-01T00:00:00Z)
"""

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mp4_info import probe  # noqa: E402

MEDIA_DIR = os.environ.get("MEDIA_DIR", "/media")
SCHEDULE_OUT = os.environ.get("SCHEDULE_OUT", "/var/vod/maps/main")
CACHE_DIR = os.environ.get("CACHE_DIR", "/var/vod/cache")
SEGMENT_DURATION = int(os.environ.get("SEGMENT_DURATION", "10000"))
LIVE_WINDOW = int(os.environ.get("LIVE_WINDOW", "60000"))
SCHEDULE_EPOCH_MS = int(
    os.environ.get("SCHEDULE_EPOCH_MS", "1767225600000")  # 2026-01-01T00:00:00Z
)

EXTENSIONS = (".mp4", ".mov", ".m4v", ".mkv")
MAX_CLIPS = 126          # حد أقصى آمن (توثيق الوحدة: حتى 128 عنصراً في durations)
# ترميزات الصوت التي تدعمها وحدة vod في حزم MPEG-TS
AUDIO_OK = {"aac", "mp3", "ac3"}
MARGIN_BEFORE_MS = LIVE_WINDOW + 3 * SEGMENT_DURATION + 20000
MARGIN_AFTER_MS = 2 * SEGMENT_DURATION + 10000


def list_media():
    """قائمة ملفات الفيديو مرتبة أبجدياً (الترتيب = ترتيب البرامج)."""
    try:
        names = sorted(os.listdir(MEDIA_DIR))
    except FileNotFoundError:
        return []
    return [
        os.path.join(MEDIA_DIR, n)
        for n in names
        if os.path.splitext(n)[1].lower() in EXTENSIONS and not n.startswith(".")
    ]


def load_cache():
    path = os.path.join(CACHE_DIR, "durations.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def save_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, "durations.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh)
    os.replace(tmp, path)


def build_playlist():
    """يعيد [(path, duration_ms)] للملفات الصالحة فقط، مع تحذيرات للملفات المستبعدة."""
    cache = load_cache()
    changed = False
    playlist = []

    for path in list_media():
        st = os.stat(path)
        key = path
        entry = cache.get(key)
        if not entry or entry.get("size") != st.st_size or entry.get("mtime") != st.st_mtime:
            try:
                info = probe(path)
                entry = {
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "duration_ms": info["duration_ms"],
                    "video": info["video"],
                    "audio": info["audio"],
                }
                cache[key] = entry
                changed = True
            except Exception as exc:  # noqa: BLE001
                print("[schedule] تجاهل ملف غير صالح %s: %s" % (path, exc), flush=True)
                cache.pop(key, None)
                changed = True
                continue

        audio = entry.get("audio")
        video_ok = entry["video"] == "avc1"
        audio_ok = audio in (None, "") or audio in AUDIO_OK
        if video_ok and audio_ok and entry["duration_ms"] >= 500:
            playlist.append((path, entry["duration_ms"]))
        else:
            if entry["video"] != "avc1":
                reason = (
                    "ترميز الفيديو %s غير مدعوم (المطلوب H.264 — ملفات H.265/HEVC "
                    "لا يمكن إعادة تغليفها بدون ffmpeg)" % entry["video"]
                )
            elif not audio_ok:
                reason = (
                    "ترميز الصوت %s غير مدعوم في TS (المطلوب AAC أو MP3 أو AC3)" % audio
                )
            else:
                reason = "مدة قصيرة جداً"
            print("[schedule] تجاهل %s: %s" % (path, reason), flush=True)

    if changed:
        save_cache(cache)
    return playlist


def main():
    playlist = build_playlist()
    if not playlist:
        print(
            "[schedule] لا توجد ملفات صالحة في %s — القناة ستكون غير متاحة "
            "(ضع ملفات MP4 بترميز H.264/AAC ثم أعد النشر)" % MEDIA_DIR,
            flush=True,
        )
        return 0  # لا نُسقط الحاوية؛ صفحة الويب ستعرض القناة كغير متاحة

    paths = [p for p, _ in playlist]
    durs = [d for _, d in playlist]
    n = len(paths)
    cycle_duration = sum(durs)

    now_ms = int(time.time() * 1000)
    win_start = now_ms - MARGIN_BEFORE_MS
    win_end = now_ms + MARGIN_AFTER_MS

    # موقع بداية الدورة التي تلتقي مع بداية النافذة
    cycle_index = (win_start - SCHEDULE_EPOCH_MS) // cycle_duration
    if cycle_index < 0:
        cycle_index = 0

    clips = []
    out_durs = []
    first_clip_time = None
    first_global_index = None

    t = SCHEDULE_EPOCH_MS + cycle_index * cycle_duration
    gi = cycle_index * n  # الرقم العام (0-based) للمقطع الحالي منذ بداية القناة

    while t < win_end and len(clips) < MAX_CLIPS:
        idx = gi % n
        d = durs[idx]
        if t + d > win_start:  # المقطع يتقاطع مع النافذة
            if not clips:
                first_clip_time = t
                first_global_index = gi
            clips.append(paths[idx])
            out_durs.append(d)
        t += d
        gi += 1

    if not clips:
        print("[schedule] نافذة فارغة؟ لن يُكتب ملف الخريطة", flush=True)
        return 1

    # عدد المقاطع الكاملة قبل أول مقطع في النافذة (بتقسيم ذكي على الدورات الكاملة)
    full_cycles = first_global_index // n
    remainder = first_global_index % n
    segments_per_cycle = sum(int(math.ceil(d / float(SEGMENT_DURATION))) for d in durs)
    segments_before = full_cycles * segments_per_cycle
    segments_before += sum(
        int(math.ceil(durs[i] / float(SEGMENT_DURATION))) for i in range(remainder)
    )

    mapping = {
        "playlistType": "live",
        "discontinuity": True,
        "firstClipTime": first_clip_time,
        "initialClipIndex": first_global_index + 1,      # 1-based كما في المثال المرجعي
        "initialSegmentIndex": segments_before + 1,      # 1-based
        "durations": out_durs,
        "sequences": [
            {
                "clips": [{"type": "source", "path": p} for p in clips],
            }
        ],
    }

    os.makedirs(os.path.dirname(SCHEDULE_OUT), exist_ok=True)
    tmp = SCHEDULE_OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, separators=(",", ":"))
    os.replace(tmp, SCHEDULE_OUT)

    total_window = sum(out_durs)
    print(
        "[schedule] خريطة جديدة: %d مقطعاً، تغطية %.1f ث، أول مقطع عند %d، "
        "clipIndex=%d segmentIndex=%d"
        % (
            len(clips),
            total_window / 1000.0,
            first_clip_time,
            first_global_index + 1,
            segments_before + 1,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
