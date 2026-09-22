"""Serves the Fruit Fly TV page and answers two small JSON endpoints:

  /api/live?handle=@channel    -> is the channel live right now (and which video)?
  /api/videos?handle=@channel  -> the channel's uploads, for binge-watching while it is offline

The browser cannot read youtube.com pages itself (cross-origin), so this little server does it.
Run:  python serve.py   then open http://localhost:8765/fruit-fly-tv.html
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "fruit-fly-tv.html")
LIVE_CACHE_SECONDS = 30
VIDEOS_CACHE_SECONDS = 10 * 60
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_cache = {}  # (kind, handle) -> (timestamp, result)


def get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.8",
        "Cookie": "CONSENT=YES+cb; SOCS=CAI",   # skips the EU consent interstitial
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def norm(handle):
    handle = handle.strip()
    return handle if handle.startswith("@") else "@" + handle


def fetch_live_status(handle):
    handle = norm(handle)
    page = get(f"https://www.youtube.com/{urllib.parse.quote(handle)}/live")

    m = re.search(r'<link rel="canonical" href="([^"]+)"', page)
    canonical = m.group(1) if m else ""
    vm = re.search(r"[?&]v=([\w-]{11})", canonical)
    video_id = vm.group(1) if vm else None

    live_now = bool(re.search(r'"videoDetails":\{.*?"isLiveNow":true', page, re.S)) if video_id else False
    sm = re.search(r'"scheduledStartTime":"(\d+)"', page)
    tm = re.search(r'<meta name="title" content="([^"]*)"', page)
    cm = re.search(r'"externalId":"(UC[\w-]{22})"', page)

    return {
        "handle": handle,
        "channelId": cm.group(1) if cm else None,
        "live": bool(video_id and live_now),
        "upcoming": bool(video_id and not live_now),
        "videoId": video_id,
        "title": html.unescape(tm.group(1)) if tm else "",
        "scheduledStart": int(sm.group(1)) if (sm and video_id and not live_now) else None,
        "checkedAt": int(time.time()),
    }


def fetch_videos(handle):
    """Uploads from the channel's /videos tab (ids, newest first) plus titles from its RSS feed."""
    handle = norm(handle)
    page = get(f"https://www.youtube.com/{urllib.parse.quote(handle)}/videos")
    cm = re.search(r'"externalId":"(UC[\w-]{22})"', page)
    channel_id = cm.group(1) if cm else None

    ids = []
    for m in re.finditer(r'"videoId":"([\w-]{11})"', page):
        if m.group(1) not in ids:
            ids.append(m.group(1))

    titles = {}
    if channel_id:
        try:
            feed = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
            for e in re.finditer(r"<yt:videoId>([\w-]{11})</yt:videoId>\s*<yt:channelId>[^<]*</yt:channelId>\s*<title>([^<]*)</title>", feed):
                titles[e.group(1)] = html.unescape(e.group(2))
                if e.group(1) not in ids:
                    ids.append(e.group(1))
        except Exception:
            pass

    return {
        "handle": handle,
        "channelId": channel_id,
        "videos": [{"id": i, "title": titles.get(i, "")} for i in ids],
        "checkedAt": int(time.time()),
    }


# ---------------------------------------------------------------- connectome data (via Virtual Fly Brain)
# Neuron skeletons and the template brain surface come from virtualflybrain.org, which hosts the FlyWire
# (FAFB) whole-brain connectome neurons registered to the JRC2018 unisex template. Files are cached on disk.
VFB = "https://www.virtualflybrain.org/data/VFB/i"
TEMPLATE = "VFB_00101567"          # JRC2018Unisex adult brain template
THIN_VERSION = 3                   # bump when thin_swc changes so cached thinned copies are rebuilt
CACHE_DIR = os.environ.get("CACHE_DIR") or os.path.join(HERE, "cache")


def cached_file(name, url):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name)
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        with open(path, "wb") as f:
            f.write(data)
    with open(path, "rb") as f:
        return f.read()


def thin_swc(text, max_nodes=3500):
    """Reduce an SWC skeleton to roughly max_nodes while keeping its branching structure.
    Root, branch points and tips are always kept; long unbranched runs keep every k-th node."""
    nodes, order = {}, []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        p = line.split()
        if len(p) < 7:
            continue
        nid, parent = int(p[0]), int(p[6])
        nodes[nid] = (p[2], p[3], p[4], parent)
        order.append(nid)
    n = len(order)
    if n <= max_nodes:
        return "\n".join(f"{i} 0 {x} {y} {z} 1 {pr}" for i, (x, y, z, pr) in nodes.items())
    step = -(-n // max_nodes)  # ceil

    def count_children():
        c = {}
        for nid in order:
            pr = nodes[nid][3]
            if pr != -1:
                c[pr] = c.get(pr, 0) + 1
        return c

    # Very branchy neurons keep too many tips/branch points, so first prune short terminal twigs
    # (shorter than ~step nodes), a few passes, until the skeleton is near the budget.
    children = count_children()
    for _ in range(4):
        if len(order) <= max_nodes * 3:
            break
        min_len = max(3, step // 3)
        drop = set()
        for nid in order:
            if children.get(nid, 0) != 0 or nid in drop:
                continue
            chain, cur = [], nid
            while cur != -1 and cur in nodes and children.get(cur, 0) <= 1 and nodes[cur][3] != -1:
                chain.append(cur)
                cur = nodes[cur][3]
                if len(chain) >= min_len:
                    break
            if len(chain) < min_len:
                drop.update(chain)
        if not drop:
            break
        for nid in drop:
            del nodes[nid]
        order = [nid for nid in order if nid not in drop]
        children = count_children()
    n = len(order)
    step = max(1, -(-n // max_nodes))
    keep = {nid for nid in order if nodes[nid][3] == -1 or children.get(nid, 0) != 1}
    run = {}  # distance (in nodes) from the last kept ancestor; SWC lists parents before children
    for nid in order:
        if nid in keep:
            run[nid] = 0
            continue
        d = run.get(nodes[nid][3], 0) + 1
        if d >= step:
            keep.add(nid)
            d = 0
        run[nid] = d
    newid = {nid: i + 1 for i, nid in enumerate(k for k in order if k in keep)}

    def anc(nid):
        cur = nodes[nid][3]
        while cur != -1 and cur in nodes and cur not in keep:
            cur = nodes[cur][3]
        return cur if (cur != -1 and cur in nodes) else -1

    out = []
    for nid in order:
        if nid not in keep:
            continue
        x, y, z, _ = nodes[nid]
        pr = anc(nid)
        out.append(f"{newid[nid]} 0 {x} {y} {z} 1 {newid[pr] if pr != -1 else -1}")
    return "\n".join(out)


def neuron_swc(vfb_id, max_nodes=3500):
    if not re.fullmatch(r"VFB_[0-9a-z]{8}", vfb_id):
        raise ValueError("bad VFB id")
    max_nodes = max(300, min(int(max_nodes), 20000))
    lite = os.path.join(CACHE_DIR, f"{vfb_id}.thin{max_nodes}.v{THIN_VERSION}.swc")   # pruned twigs + subsampled
    if os.path.exists(lite):
        with open(lite, "rb") as f:
            return f.read()
    raw = cached_file(f"{vfb_id}.swc", f"{VFB}/{vfb_id[4:8]}/{vfb_id[8:12]}/{TEMPLATE}/volume.swc")
    data = thin_swc(raw.decode("utf-8", "replace"), max_nodes).encode()
    with open(lite, "wb") as f:
        f.write(data)
    return data


def decimate_obj(text, cell):
    """Vertex-clustering decimation of a triangle OBJ: vertices in the same `cell`-micron grid box merge."""
    verts, faces = [], []
    for line in text.splitlines():
        if line.startswith("v "):
            a = line.split()
            verts.append((float(a[1]), float(a[2]), float(a[3])))
        elif line.startswith("f "):
            a = line.split()
            faces.append([int(x.split("/")[0]) for x in a[1:4]])
    cell_of, acc, vmap = {}, [], [0] * len(verts)
    for i, (x, y, z) in enumerate(verts):
        key = (int(x // cell), int(y // cell), int(z // cell))
        j = cell_of.get(key)
        if j is None:
            j = len(acc)
            cell_of[key] = j
            acc.append([0.0, 0.0, 0.0, 0])
        a = acc[j]
        a[0] += x; a[1] += y; a[2] += z; a[3] += 1
        vmap[i] = j
    out = ["v %.2f %.2f %.2f" % (a[0] / a[3], a[1] / a[3], a[2] / a[3]) for a in acc]
    seen = set()
    for f in faces:
        t = tuple(vmap[i - 1] + 1 for i in f)
        if len(set(t)) < 3:
            continue
        k = tuple(sorted(t))
        if k in seen:
            continue
        seen.add(k)
        out.append("f %d %d %d" % t)
    return "\n".join(out)


def brain_obj(lod="full"):
    full = cached_file("brain_JRC2018U.obj", f"{VFB}/0010/1567/{TEMPLATE}/volume.obj")
    cell = {"low": 5.0, "med": 2.5}.get(lod)
    if not cell:
        return full
    path = os.path.join(CACHE_DIR, f"brain_JRC2018U.{lod}.obj")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    data = decimate_obj(full.decode("utf-8", "replace"), cell).encode()
    with open(path, "wb") as f:
        f.write(data)
    return data


def log(msg):
    try:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}")
    except Exception:
        pass


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        # Only the page itself is public; the folder (server code, cache) is never listed or served.
        if parsed.path in ("/", "/index.html", "/fruit-fly-tv.html"):
            return self._file(PAGE, "text/html; charset=utf-8")
        if parsed.path == "/healthz":
            return self._text(b"ok")
        if parsed.path in ("/api/brain", "/api/neuron"):
            q = urllib.parse.parse_qs(parsed.query)
            try:
                if parsed.path == "/api/brain":
                    data = brain_obj(q.get("lod", ["full"])[0])
                else:
                    data = neuron_swc(q.get("id", [""])[0].strip(), q.get("max", ["3500"])[0])
            except Exception as exc:
                log(f"{parsed.path}: ERROR {type(exc).__name__}: {exc}")
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, 502)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "max-age=3600")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path not in ("/api/live", "/api/videos"):
            return self._json({"error": "not found"}, 404)
        handle = urllib.parse.parse_qs(parsed.query).get("handle", [""])[0].strip()
        if not handle:
            return self._json({"error": "missing handle"}, 400)

        kind = parsed.path.rsplit("/", 1)[1]
        ttl = LIVE_CACHE_SECONDS if kind == "live" else VIDEOS_CACHE_SECONDS
        key = (kind, handle.lower())
        now = time.time()
        cached = _cache.get(key)
        if cached and now - cached[0] < ttl:
            return self._json(cached[1])
        try:
            result = fetch_live_status(handle) if kind == "live" else fetch_videos(handle)
        except Exception as exc:  # network trouble, YouTube layout change, etc.
            log(f"{kind} {handle}: ERROR {type(exc).__name__}: {exc}")
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 502)
        _cache[key] = (now, result)
        self._json(result)
        if kind == "live":
            state = "LIVE" if result["live"] else ("upcoming" if result["upcoming"] else "offline")
            log(f"{handle}: {state} {result['videoId'] or ''} {result['title'][:60]}")
        else:
            log(f"{handle}: {len(result['videos'])} videos for binge-watching")

    def _json(self, obj, status=200):
        self._text(json.dumps(obj).encode(), "application/json", status, "no-store")

    def _text(self, body, ctype="text/plain; charset=utf-8", status=200, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._json({"error": "page missing"}, 500)
        self._text(body, ctype, 200, "no-cache")

    def log_message(self, fmt, *args):  # keep the console quiet: only report errors on static files
        text = " ".join(str(a) for a in args)
        if "/api/" in text or "favicon.ico" in text:
            return
        if "404" in text or "code" in fmt:
            super().log_message(fmt, *args)


if __name__ == "__main__":
    # stream titles often contain emoji; don't let the Windows console encoding crash a request
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # Port: command-line argument > PORT env var (set by Render/Railway/Fly/Docker) > 8765.
    # Host: 127.0.0.1 when run by hand on your own machine; all interfaces when a host sets PORT.
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", PORT))
    host = os.environ.get("HOST") or ("0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
    print(f"Fruit Fly TV  ->  http://localhost:{port}/   (Ctrl+C to stop)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
