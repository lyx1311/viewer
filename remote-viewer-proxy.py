#!/usr/bin/env python3
import http.client
import os
import shutil
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 18082
LISTEN_PORTS = (18080, 18081)
REDIRECT_TARGET = "/s/xguO"
ADMIN_TARGET = "/session?viewer_admin=1"
LOGO_PATH = "/opt/viewer-proxy/viewer-logo.png"
ICON_PATHS = {
    "/favicon.ico",
    "/static/img/favicon.ico",
    "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png",
}
ADMIN_NAV_PATCH = b"""<script>
(function () {
  function isPlainSession(url) {
    try {
      var parsed = new URL(String(url), window.location.origin);
      return parsed.origin === window.location.origin &&
        parsed.pathname === "/session" &&
        parsed.searchParams.get("viewer_admin") !== "1";
    } catch (_) {
      return false;
    }
  }
  function toAdmin(url) {
    if (!isPlainSession(url)) return url;
    return "/admin";
  }
  var rawPush = history.pushState;
  var rawReplace = history.replaceState;
  history.pushState = function (state, title, url) {
    if (arguments.length >= 3) url = toAdmin(url);
    return rawPush.call(this, state, title, url);
  };
  history.replaceState = function (state, title, url) {
    if (arguments.length >= 3) url = toAdmin(url);
    return rawReplace.call(this, state, title, url);
  };
  document.addEventListener("click", function (event) {
    var link = event.target && event.target.closest && event.target.closest("a[href]");
    if (!link || !isPlainSession(link.getAttribute("href"))) return;
    event.preventDefault();
    window.location.assign("/admin");
  }, true);
  if (isPlainSession(window.location.href)) {
    window.location.replace("/admin");
  }
})();
</script>
"""

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
}


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "viewer-proxy/1.4"

    def do_GET(self):
        if self.should_redirect_to_share():
            self.redirect_to_share()
            return
        if self.should_redirect_to_admin():
            self.redirect_to_admin()
            return
        if self.should_serve_icon():
            self.serve_icon()
            return
        self.proxy()

    def do_HEAD(self):
        if self.should_redirect_to_share():
            self.redirect_to_share()
            return
        if self.should_redirect_to_admin():
            self.redirect_to_admin()
            return
        if self.should_serve_icon():
            self.serve_icon()
            return
        self.proxy()

    def do_POST(self): self.proxy()
    def do_PUT(self): self.proxy()
    def do_DELETE(self): self.proxy()
    def do_PATCH(self): self.proxy()
    def do_OPTIONS(self): self.proxy()
    def do_PROPFIND(self): self.proxy()
    def do_PROPPATCH(self): self.proxy()
    def do_MKCOL(self): self.proxy()
    def do_MOVE(self): self.proxy()
    def do_COPY(self): self.proxy()
    def do_LOCK(self): self.proxy()
    def do_UNLOCK(self): self.proxy()

    def redirect_to_share(self):
        self.send_response(302)
        self.send_header("X-Viewer-Proxy", self.server_version)
        self.send_header("Location", REDIRECT_TARGET)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def redirect_to_admin(self):
        self.send_response(302)
        self.send_header("X-Viewer-Proxy", self.server_version)
        self.send_header("Location", ADMIN_TARGET)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def should_redirect_to_share(self):
        parts = urlsplit(self.path)
        if parts.path in ("", "/"):
            return True
        if parts.path == "/session":
            query = parse_qs(parts.query)
            return query.get("viewer_admin") != ["1"]
        return False

    def should_redirect_to_admin(self):
        return urlsplit(self.path).path == "/admin"

    def should_serve_icon(self):
        return urlsplit(self.path).path in ICON_PATHS

    def serve_icon(self):
        if not os.path.exists(LOGO_PATH):
            self.send_error(404, "Logo is not configured")
            return
        size = os.path.getsize(LOGO_PATH)
        self.send_response(200)
        self.send_header("X-Viewer-Proxy", self.server_version)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(LOGO_PATH, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile, length=1024 * 1024)

    def proxy(self):
        conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=120)
        try:
            conn.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            saw_host = False
            for key, value in self.headers.items():
                lk = key.lower()
                if lk in HOP_BY_HOP:
                    continue
                if lk == "accept-encoding":
                    continue
                if lk == "host":
                    saw_host = True
                    conn.putheader("Host", value)
                else:
                    conn.putheader(key, value)
            if not saw_host:
                conn.putheader("Host", "viewer.lyx1311.top")
            conn.putheader("Connection", "close")
            conn.putheader("X-Forwarded-Proto", "https")
            conn.putheader("X-Forwarded-Host", self.headers.get("Host", "viewer.lyx1311.top"))
            conn.endheaders()

            length = self.headers.get("Content-Length")
            if length:
                remaining = int(length)
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    conn.send(chunk)
                    remaining -= len(chunk)
            elif self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                self.send_error(411, "Chunked request bodies are not supported by viewer-proxy")
                return

            resp = conn.getresponse()
            headers = resp.getheaders()
            content_type = ""
            response_length = None
            for key, value in headers:
                lk = key.lower()
                if lk == "content-type":
                    content_type = value.lower()
                elif lk == "content-length":
                    try:
                        response_length = int(value)
                    except ValueError:
                        response_length = None

            if self.should_patch_html(resp.status, content_type, response_length):
                self.send_patched_html(resp, headers, response_length)
                return

            self.send_response(resp.status, resp.reason)
            self.send_header("X-Viewer-Proxy", self.server_version)
            for key, value in headers:
                lk = key.lower()
                if lk in HOP_BY_HOP:
                    continue
                self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()

            if self.command == "HEAD":
                return
            if response_length is None:
                shutil.copyfileobj(resp, self.wfile, length=1024 * 1024)
                return
            remaining = response_length
            while remaining > 0:
                chunk = resp.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except BrokenPipeError:
            pass
        except Exception as exc:
            self.send_error(502, "Bad Gateway: %s" % exc)
        finally:
            conn.close()

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def should_patch_html(self, status, content_type, response_length):
        if self.command != "GET":
            return False
        if status != 200:
            return False
        if "text/html" not in content_type:
            return False
        if response_length is not None and response_length > 2 * 1024 * 1024:
            return False
        return True

    def send_patched_html(self, resp, headers, response_length):
        if response_length is None:
            body = resp.read()
        else:
            body = resp.read(response_length)
        marker = b"</head>"
        if marker in body and ADMIN_NAV_PATCH not in body:
            body = body.replace(marker, ADMIN_NAV_PATCH + marker, 1)

        self.send_response(resp.status, resp.reason)
        self.send_header("X-Viewer-Proxy", self.server_version)
        for key, value in headers:
            lk = key.lower()
            if lk in HOP_BY_HOP or lk == "content-length":
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    servers = []
    for port in LISTEN_PORTS:
        server = ThreadingHTTPServer(("127.0.0.1", port), ProxyHandler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print("viewer-proxy listening on 127.0.0.1:%d" % port, flush=True)
    threading.Event().wait()
