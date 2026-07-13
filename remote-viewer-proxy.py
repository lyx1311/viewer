#!/usr/bin/env python3
import http.client
import json
import mimetypes
import os
import shutil
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 18082
LISTEN_PORTS = (18080, 18081)
REDIRECT_TARGET = "/s/xguO"
ADMIN_TARGET = "/session?viewer_admin=1"
LOGO_PATH = "/opt/viewer-proxy/viewer-logo.png"
ASSET_PREFIX = "/viewer-assets/"
ASSET_ROOT = "/opt/viewer-proxy/assets"
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
KATEX_HEAD_ASSETS = b"""<link rel="stylesheet" href="/viewer-assets/katex/katex.min.css" data-viewer-katex="1">
<script data-viewer-katex="1">
window.__viewerKatexModuleGlobals = { define: window.define, module: window.module, exports: window.exports };
try { window.define = undefined; } catch (_) {}
try { window.module = undefined; } catch (_) {}
try { window.exports = undefined; } catch (_) {}
</script>
<script src="/viewer-assets/katex/katex.min.js" data-viewer-katex="1"></script>
<script src="/viewer-assets/katex/contrib/auto-render.min.js" data-viewer-katex="1"></script>
<script data-viewer-katex="1">
(function () {
  var old = window.__viewerKatexModuleGlobals || {};
  try { window.define = old.define; } catch (_) {}
  try { window.module = old.module; } catch (_) {}
  try { window.exports = old.exports; } catch (_) {}
  try { delete window.__viewerKatexModuleGlobals; } catch (_) {}
})();
</script>
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

  var markdownEnhancerPromise = null;
  var markdownEnhanceScheduled = false;
  var allowedHtmlTags = {
    img: true,
    br: true,
    span: true,
    sub: true,
    sup: true,
    kbd: true,
    mark: true,
    small: true,
    details: true,
    summary: true,
    u: true,
    s: true,
    del: true,
    ins: true,
    center: true
  };
  var voidHtmlTags = { img: true, br: true };
  var htmlTagPattern = /<\\/?(?:img|br|span|sub|sup|kbd|mark|small|details|summary|u|s|del|ins|center)\\b[^>]*>/i;
  var htmlAttributes = {
    img: { src: true, alt: true, title: true, width: true, height: true, loading: true, class: true },
    span: { class: true, title: true },
    sub: { class: true, title: true },
    sup: { class: true, title: true },
    kbd: { class: true, title: true },
    mark: { class: true, title: true },
    small: { class: true, title: true },
    details: { class: true, title: true, open: true },
    summary: { class: true, title: true },
    u: { class: true, title: true },
    s: { class: true, title: true },
    del: { class: true, title: true },
    ins: { class: true, title: true },
    center: { class: true, title: true },
    br: { class: true, title: true }
  };

  function ensureMarkdownEnhancerAssets() {
    if (markdownEnhancerPromise) return markdownEnhancerPromise;
    markdownEnhancerPromise = Promise.resolve().then(function () {
      if (!window.katex) runEmbeddedUmd("viewer-katex-js");
      if (!window.renderMathInElement) runEmbeddedUmd("viewer-katex-auto-render-js");
      document.documentElement.setAttribute("data-viewer-katex-loaded", window.katex && window.renderMathInElement ? "1" : "0");
    }).catch(function (error) {
      try {
        document.documentElement.setAttribute("data-viewer-katex-loaded", "error");
        document.documentElement.setAttribute("data-viewer-katex-error", String(error && error.message || error || "unknown"));
      } catch (_) {}
    });
    return markdownEnhancerPromise;
  }

  function runEmbeddedUmd(id) {
    var node = document.getElementById(id);
    if (!node) throw new Error("missing embedded script: " + id);
    var source = node.textContent || "";
    (0, eval)("(function(){var define=undefined,module=undefined,exports=undefined;\\n" + source + "\\n})();\\n//# sourceURL=" + id);
  }

  function isSafeImgSrc(value) {
    var raw = String(value || "").trim();
    if (!raw) return false;
    var lower = raw.toLowerCase();
    if (/^[a-z0-9+.-]+:/i.test(raw)) {
      if (lower.indexOf("data:image/") === 0) return true;
      try {
        var parsed = new URL(raw, window.location.href);
        return parsed.protocol === "http:" || parsed.protocol === "https:";
      } catch (_) {
        return false;
      }
    }
    if (lower.indexOf("//") === 0) return true;
    if (lower.indexOf("javascript:") === 0 || lower.indexOf("vbscript:") === 0 || lower.indexOf("data:") === 0) return false;
    return true;
  }

  function textFallbackForElement(element) {
    return document.createTextNode(element.outerHTML || element.textContent || "");
  }

  function sanitizeHtmlNode(node) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      var tag = node.tagName.toLowerCase();
      if (!allowedHtmlTags[tag]) {
        node.parentNode.replaceChild(textFallbackForElement(node), node);
        return true;
      }
      Array.prototype.slice.call(node.attributes).forEach(function (attr) {
        var name = attr.name.toLowerCase();
        var allowed = htmlAttributes[tag] && htmlAttributes[tag][name];
        if (!allowed || name.indexOf("on") === 0) {
          node.removeAttribute(attr.name);
          return;
        }
        if (tag === "img" && name === "src" && !isSafeImgSrc(attr.value)) {
          node.removeAttribute(attr.name);
        }
      });
      if (tag === "img") {
        if (!node.getAttribute("loading")) node.setAttribute("loading", "lazy");
        node.setAttribute("referrerpolicy", "no-referrer");
      }
      Array.prototype.slice.call(node.childNodes).forEach(sanitizeHtmlNode);
      return true;
    }
    if (node.nodeType === Node.COMMENT_NODE) {
      node.parentNode.removeChild(node);
      return true;
    }
    return false;
  }

  function safeHtmlFragmentFromText(text) {
    if (!htmlTagPattern.test(text)) return null;
    var template = document.createElement("template");
    template.innerHTML = text;
    var hasAllowedElement = false;
    Array.prototype.slice.call(template.content.childNodes).forEach(function (node) {
      sanitizeHtmlNode(node);
    });
    Object.keys(allowedHtmlTags).forEach(function (tag) {
      if (!hasAllowedElement && template.content.querySelector && template.content.querySelector(tag)) {
        hasAllowedElement = true;
      }
    });
    return hasAllowedElement ? template.content : null;
  }

  function decodeSafeHtmlText(root) {
    if (!root) return;
    var walker = document.createTreeWalker(root, 4, {
      acceptNode: function (node) {
        if (!node.nodeValue || !htmlTagPattern.test(node.nodeValue)) return 2;
        var parent = node.parentElement;
        if (!parent) return 2;
        if (parent.closest("script,style,textarea,pre,code")) return 2;
        return 1;
      }
    });
    var nodes = [];
    var current;
    while ((current = walker.nextNode())) nodes.push(current);
    nodes.forEach(function (node) {
      var fragment = safeHtmlFragmentFromText(node.nodeValue);
      if (fragment && node.parentNode) node.parentNode.replaceChild(fragment, node);
    });
  }

  function normalizeTexForMarkdown(tex) {
    var value = String(tex || "");
    // Lexical/CommonMark consumes one slash from a TeX row separator at the
    // physical end of a Markdown line. Restore it before KaTeX sees it.
    var hadBrokenRows = /(^|[^\\\\])\\\\(?=[ \\t]*(?:\\r?\\n|$))/m.test(value);
    if (hadBrokenRows) {
      value = value.replace(/(^|[^\\\\])\\\\(?=[ \\t]*(?:\\r?\\n|$))/gm, "$1\\\\\\\\");
    }
    var hasRows = /\\\\\\\\[ \\t]*(?:\\r?\\n|$)/m.test(value);
    var hasRowEnvironment = /\\\\begin\\{(?:aligned|alignedat|array|cases|gathered|matrix|bmatrix|Bmatrix|pmatrix|vmatrix|Vmatrix|smallmatrix|split)\\}/.test(value);
    // A bare multi-line display block is valid in many note-taking apps but
    // not in KaTeX. Give it an aligned environment without changing the file.
    if (hasRows && !hasRowEnvironment) {
      value = "\\\\begin{aligned}\\n" + value + "\\n\\\\end{aligned}";
    }
    return value;
  }

  function repairRenderedKatex(root) {
    if (!root || !window.katex || !window.katex.renderToString) return;
    root.querySelectorAll(".katex annotation[encoding='application/x-tex']").forEach(function (annotation) {
      var tex = annotation.textContent || "";
      var repaired = normalizeTexForMarkdown(tex);
      if (repaired === tex) return;
      var rendered = annotation.closest(".katex");
      if (!rendered || rendered.getAttribute("data-viewer-row-repaired") === "1") return;
      try {
        var template = document.createElement("template");
        var display = !!rendered.closest(".katex-display");
        template.innerHTML = window.katex.renderToString(repaired, {
          displayMode: display,
          throwOnError: false,
          strict: "ignore",
          trust: false
        });
        var replacement = template.content.firstElementChild;
        if (replacement) {
          replacement.setAttribute("data-viewer-row-repaired", "1");
          rendered.replaceWith(replacement);
        }
      } catch (_) {}
    });
  }

  function renderTexText(root) {
    if (!root || !window.katex || !window.katex.renderToString) return;
    var walker = document.createTreeWalker(root, 4, {
      acceptNode: function (node) {
        var text = node.nodeValue || "";
        if (text.indexOf("$") === -1 && text.indexOf("\\\\(") === -1 && text.indexOf("\\\\[") === -1) return 2;
        var parent = node.parentElement;
        if (!parent) return 2;
        if (parent.closest("script,style,textarea,pre,code,.katex,[data-lexical-editor='true']")) return 2;
        return 1;
      }
    });
    var nodes = [];
    var current;
    while ((current = walker.nextNode())) nodes.push(current);
    var pattern = /(\\$\\$[\\s\\S]+?\\$\\$|\\\\\\[[\\s\\S]+?\\\\\\]|\\\\\\([\\s\\S]+?\\\\\\)|\\$[^\\n$]+?\\$)/g;
    nodes.forEach(function (node) {
      var text = node.nodeValue || "";
      if (!pattern.test(text)) return;
      pattern.lastIndex = 0;
      var fragment = document.createDocumentFragment();
      var last = 0;
      var changed = false;
      var match;
      while ((match = pattern.exec(text))) {
        var token = match[0];
        var display = false;
        var tex = "";
        if (token.indexOf("$$") === 0) {
          display = true;
          tex = token.slice(2, -2);
        } else if (token.indexOf("\\\\[") === 0) {
          display = true;
          tex = token.slice(2, -2);
        } else if (token.indexOf("\\\\(") === 0) {
          tex = token.slice(2, -2);
        } else {
          tex = token.slice(1, -1);
        }
        if (!tex.trim()) continue;
        if (match.index > last) fragment.appendChild(document.createTextNode(text.slice(last, match.index)));
        try {
          var template = document.createElement("template");
          template.innerHTML = window.katex.renderToString(normalizeTexForMarkdown(tex), { displayMode: display, throwOnError: false });
          fragment.appendChild(template.content);
          changed = true;
        } catch (_) {
          fragment.appendChild(document.createTextNode(token));
        }
        last = match.index + token.length;
      }
      if (!changed) return;
      if (last < text.length) fragment.appendChild(document.createTextNode(text.slice(last)));
      if (node.parentNode) node.parentNode.replaceChild(fragment, node);
    });
  }

  function texTextFromDisplayBlock(block) {
    // Within a display formula, Cloudreve can turn _{...} into an <em> node.
    // Rebuild that exact Markdown emphasis boundary as TeX subscript markers.
    function visit(node) {
      if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
      if (node.nodeType !== Node.ELEMENT_NODE) return "";
      if (node.tagName === "BR") return "\\n";
      var content = Array.prototype.map.call(node.childNodes, visit).join("");
      return node.tagName === "EM" ? "_" + content + "_" : content;
    }
    return visit(block);
  }

  function renderKatexDisplayBlock(block, tex) {
    try {
      block.innerHTML = window.katex.renderToString(normalizeTexForMarkdown(tex), {
        displayMode: true,
        throwOnError: false,
        strict: "ignore",
        trust: false
      });
      block.setAttribute("data-viewer-display-math", "1");
      return true;
    } catch (_) {
      return false;
    }
  }

  function renderLexicalDisplayBlocks(root) {
    if (!root || !root.children || !window.katex) return;
    var blocks = Array.prototype.slice.call(root.children);
    for (var i = 0; i < blocks.length; i += 1) {
      var first = blocks[i];
      if (!first || first.getAttribute("data-viewer-display-math") === "1") continue;
      var firstText = texTextFromDisplayBlock(first);
      // Cloudreve's Markdown importer can split a display formula into two
      // paragraphs: an opening delimiter plus formula, followed by a closing
      // "$$" paragraph. Only join a deliberately standalone formula run; in
      // particular, never search across a heading/list/plain-text block.
      if (first.tagName !== "P") continue;
      var trimmedFirst = firstText.replace(/^\\s+/, "");
      var open = trimmedFirst.indexOf("$$");
      var left = "$$";
      var right = "$$";
      if (open < 0) {
        open = trimmedFirst.indexOf("\\\\[");
        left = "\\\\[";
        right = "\\\\]";
      }
      if (open !== 0) continue;
      var afterOpen = trimmedFirst.slice(left.length);
      var sameClose = afterOpen.indexOf(right);
      if (sameClose >= 0) {
        // Same-paragraph delimiters can still be split across inline spans by
        // the rich-text parser, so render them here instead of relying on a
        // text-node-only pass later.
        if (afterOpen.slice(sameClose + right.length).trim()) continue;
        var singleTex = afterOpen.slice(0, sameClose).trim();
        if (singleTex) renderKatexDisplayBlock(first, singleTex);
        continue;
      }

      var texParts = [afterOpen];
      var end = -1;
      for (var j = i + 1; j < blocks.length; j += 1) {
        var candidate = blocks[j];
        // A display block may span only adjacent paragraphs. This boundary is
        // what makes malformed/unclosed delimiters harmless to nearby content.
        if (!candidate || candidate.tagName !== "P") break;
        var part = texTextFromDisplayBlock(candidate);
        var trimmedPart = part.trim();
        if (trimmedPart === right) {
          end = j;
          break;
        }
        // A second opening delimiter means this was not one formula run.
        if (trimmedPart.indexOf(left) === 0 || trimmedPart.indexOf(right) >= 0) break;
        texParts.push(part);
      }
      if (end < 0) continue;
      var tex = texParts.join("\\n").trim();
      if (!tex) continue;
      if (renderKatexDisplayBlock(first, tex)) {
        for (var k = i + 1; k <= end; k += 1) blocks[k].style.display = "none";
        i = end;
      }
    }
  }

  function enhanceLexicalPreviews() {
    if (!window.katex) return;
    document.querySelectorAll("[data-lexical-editor='true'][contenteditable='false']").forEach(function (editor) {
      if (editor.closest("[data-viewer-lexical-clone='1']")) return;
      var wrapper = editor.parentElement;
      if (!wrapper) return;
      var signature = (editor.textContent || "") + "|" + editor.childElementCount;
      var clone = wrapper.querySelector(":scope > [data-viewer-lexical-clone='1']");
      if (clone && clone.getAttribute("data-viewer-source-signature") === signature) {
        // A replacement editor can already be hidden when it is cloned.  Never
        // let that transient source state hide the stable read-only preview.
        clone.style.removeProperty("display");
        editor.style.display = "none";
        return;
      }
      if (clone) clone.remove();

      clone = editor.cloneNode(true);
      clone.setAttribute("data-viewer-lexical-clone", "1");
      clone.setAttribute("data-viewer-source-signature", signature);
      clone.removeAttribute("data-lexical-editor");
      clone.removeAttribute("contenteditable");
      clone.removeAttribute("aria-label");
      clone.removeAttribute("aria-readonly");
      clone.style.removeProperty("display");
      clone.querySelectorAll("[id]").forEach(function (node) { node.removeAttribute("id"); });
      clone.querySelectorAll("[data-lexical-text]").forEach(function (node) {
        node.removeAttribute("data-lexical-text");
        node.removeAttribute("data-viewer-tex-rendered");
      });
      wrapper.appendChild(clone);
      editor.style.display = "none";
      decodeSafeHtmlText(clone);
      repairRenderedKatex(clone);
      renderLexicalDisplayBlocks(clone);
      renderTexText(clone);
    });
  }

  function escapeHtmlText(value) {
    return String(value || "").replace(/[&<>"]/g, function (ch) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch];
    });
  }

  function renderTexHtml(text) {
    var pattern = /(\\$\\$[\\s\\S]+?\\$\\$|\\\\\\[[\\s\\S]+?\\\\\\]|\\\\\\([\\s\\S]+?\\\\\\)|\\$[^\\n$]+?\\$)/g;
    var last = 0;
    var changed = false;
    var html = "";
    var match;
    while ((match = pattern.exec(text))) {
      var token = match[0];
      var display = false;
      var tex = "";
      if (token.indexOf("$$") === 0) {
        display = true;
        tex = token.slice(2, -2);
      } else if (token.indexOf("\\\\[") === 0) {
        display = true;
        tex = token.slice(2, -2);
      } else if (token.indexOf("\\\\(") === 0) {
        tex = token.slice(2, -2);
      } else {
        tex = token.slice(1, -1);
      }
      if (!tex.trim()) continue;
      html += escapeHtmlText(text.slice(last, match.index));
      try {
        html += window.katex.renderToString(normalizeTexForMarkdown(tex), { displayMode: display, throwOnError: false });
        changed = true;
      } catch (_) {
        html += escapeHtmlText(token);
      }
      last = match.index + token.length;
    }
    if (!changed) return null;
    html += escapeHtmlText(text.slice(last));
    return html;
  }

  function renderTexLexicalSpans(root) {
    if (!root.querySelectorAll) return;
    root.querySelectorAll("span[data-lexical-text='true']").forEach(function (span) {
      if (span.getAttribute("data-viewer-tex-rendered") === "1") return;
      var text = span.textContent || "";
      if (text.indexOf("$") === -1 && text.indexOf("\\\\(") === -1 && text.indexOf("\\\\[") === -1) return;
      var html = renderTexHtml(text);
      if (!html) return;
      var holder = document.createElement("span");
      holder.setAttribute("data-viewer-tex-holder", "1");
      holder.style.display = "block";
      holder.style.margin = "0.25rem 0";
      holder.innerHTML = html;
      var container = (span.closest && (span.closest(".mdxeditor-rich-text-editor") || span.closest(".mdxeditor"))) || span.parentNode;
      if (container) {
        container.appendChild(holder);
        span.setAttribute("data-viewer-tex-rendered", "1");
      }
    });
  }

  function markdownCandidateRoots() {
    var selectors = [
      ".markdown-body",
      "[class*='markdown']",
      "[class*='Markdown']",
      "[class*='preview']",
      "[class*='Preview']",
      "[class*='viewer']",
      "[class*='Viewer']",
      "article"
    ];
    var roots = [];
    selectors.forEach(function (selector) {
      document.querySelectorAll(selector).forEach(function (node) {
        if (node && roots.indexOf(node) === -1) roots.push(node);
      });
    });
    if (document.body && roots.indexOf(document.body) === -1) roots.push(document.body);
    return roots.filter(function (node) {
      if (!node || !node.textContent) return false;
      if (node.closest && node.closest("script,style,textarea")) return false;
      return /\\$|\\\\\\(|\\\\\\[|<\\/?(?:img|br|span|sub|sup|kbd|mark|small|details|summary|u|s|del|ins|center)\\b/i.test(node.textContent);
    });
  }

  function enhanceMarkdownNow() {
    markdownEnhanceScheduled = false;
    ensureMarkdownEnhancerAssets().then(function () {
      enhanceLexicalPreviews();
      markdownCandidateRoots().forEach(function (root) {
        try {
          decodeSafeHtmlText(root);
          renderTexText(root);
          if (window.renderMathInElement) {
            window.renderMathInElement(root, {
              delimiters: [
                { left: "$$", right: "$$", display: true },
                { left: "\\\\[", right: "\\\\]", display: true },
                { left: "\\\\(", right: "\\\\)", display: false },
                { left: "$", right: "$", display: false }
              ],
              ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option"],
              throwOnError: false
            });
          }
        } catch (_) {}
      });
    });
  }

  function scheduleMarkdownEnhance() {
    if (markdownEnhanceScheduled) return;
    markdownEnhanceScheduled = true;
    window.setTimeout(enhanceMarkdownNow, 250);
  }

  function startMarkdownPatch() {
    scheduleMarkdownEnhance();
    var observer = new MutationObserver(scheduleMarkdownEnhance);
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    var attempts = 0;
    var timer = setInterval(function () {
      scheduleMarkdownEnhance();
      attempts += 1;
      if (attempts >= 60) clearInterval(timer);
    }, 500);
  }

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
    startMarkdownPatch();
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
    server_version = "viewer-proxy/1.16"

    def do_GET(self):
        if self.should_serve_asset():
            self.serve_asset()
            return
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
        if self.should_serve_asset():
            self.serve_asset()
            return
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

    def should_serve_asset(self):
        return urlsplit(self.path).path.startswith(ASSET_PREFIX)

    def katex_head_assets(self):
        try:
            css = self.read_asset("katex/katex.min.css")
            katex_js = self.read_asset("katex/katex.min.js")
            auto_render_js = self.read_asset("katex/contrib/auto-render.min.js")
        except OSError:
            return KATEX_HEAD_ASSETS

        css = css.replace(b"url(fonts/", b"url(/viewer-assets/katex/fonts/")
        return b"".join([
            b'<style data-viewer-katex="1">\n',
            css,
            b"\n</style>\n",
            b'<script type="application/viewer-katex-source" id="viewer-katex-js" data-viewer-katex="1">\n',
            self.safe_inline_script(katex_js),
            b"\n</script>\n",
            b'<script type="application/viewer-katex-source" id="viewer-katex-auto-render-js" data-viewer-katex="1">\n',
            self.safe_inline_script(auto_render_js),
            b"\n</script>\n",
        ])

    def read_asset(self, relative):
        root = os.path.realpath(ASSET_ROOT)
        asset_path = os.path.realpath(os.path.join(root, relative))
        if asset_path != root and not asset_path.startswith(root + os.sep):
            raise OSError("asset path escapes root")
        with open(asset_path, "rb") as fh:
            return fh.read()

    def safe_inline_script(self, data):
        return data.replace(b"</script", b"<\\/script").replace(b"</SCRIPT", b"<\\/SCRIPT")

    def serve_asset(self):
        request_path = urlsplit(self.path).path
        relative = unquote(request_path[len(ASSET_PREFIX):])
        relative = relative.lstrip("/")
        relative = os.path.normpath(relative)
        if relative in ("", ".") or relative.startswith("..") or os.path.isabs(relative):
            self.send_error(404, "Asset not found")
            return

        root = os.path.realpath(ASSET_ROOT)
        asset_path = os.path.realpath(os.path.join(root, relative))
        if asset_path != root and not asset_path.startswith(root + os.sep):
            self.send_error(404, "Asset not found")
            return
        if not os.path.isfile(asset_path):
            self.send_error(404, "Asset not found")
            return

        content_type = mimetypes.guess_type(asset_path)[0] or "application/octet-stream"
        if asset_path.endswith(".js"):
            content_type = "application/javascript; charset=utf-8"
        elif asset_path.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif asset_path.endswith(".woff2"):
            content_type = "font/woff2"
        elif asset_path.endswith(".woff"):
            content_type = "font/woff"
        elif asset_path.endswith(".ttf"):
            content_type = "font/ttf"

        size = os.path.getsize(asset_path)
        self.send_response(200)
        self.send_header("X-Viewer-Proxy", self.server_version)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(asset_path, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile, length=1024 * 1024)

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
        injection = self.katex_head_assets() + VIEWER_PATCH
        if head_marker in body and VIEWER_PATCH not in body:
            body = body.replace(head_marker, head_marker + injection, 1)
        elif marker in body and VIEWER_PATCH not in body:
            body = body.replace(marker, injection + marker, 1)
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
