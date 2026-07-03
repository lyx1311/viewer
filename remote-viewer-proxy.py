#!/usr/bin/env python3
import http.client
import json
import os
import shutil
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

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
SERVICE_WORKER_PATH = "/sw.js"
SERVICE_WORKER_CLEANUP = b"""self.addEventListener("install", function (event) {
  self.skipWaiting();
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.map(function (key) { return caches.delete(key); }));
  }));
});
self.addEventListener("activate", function (event) {
  event.waitUntil((async function () {
    try {
      await clients.claim();
      await self.registration.unregister();
    } catch (_) {}
  })());
});
"""
VIEWER_PATCH = """<script>
(function () {
  var viewerDefaults = {
    open_with_md: "markdown",
    open_with_markdown: "markdown",
    open_with_txt: "monaco",
    open_with_text: "monaco",
    open_with_json: "monaco",
    open_with_yaml: "monaco",
    open_with_yml: "monaco",
    open_with_html: "monaco",
    open_with_htm: "monaco",
    open_with_css: "monaco",
    open_with_js: "monaco",
    open_with_py: "monaco",
    open_with_sh: "monaco",
    open_with_ini: "monaco",
    open_with_pdf: "pdf",
    open_with_png: "image",
    open_with_jpg: "image",
    open_with_jpeg: "image",
    open_with_gif: "image",
    open_with_webp: "image",
    open_with_svg: "image",
    open_with_bmp: "image",
    open_with_avif: "image",
    layout: "list",
    sort_by: "name",
    sort_direction: "asc",
    folder_click_action: "open"
  };
  function patchSettings(settings) {
    if (!settings || typeof settings !== "object") return;
    Object.keys(viewerDefaults).forEach(function (key) {
      settings[key] = viewerDefaults[key];
    });
  }
  function seedViewerDefaults() {
    try {
      var key = "cloudreve_session";
      var raw = localStorage.getItem(key);
      var state = raw ? JSON.parse(raw) : {};
      if (!state || typeof state !== "object") state = {};
      if (!state.sessions || typeof state.sessions !== "object") state.sessions = {};
      if (!state.anonymousSettings || typeof state.anonymousSettings !== "object") state.anonymousSettings = {};
      patchSettings(state.anonymousSettings);
      Object.keys(state.sessions).forEach(function (sessionId) {
        var session = state.sessions[sessionId];
        if (!session || typeof session !== "object") return;
        if (!session.settings || typeof session.settings !== "object") session.settings = {};
        patchSettings(session.settings);
      });
      localStorage.setItem(key, JSON.stringify(state));
    } catch (_) {}
  }
  seedViewerDefaults();
  try {
    if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
      navigator.serviceWorker.getRegistrations().then(function (registrations) {
        registrations.forEach(function (registration) {
          try { registration.unregister(); } catch (_) {}
        });
      }).catch(function () {});
    }
    if (window.caches && caches.keys) {
      caches.keys().then(function (keys) {
        keys.forEach(function (key) {
          try { caches.delete(key); } catch (_) {}
        });
      }).catch(function () {});
    }
  } catch (_) {}

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
  function isShareView() {
    try {
      var url = new URL(window.location.href);
      var path = url.searchParams.get("path") || "";
      return url.pathname.indexOf("/s/") === 0 || path.indexOf("@share") !== -1;
    } catch (_) {
      return false;
    }
  }
  function hideVisitorCreateButton() {
    if (!isShareView()) return;
    var buttons = document.querySelectorAll("button");
    buttons.forEach(function (button) {
      var text = (button.textContent || "").replace(/\\s+/g, "");
      if (text === "新建" || text.toLowerCase() === "new") {
        button.style.display = "none";
        button.setAttribute("aria-hidden", "true");
        button.setAttribute("data-viewer-hidden-create", "1");
      }
    });
  }
  function installSingleClickOpen() {
    if (!isShareView() || document.documentElement.getAttribute("data-viewer-single-click-open") === "1") return;
    document.documentElement.setAttribute("data-viewer-single-click-open", "1");
    document.addEventListener("click", function (event) {
      if (!isShareView()) return;
      if (event.defaultPrevented || event.button !== 0 || event.detail !== 1) return;
      if (event.target && event.target.closest && event.target.closest("button,a,input,textarea,select,[role='button']")) return;
      var row = event.target && event.target.closest && event.target.closest("[data-item-index]");
      if (!row) return;
      var target = event.target;
      window.setTimeout(function () {
        try {
          target.dispatchEvent(new MouseEvent("dblclick", {
            bubbles: true,
            cancelable: true,
            view: window,
            button: 0,
            buttons: 1,
            clientX: event.clientX,
            clientY: event.clientY
          }));
        } catch (_) {}
      }, 80);
    }, true);
  }
  var style = document.createElement("style");
  style.textContent = "button[data-viewer-hidden-create='1']{display:none!important}";
  function startVisitorPatch() {
    try { document.head.appendChild(style); } catch (_) {}
    hideVisitorCreateButton();
    installSingleClickOpen();
    var observer = new MutationObserver(hideVisitorCreateButton);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    var attempts = 0;
    var timer = setInterval(function () {
      hideVisitorCreateButton();
      attempts += 1;
      if (attempts >= 40) clearInterval(timer);
    }, 250);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startVisitorPatch);
  } else {
    startVisitorPatch();
  }
})();
</script>
""".encode("utf-8")

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
    server_version = "viewer-proxy/1.10"

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
        if self.should_serve_service_worker():
            self.serve_service_worker()
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
        if self.should_serve_service_worker():
            self.serve_service_worker()
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

    def should_serve_service_worker(self):
        return urlsplit(self.path).path == SERVICE_WORKER_PATH

    def serve_service_worker(self):
        self.send_response(200)
        self.send_header("X-Viewer-Proxy", self.server_version)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(SERVICE_WORKER_CLEANUP)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Service-Worker-Allowed", "/")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command == "HEAD":
            return
        self.wfile.write(SERVICE_WORKER_CLEANUP)

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
            upstream_path = self.normalized_upstream_path()
            conn.putrequest(self.command, upstream_path, skip_host=True, skip_accept_encoding=True)
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
            if self.should_patch_file_json(resp.status, content_type, response_length):
                self.send_patched_file_json(resp, headers, response_length)
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

    def normalized_upstream_path(self):
        if self.command != "GET":
            return self.path
        parts = urlsplit(self.path)
        if parts.path != "/api/v4/file":
            return self.path

        query = parse_qsl(parts.query, keep_blank_values=True)
        keys = {key for key, _ in query}
        changed = False
        if "page_size" not in keys:
            query.append(("page_size", "100"))
            changed = True
        if "order_by" not in keys:
            query.append(("order_by", "name"))
            changed = True
        if "order_direction" not in keys:
            query.append(("order_direction", "asc"))
            changed = True
        if not changed:
            return self.path
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

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

    def should_patch_file_json(self, status, content_type, response_length):
        if self.command != "GET":
            return False
        if status != 200:
            return False
        if urlsplit(self.path).path != "/api/v4/file":
            return False
        if "application/json" not in content_type:
            return False
        if response_length is not None and response_length > 8 * 1024 * 1024:
            return False
        return True

    def send_patched_file_json(self, resp, headers, response_length):
        if response_length is None:
            body = resp.read()
        else:
            body = resp.read(response_length)
        patched = body
        try:
            payload = json.loads(body.decode("utf-8"))
            files = payload.get("data", {}).get("files")
            if isinstance(files, list):
                files.sort(key=self.file_sort_key)
                patched = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except Exception:
            patched = body

        self.send_response(resp.status, resp.reason)
        self.send_header("X-Viewer-Proxy", self.server_version)
        for key, value in headers:
            lk = key.lower()
            if lk in HOP_BY_HOP or lk == "content-length":
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(patched)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(patched)

    def file_sort_key(self, item):
        if not isinstance(item, dict):
            return (2, "")
        is_dir = 0 if item.get("type") == 1 else 1
        name = str(item.get("name", ""))
        return (is_dir, name.casefold(), name)

    def send_patched_html(self, resp, headers, response_length):
        if response_length is None:
            body = resp.read()
        else:
            body = resp.read(response_length)
        marker = b"</head>"
        head_marker = b"<head>"
        if head_marker in body and VIEWER_PATCH not in body:
            body = body.replace(head_marker, head_marker + VIEWER_PATCH, 1)
        elif marker in body and VIEWER_PATCH not in body:
            body = body.replace(marker, VIEWER_PATCH + marker, 1)
        body = body.replace(b"<script async type=\"module\"", b"<script type=\"module\"")

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
