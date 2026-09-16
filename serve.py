#!/usr/bin/env python3
"""V4V Public API tester — local UI + CORS-free proxy + signed-callback receiver.

A dependency-free companion for exploring the V4V Public API by hand: it serves
the single-page UI, proxies calls to the API (the API sends no CORS headers, so
a browser cannot reach it directly), and receives signed task callbacks.

    python3 serve.py                       # http://127.0.0.1:4010
    python3 serve.py --port 8080 --api https://your-host/api/v1

The API base URL is also editable in the UI, so one running instance can be
pointed at production, staging or a local build without a restart.

MIT licensed. See LICENSE.
"""
import argparse, hashlib, hmac, json, os, re, ssl, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = {"base": "https://v4v.ai/api/v1", "secret": "", "events": []}
LOCK = threading.Lock()
MAX_EVENTS = 200

# ── redaction (opt-in via --redact) ─────────────────────────────────────────
# Some deployments echo an upstream vendor name in ids, hostnames or metadata
# that should not reach whoever is testing. Every proxied response body is
# scrubbed of the configured words; requests and URL paths are un-scrubbed on
# the way out, so round-trips stay valid and submitting a masked id still works.
REDACT = {"words": [], "as": "hidden", "enabled": False}
_MAP = {}          # real token -> masked token
_RMAP = {}         # masked token -> real token
_TOKEN_RE = None


def _compile():
    global _TOKEN_RE
    if not REDACT["words"] or not REDACT["enabled"]:
        REDACT["enabled"] = False
        return
    # the word must stand alone inside an identifier ("cookbook" must not match "book")
    alt = "|".join(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(w) for w in REDACT["words"])
    # a maximal identifier/host-ish run that contains one of the words
    _TOKEN_RE = re.compile(r"[A-Za-z0-9._\-]*(?:%s)[A-Za-z0-9._\-]*" % alt, re.I)


def _mask_token(tok):
    key = tok.lower()          # differently-cased spellings share one masked form
    with LOCK:
        if key in _MAP:
            return _MAP[key]
        m = tok
        for w in REDACT["words"]:
            m = re.sub(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(w), REDACT["as"], m, flags=re.I)
        if m == tok:
            return tok
        # keep masked tokens unique so un-masking is unambiguous
        if m in _RMAP and _RMAP[m].lower() != key:
            i = 2
            while f"{m}-{i}" in _RMAP:
                i += 1
            m = f"{m}-{i}"
        _MAP[key], _RMAP[m] = m, tok
        return m


def mask(text):
    if not REDACT["enabled"] or not text or not _TOKEN_RE:
        return text
    return _TOKEN_RE.sub(lambda mo: _mask_token(mo.group(0)), text)


def unmask(text):
    if not REDACT["enabled"] or not text:
        return text
    with LOCK:
        pairs = sorted(_RMAP.items(), key=lambda kv: -len(kv[0]))
    for masked, real in pairs:
        if masked in text:
            text = text.replace(masked, real)
    return text


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "v4v-api-tester"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s  %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---------- helpers ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _static(self, path):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        if "/" in name or ".." in name:
            return self._send(404, {"error": "not found"})
        fp = os.path.join(HERE, name)
        if not os.path.isfile(fp):
            return self._send(404, {"error": "not found"})
        ctype = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    # ---------- proxy ----------
    def _proxy(self, path):
        """/__api/<rest> -> <base>/<rest>, injecting the bearer token."""
        rest = unmask(path[len("/__api"):] or "/")
        base = self.headers.get("X-V4V-Base") or STATE["base"]
        url = base.rstrip("/") + rest
        token = self.headers.get("X-V4V-Token", "")
        body = self._read_body() or None
        ctype_in = (self.headers.get("Content-Type") or "")
        if body and "json" in ctype_in.lower():
            body = unmask(body.decode("utf-8", "replace")).encode()

        req = urllib.request.Request(url, data=body, method=self.command)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        ct = self.headers.get("X-V4V-Content-Type") or self.headers.get("Content-Type")
        if body is not None and ct:
            req.add_header("Content-Type", ct)
        idem = self.headers.get("X-V4V-Idempotency-Key")
        if idem:
            req.add_header("Idempotency-Key", idem)
        req.add_header("Accept", "application/json")

        ctx = ssl.create_default_context()
        if os.environ.get("V4V_INSECURE"):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=180, context=ctx) as r:
                status, hdrs, data = r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            status, hdrs, data = e.code, dict(e.headers), e.read()
        except Exception as e:
            return self._send(599, {"error": {"code": "PROXY_ERROR", "message": str(e)}},
                              extra={"X-V4V-Elapsed": str(int((time.time() - t0) * 1000))})
        ct_out = (hdrs.get("Content-Type") or "")
        if REDACT["enabled"] and ("json" in ct_out.lower() or "text" in ct_out.lower() or not ct_out):
            data = mask(data.decode("utf-8", "replace")).encode()
        keep = {k: v for k, v in hdrs.items()
                if k.lower() in ("content-type", "retry-after", "x-request-id", "location")}
        keep["X-V4V-Elapsed"] = str(int((time.time() - t0) * 1000))
        keep["X-V4V-Status"] = str(status)
        self.send_response(200)                      # tunnel real status in a header
        self.send_header("Content-Type", keep.get("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in keep.items():
            if k.lower() != "content-type":
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    # ---------- webhook receiver ----------
    def _hook(self):
        raw = self._read_body()
        ts = self.headers.get("X-V4V-Timestamp", "")
        sig = self.headers.get("X-V4V-Signature", "")
        secret = STATE["secret"]
        verdict = "no secret set"
        if secret:
            expected = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
            given = sig[3:] if sig.startswith("v1=") else sig
            ok = hmac.compare_digest(given, expected)
            fresh = False
            try:
                fresh = abs(time.time() - float(ts)) < 300
            except ValueError:
                pass
            verdict = ("valid" if ok else "INVALID signature") + ("" if fresh else " · stale/absent timestamp")
        ev = {"at": time.time(), "path": self.path, "headers": dict(self.headers),
              "raw": mask(raw.decode("utf-8", "replace")), "verify": verdict}
        try:
            ev["json"] = json.loads(ev["raw"])
        except Exception:
            ev["json"] = None
        with LOCK:
            STATE["events"].insert(0, ev)
            del STATE["events"][MAX_EVENTS:]
        self._send(200, {"received": True})

    # ---------- media relay ----------
    def _media(self):
        """Stream a result URL so a redacted host never reaches the browser."""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        target = unmask(urllib.parse.unquote(q.get("u", [""])[0]))
        if not target.lower().startswith(("http://", "https://")):
            return self._send(400, {"error": "bad url"})
        req = urllib.request.Request(target, method="GET")
        # signed CDNs reject the default Python-urllib agent, so speak as the browser does
        req.add_header("User-Agent", self.headers.get("User-Agent")
                       or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
        req.add_header("Accept", self.headers.get("Accept") or "*/*")
        rng = self.headers.get("Range")
        if rng:
            req.add_header("Range", rng)
        ctx = ssl.create_default_context()
        if os.environ.get("V4V_INSECURE"):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            r = urllib.request.urlopen(req, timeout=120, context=ctx)
        except urllib.error.HTTPError as e:
            return self._send(e.code, {"error": {"code": "MEDIA_HTTP_" + str(e.code),
                                                 "message": e.read()[:400].decode("utf-8", "replace")}})
        except Exception as e:
            return self._send(599, {"error": {"code": "MEDIA_FETCH_FAILED", "message": str(e)}})
        with r:
            self.send_response(r.status)
            for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                if r.headers.get(k):
                    self.send_header(k, r.headers[k])
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ---------- verbs ----------
    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p == "/__events":
            with LOCK:
                return self._send(200, {"events": STATE["events"], "secret_set": bool(STATE["secret"])})
        if p == "/__config":
            return self._send(200, {"base": STATE["base"], "secret_set": bool(STATE["secret"]),
                                    "redact": REDACT["enabled"],
                                    "redact_as": REDACT["as"] if REDACT["enabled"] else ""})
        if p == "/__media":
            return self._media()
        if p.startswith("/__api"):
            return self._proxy(p)
        self._static(p)

    def do_DELETE(self):
        if self.path == "/__events":
            with LOCK:
                STATE["events"] = []
            return self._send(200, {"ok": True})
        if self.path.startswith("/__api"):
            return self._proxy(self.path)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?", 1)[0]
        if p.startswith("/__hook"):
            return self._hook()
        if p == "/__config":
            cfg = json.loads(self._read_body() or b"{}")
            if "base" in cfg:
                STATE["base"] = cfg["base"]
            if "secret" in cfg:
                STATE["secret"] = cfg["secret"]
            return self._send(200, {"base": STATE["base"], "secret_set": bool(STATE["secret"]),
                                    "redact": REDACT["enabled"],
                                    "redact_as": REDACT["as"] if REDACT["enabled"] else ""})
        if p.startswith("/__api"):
            return self._proxy(p)
        self._send(404, {"error": "not found"})

    do_PUT = do_PATCH = do_POST


def main():
    ap = argparse.ArgumentParser(description="Local tester for the V4V Public API.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 4010)),
                    help="port for the tester UI (default: 4010, env PORT)")
    ap.add_argument("--api", default=os.environ.get("V4V_API_BASE", STATE["base"]), metavar="URL",
                    help="initial API base URL; also editable in the UI (env V4V_API_BASE)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="interface to bind (default: 127.0.0.1, loopback only)")
    ap.add_argument("--redact", action="append", default=None, metavar="WORD",
                    help="hide WORD from every response body, reversibly (repeatable)")
    ap.add_argument("--redact-as", default=None, metavar="TEXT",
                    help="replacement for redacted words (default: hidden)")
    a = ap.parse_args()
    STATE["base"] = a.api
    if a.redact:
        REDACT["words"], REDACT["enabled"] = a.redact, True
    if a.redact_as:
        REDACT["as"] = a.redact_as
    _compile()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"\n  V4V API tester  →  http://{a.host}:{a.port}/")
    print(f"  proxying to     →  {a.api}")
    print(f"  callback_url    →  http://{a.host}:{a.port}/__hook")
    red = ", ".join(REDACT["words"]) + " → " + REDACT["as"] if REDACT["enabled"] else "off"
    print(f"  redaction       →  {red}   (Ctrl+C to stop)\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
