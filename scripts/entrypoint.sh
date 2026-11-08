#!/bin/bash
# =====================================================================
# entrypoint.sh — تهيئة وإقلاع المحطة التلفزية على nginx (بدون ffmpeg)
# 1) يجهّز متغيرات إعادة البث  2) يولّد خريطة البث الحي
# 3) يكتب config.js لصفحة المشغل  4) يبني nginx.conf  5) يشغّل كل شيء
# =====================================================================
set -u

# ---------- قيم افتراضية ----------
export PORT="${PORT:-8000}"
export CHANNEL_NAME="${CHANNEL_NAME:-قناتي HD}"
export MEDIA_DIR="${MEDIA_DIR:-/media}"
export MAPS_DIR="${MAPS_DIR:-/var/vod/maps}"
export CACHE_DIR="${CACHE_DIR:-/var/vod/cache}"
export CONFIG_JS="${CONFIG_JS:-/var/vod/config.js}"
export WWW_DIR="${WWW_DIR:-/var/www}"
export SEGMENT_DURATION="${SEGMENT_DURATION:-10000}"
export LIVE_WINDOW="${LIVE_WINDOW:-60000}"
export SCHEDULE_INTERVAL="${SCHEDULE_INTERVAL:-15}"
# ملاحظة: الوحدة تطلب الخريطة عبر /map/ + <مسار الطلب بدون اسم الملف>،
# لذا يجب أن يكون الملف تحت MAPS_DIR/live/main ليطابق الرابط العام /live/main/index.m3u8
export SCHEDULE_OUT="${SCHEDULE_OUT:-${MAPS_DIR}/live/main}"
SCHEDULE_EPOCH_MS="${SCHEDULE_EPOCH_MS:-1767225600000}"
export SCHEDULE_EPOCH_MS
ERROR_LOG="${ERROR_LOG:-/var/log/nginx/error.log}"
export ERROR_LOG

mkdir -p "$MAPS_DIR" "$CACHE_DIR" "$(dirname "$ERROR_LOG")"

# ---------- محلل DNS (مطلوب لـ proxy_pass بمتغيرات) ----------
DNS_RESOLVER="$(awk '/^nameserver/ {print $2; exit}' /etc/resolv.conf 2>/dev/null)"
if [ -z "${DNS_RESOLVER}" ]; then
    DNS_RESOLVER="1.1.1.1"
fi
export DNS_RESOLVER

# ---------- تنزيل وسائط من MEDIA_URL (إن ضُبط) ----------
# بديل عملي عن التورنت: روابط مباشرة لملفات MP4/MKV (مثل GitHub Releases)
if [ -n "${MEDIA_URL:-}" ]; then
    echo "[entrypoint] تنزيل الوسائط من MEDIA_URL ..."
    python3 /opt/tv/scripts/media_fetch.py || echo "[entrypoint] تحذير: تعذّر تنزيل بعض الوسائط"
fi

# ---------- توليد مواقع القنوات الخارجية (Restream 1..8 — HLS/TS) ----------
python3 /opt/tv/scripts/gen_restream.py || echo "[entrypoint] تحذير: تعذّر توليد مواقع القنوات الخارجية"
# ضمان وجود ملف الـ include حتى لو فشل التوليد (وإلا لن يقوم nginx بالإقلاع)
[ -s /var/vod/restream_locations.conf ] || printf '# لا قنوات خارجية\n' > /var/vod/restream_locations.conf

# ---------- توليد خريطة البث الحي (أول مرة قبل تشغيل nginx) ----------
echo "[entrypoint] توليد خريطة البث الحي من ${MEDIA_DIR} ..."
if ! python3 /opt/tv/scripts/build_schedule.py; then
    echo "[entrypoint] تحذير: تعذّر توليد الخريطة — قناة الملفات ستكون غير متاحة"
fi

# ---------- ملف إعدادات مشغل الويب ----------
MAIN_ENABLED=0
[ -s "$SCHEDULE_OUT" ] && MAIN_ENABLED=1
export MAIN_ENABLED
CONFIG_TMP="${CONFIG_JS}.tmp"
python3 - "$CONFIG_TMP" <<PYEOF
import json, os, sys
sys.path.insert(0, "/opt/tv/scripts")
from restream_env import iter_channels

channels = []
if os.environ.get("MAIN_ENABLED") == "1":
    channels.append({
        "name": os.environ.get("CHANNEL_NAME", "قناتي HD"),
        "url": "/live/main/master.m3u8",
        "type": "hls",
    })
for ch in iter_channels():
    channels.append({"name": ch["name"], "url": ch["link"], "type": ch["type"]})

cfg = {
    "channelName": os.environ.get("CHANNEL_NAME", "قناتي HD"),
    "channels": channels,
    "segmentDuration": int(os.environ.get("SEGMENT_DURATION", "10000")) / 1000.0,
}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write("window.TV_CONFIG = " + json.dumps(cfg, ensure_ascii=False) + ";\n")
PYEOF
if [ -f "$CONFIG_TMP" ]; then
    mv "$CONFIG_TMP" "$CONFIG_JS"
else
    echo 'window.TV_CONFIG = {"channelName":"قناتي HD","channels":[],"segmentDuration":10};' > "$CONFIG_JS"
fi

# ---------- بناء nginx.conf من القالب ----------
envsubst '${PORT} ${MAPS_DIR} ${WWW_DIR} ${CONFIG_JS} ${SEGMENT_DURATION} ${LIVE_WINDOW} ${DNS_RESOLVER} ${ERROR_LOG}' \
    < /opt/tv/nginx.conf.template > /opt/nginx/conf/nginx.conf

EXTERNAL_COUNT=$(python3 -c 'import sys; sys.path.insert(0, "/opt/tv/scripts"); from restream_env import iter_channels; print(len(iter_channels()))' 2>/dev/null || echo 0)

echo "[entrypoint] nginx جاهز على المنفذ ${PORT} — القناة الرئيسية: ${MAIN_ENABLED} — قنوات خارجية: ${EXTERNAL_COUNT}"

# ---------- حلقة تجديد خريطة البث في الخلفية ----------
(
    while true; do
        sleep "$SCHEDULE_INTERVAL"
        python3 /opt/tv/scripts/build_schedule.py >> /var/vod/scheduler.log 2>&1 || true
        # تقليم سجل المجدول حتى لا يتضخم
        if [ -f /var/vod/scheduler.log ] && [ "$(wc -c < /var/vod/scheduler.log)" -gt 1000000 ]; then
            tail -n 200 /var/vod/scheduler.log > /var/vod/scheduler.log.new
            mv /var/vod/scheduler.log.new /var/vod/scheduler.log
        fi
    done
) &

# ---------- تشغيل nginx في المقدمة ----------
exec /opt/nginx/sbin/nginx -g 'daemon off;'
