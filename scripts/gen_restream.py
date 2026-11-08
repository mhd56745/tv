#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_restream.py — يولّد ملف مواقع nginx للقنوات الخارجية (Restream).

يقرأ من البيئة:
    RESTREAM_URL        الرابط الأساسي (قناة 1)
    RESTREAM_NAME       اسم القناة 1 (اختياري)
    RESTREAM_TYPE       hls | ts (اختياري — اكتشاف تلقائي من الامتداد)
    RESTREAM_URL_2..8   قنوات إضافية (مع RESTREAM_NAME_2..8 و RESTREAM_TYPE_2..8)

يكتب ملف include تضمّنه nginx.conf (المسار من أول وسيط، افتراضياً
/var/vod/restream_locations.conf). لكل قناة HLS يُولد location يعيد كتابة
الروابط المطلقة داخل m3u8 عبر sub_filter، ولكل قناة TS يُولد موقع تمرير مباشر.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from restream_env import iter_channels  # noqa: E402

DEFAULT_OUT = "/var/vod/restream_locations.conf"

BLOCK_HEADER = """\
# ============================================================
#  قنوات إعادة البث — ملف مولَّد آلياً عند الإقلاع (gen_restream.py)
#  لا تعدّله يدوياً — سيُستبدل عند كل تشغيل
# ============================================================
"""

HLS_BLOCK = """\
# --- القناة {num}: {name} (HLS) — {origin} ---
location ^~ /restream/{num}/ {{
    resolver {resolver} valid=60s ipv6=off;
    set $r{num}_origin "{origin}";
    set $r{num}_host "{host}";
    rewrite ^/restream/{num}/(.*)$ /$1 break;
    proxy_pass $r{num}_origin;
    proxy_set_header Host $r{num}_host;
    proxy_set_header Accept-Encoding "";
    proxy_http_version 1.1;
    proxy_ssl_server_name on;
    proxy_connect_timeout 10s;
    proxy_read_timeout 60s;
    proxy_buffers 16 32k;

    # إعادة كتابة الروابط المطلقة داخل قوائم m3u8 لتعود عبر الوكيل
    sub_filter '{origin}/' '/restream/{num}/';
    sub_filter_types application/vnd.apple.mpegurl application/x-mpegurl;
    sub_filter_once off;

    add_header Access-Control-Allow-Origin "*" always;
    add_header Access-Control-Allow-Methods "GET, HEAD, OPTIONS" always;
    add_header Access-Control-Allow-Headers "Origin, Range, Accept, Referer" always;
}}
"""

TS_BLOCK = """\
# --- القناة {num}: {name} (TS متواصل) — {full_url} ---
location = /restream/{num}/stream.ts {{
    resolver {resolver} valid=60s ipv6=off;
    set $r{num}_full "{full_url}";
    set $r{num}_host "{host}";
    proxy_pass $r{num}_full;
    proxy_set_header Host $r{num}_host;
    proxy_set_header Accept-Encoding "";
    proxy_http_version 1.1;
    proxy_ssl_server_name on;
    proxy_buffering off;
    proxy_connect_timeout 10s;
    proxy_read_timeout 300s;
    add_header Access-Control-Allow-Origin "*" always;
}}
"""


def get_resolver():
    val = (os.environ.get("DNS_RESOLVER") or "").strip()
    if val:
        return val
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    return parts[1]
    except OSError:
        pass
    return "1.1.1.1"


def build_conf(channels, resolver):
    if not channels:
        return BLOCK_HEADER + "# لا توجد قنوات خارجية مضبوطة (RESTREAM_URL فارغ)\n"
    parts = [BLOCK_HEADER]
    for ch in channels:
        common = {"num": ch["channel"], "name": ch["name"], "resolver": resolver}
        if ch["type"] == "hls":
            parts.append(HLS_BLOCK.format(origin=ch["origin"], host=ch["host"], **common))
        else:
            parts.append(TS_BLOCK.format(
                full_url=ch["full_url"], host=ch["host"], **common))
    return "\n".join(parts)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    resolver = get_resolver()
    channels = iter_channels()
    content = build_conf(channels, resolver)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, out_path)

    print("[restream] %d قناة خارجية -> %s" % (len(channels), out_path))
    for ch in channels:
        print("[restream]   قناة %d «%s» نوع=%s رابط=%s" % (
            ch["channel"], ch["name"], ch["type"], ch["link"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
