#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
restream_env.py — تحليل روابط القنوات الخارجية (HLS أو TS).

يُستخدم كمكتبة:
    from restream_env import parse
    info = parse(url, force_type)      # info["enabled"], info["origin"], ...

أو كسكربت من سطر الأوامر (للتوافق القديم): يطبع متغيرات shell للقناة الأولى:
    eval "$(python3 restream_env.py)"

المنطق:
- الرابط المنتهي بـ .ts/.mts/.m2ts (أو RESTREAM_TYPE=ts) يُعامل كبث TS متواصل.
- غير ذلك يُعامل كقائمة HLS (m3u8).
- الرابط العام على محطتنا: /restream/<N>/... للـ HLS و /restream/<N>/stream.ts للـ TS.
"""

import os
import shlex
from urllib.parse import urlsplit

SUPPORTED_TYPES = ("hls", "ts")


def detect_type(path, force_type="auto"):
    rtype = (force_type or "auto").strip().lower()
    if rtype in SUPPORTED_TYPES:
        return rtype
    ext = (path or "").lower().rsplit(".", 1)[-1]
    return "ts" if ext in ("ts", "mts", "m2ts") else "hls"


def parse(url, force_type="auto", channel=1):
    """يحلل رابط قناة خارجية ويعيد dict بمعلومات الوكيل لقناة رقم channel."""
    url = (url or "").strip()
    if not url:
        return {
            "enabled": 0, "channel": channel, "type": "hls",
            "origin": "", "host": "", "full_url": "", "link": "",
        }

    sp = urlsplit(url)
    scheme = sp.scheme or "https"
    netloc = sp.netloc
    path = sp.path or "/"
    query = ("?" + sp.query) if sp.query else ""

    rtype = detect_type(path, force_type)
    if rtype == "hls":
        link = "/restream/%d%s%s" % (channel, path, query)
    else:
        link = "/restream/%d/stream.ts" % channel

    return {
        "enabled": 1,
        "channel": channel,
        "type": rtype,
        "origin": "%s://%s" % (scheme, netloc),
        "host": netloc,
        "full_url": url,
        "link": link,
    }


def env_channel(i):
    """أسماء متغيرات القناة رقم i (1..8)."""
    if i == 1:
        return "RESTREAM_URL", "RESTREAM_NAME", "RESTREAM_TYPE"
    return "RESTREAM_URL_%d" % i, "RESTREAM_NAME_%d" % i, "RESTREAM_TYPE_%d" % i


def iter_channels(max_channels=8):
    """يمر على القنوات المعرفة في البيئة ويعيد قائمة dicts مرتبة."""
    out = []
    for i in range(1, max_channels + 1):
        url_key, name_key, type_key = env_channel(i)
        url = (os.environ.get(url_key) or "").strip()
        if not url:
            continue
        info = parse(url, os.environ.get(type_key) or "auto", channel=i)
        info["name"] = (os.environ.get(name_key) or "").strip() or "قناة خارجية %d" % i
        out.append(info)
    return out


if __name__ == "__main__":
    # توافق قديم: طباعة متغيرات القناة الأولى (يستخدمها run_local_test.sh)
    ch = parse(os.environ.get("RESTREAM_URL"), os.environ.get("RESTREAM_TYPE"), 1)
    values = {
        "RESTREAM_ORIGIN": ch["origin"] or "http://127.0.0.1:1",
        "RESTREAM_HOST": ch["host"] or "localhost",
        "RESTREAM_TYPE": ch["type"],
        "RESTREAM_FULL_URL": ch["full_url"] or "http://127.0.0.1:1/stream.ts",
        "RESTREAM_LINK": ch["link"],
        "RESTREAM_ENABLED": str(ch["enabled"]),
    }
    for key, val in values.items():
        print("export %s=%s" % (key, shlex.quote(val)))
