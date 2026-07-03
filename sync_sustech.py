#!/usr/bin/env python3
"""Publish Markdown files and their referenced images to Cloudreve via WebDAV."""

from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import fnmatch
import getpass
import hashlib
import html
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import time
import tomllib
from typing import Iterable
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

VERSION = 1
DAV_NS = "DAV:"


class SyncError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, object]:
    buf = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise SyncError("DPAPI credentials are supported only on Windows")
    in_blob, keep = _blob(data)
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob), "Viewer SUSTech Sync", None, None, None, 1,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise SyncError("DPAPI credentials are supported only on Windows")
    in_blob, keep = _blob(data)
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 1, ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def quote_path(path: str) -> str:
    return "/".join(urllib.parse.quote(p, safe="!$&'()*+,;=:@-._~") for p in path.split("/"))


def markdown_url(path: PurePosixPath) -> str:
    return "/".join(urllib.parse.quote(p, safe="!$&'()*+,;=:@-._~") for p in path.parts)


def is_external(ref: str) -> bool:
    low = ref.strip().lower()
    return low.startswith(("http://", "https://", "data:", "cloudreve://", "#", "mailto:"))


def strip_title(value: str) -> str:
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        return value[1:-1]
    # Preserve spaces in filenames; strip an optional quoted title only when separated by whitespace.
    return re.sub(r"\s+[\"'](?:[^\"']*)[\"']\s*$", "", value)


class Publisher:
    def __init__(self, config_path: Path):
        self.base = config_path.resolve().parent
        with config_path.open("rb") as fh:
            cfg = tomllib.load(fh)
        self.cfg = cfg
        s = cfg["sync"]
        self.source = Path(s["source"]).resolve()
        self.remote_root = s["remote_root"].strip("/")
        self.site_url = s["site_url"].rstrip("/")
        self.dav_path = "/" + s["dav_path"].strip("/")
        self.credential_file = self.base / s["credential_file"]
        self.state_file = self.base / s["state_file"]
        self.cache_dir = self.base / s["cache_dir"]
        self.report_file = self.base / s["report_file"]
        self.log_file = self.base / s["log_file"]
        self.marker_name = s["marker_name"]
        self.marker_value = s["marker_value"]
        self.old_roots = [Path(p) for p in s.get("old_roots", [])]
        self.image_exts = {x.lower() for x in s["image_extensions"]}
        self.excludes = cfg.get("exclude", {}).get("patterns", [])
        self.warnings: list[dict[str, str]] = []
        self.logger = logging.getLogger("viewer-sync")

    def setup_logging(self, verbose: bool = False) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        self.logger.handlers.clear()
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file_handler = RotatingFileHandler(self.log_file, maxBytes=2_000_000, backupCount=4, encoding="utf-8")
        file_handler.setFormatter(fmt)
        self.logger.addHandler(file_handler)
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        self.logger.addHandler(console)

    def excluded(self, rel: PurePosixPath) -> bool:
        text = rel.as_posix()
        return any(fnmatch.fnmatchcase(text, p) or fnmatch.fnmatchcase("/" + text, p) for p in self.excludes)

    def resolve_ref(self, md_path: Path, raw: str) -> Path | None:
        ref = html.unescape(raw.strip())
        if not ref or is_external(ref):
            return None
        ref = strip_title(ref)
        parsed = urllib.parse.urlsplit(ref)
        if parsed.scheme.lower() == "file":
            ref = urllib.parse.unquote(parsed.path)
            if re.match(r"^/[A-Za-z]:/", ref):
                ref = ref[1:]
        else:
            # Keep # and ? initially: both are legal Windows filename characters in
            # existing notes (notably "Final 2022 #5.png"). URL fragments are only
            # treated as such when the literal filename cannot be found.
            ref = urllib.parse.unquote(ref)
        ref = ref.replace("\\", "/")
        candidate = Path(ref)
        if re.match(r"^[A-Za-z]:/", ref):
            old_match = None
            for old in self.old_roots:
                old_s = old.as_posix().rstrip("/")
                if ref.lower().startswith(old_s.lower() + "/"):
                    old_match = ref[len(old_s) + 1 :]
                    break
            candidate = self.source / old_match if old_match is not None else Path(ref)
        elif not candidate.is_absolute():
            candidate = md_path.parent / candidate
        if not candidate.exists() and ("#" in ref or "?" in ref):
            without_suffix = ref.split("#", 1)[0].split("?", 1)[0]
            fallback = Path(without_suffix.replace("\\", "/"))
            candidate = fallback if fallback.is_absolute() else md_path.parent / fallback
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self.source)
            return resolved
        except (ValueError, OSError):
            return candidate.resolve(strict=False)

    def relative_ref(self, md_path: Path, image: Path) -> str:
        rel = os.path.relpath(image, md_path.parent).replace("\\", "/")
        return markdown_url(PurePosixPath(rel))

    def transform_markdown(self, md_path: Path, text: str) -> tuple[str, set[Path]]:
        images: set[Path] = set()

        def handle(raw: str) -> str | None:
            if is_external(raw):
                return raw
            path = self.resolve_ref(md_path, raw)
            if path is None:
                return raw
            ext = path.suffix.lower()
            if ext not in self.image_exts:
                return raw
            try:
                rel_source = PurePosixPath(path.relative_to(self.source).as_posix())
            except ValueError:
                self.warnings.append({"markdown": str(md_path), "reference": raw, "reason": "outside source root"})
                return raw
            if self.excluded(rel_source):
                self.warnings.append({"markdown": str(md_path), "reference": raw, "reason": "excluded"})
                return raw
            if not path.is_file():
                self.warnings.append({"markdown": str(md_path), "reference": raw, "reason": "not found"})
                return raw
            images.add(path)
            return self.relative_ref(md_path, path)

        # Obsidian embeds. Optional alias after | is retained as alt text.
        def obsidian(match: re.Match[str]) -> str:
            value = match.group(1)
            target, _, alias = value.partition("|")
            replacement = handle(target)
            if replacement == target and not (self.resolve_ref(md_path, target) or Path(target).suffix.lower() in self.image_exts):
                return match.group(0)
            return f"![{alias}]({replacement})"

        text = re.sub(r"!\[\[([^\]]+)\]\]", obsidian, text)

        # HTML images, with single or double quoted src.
        def html_img(match: re.Match[str]) -> str:
            before, quote, value = match.group(1), match.group(2), match.group(3)
            replacement = handle(value)
            return before + quote + (replacement if replacement is not None else value) + quote

        text = re.sub(r"(?is)(<img\b[^>]*?\bsrc\s*=\s*)([\"'])(.*?)(?:\2)", html_img, text)

        # Markdown image destinations. This deliberately accepts legacy unescaped spaces.
        def md_img(match: re.Match[str]) -> str:
            alt, value = match.group(1), match.group(2)
            replacement = handle(value)
            return f"![{alt}]({replacement if replacement is not None else value})"

        text = re.sub(r"!\[([^\]]*)\]\(([^\n]+?)\)", md_img, text)
        return text, images

    def build(self) -> dict[str, dict[str, object]]:
        if not self.source.is_dir():
            raise SyncError(f"Source directory does not exist: {self.source}")
        temp = Path(tempfile.mkdtemp(prefix="viewer-build-", dir=self.base))
        files: dict[str, dict[str, object]] = {}
        all_images: set[Path] = set()
        md_count = 0
        try:
            for md in sorted(self.source.rglob("*.md")):
                rel = PurePosixPath(md.relative_to(self.source).as_posix())
                if self.excluded(rel):
                    continue
                md_count += 1
                raw = md.read_text(encoding="utf-8-sig", errors="replace")
                converted, images = self.transform_markdown(md, raw)
                all_images.update(images)
                out = temp / Path(*rel.parts)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(converted, encoding="utf-8", newline="\n")
            for image in sorted(all_images):
                rel = PurePosixPath(image.relative_to(self.source).as_posix())
                out = temp / Path(*rel.parts)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(image, out)
            for path in sorted(p for p in temp.rglob("*") if p.is_file()):
                rel = path.relative_to(temp).as_posix()
                files[rel] = {"path": path, "size": path.stat().st_size, "sha256": sha256(path)}
            if self.cache_dir.exists():
                shutil.rmtree(self.cache_dir)
            temp.replace(self.cache_dir)
            for data in files.values():
                data["path"] = self.cache_dir / Path(str(data["path"]).split(str(temp) + os.sep, 1)[-1])
            self.logger.info("Built %d Markdown files and %d referenced images", md_count, len(all_images))
            return files
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise

    def load_credentials(self) -> tuple[str, str]:
        if not self.credential_file.exists():
            raise SyncError(f"Credential file not found: {self.credential_file}. Run --set-credentials first.")
        payload = json.loads(dpapi_unprotect(self.credential_file.read_bytes()).decode("utf-8"))
        return payload["username"], payload["password"]

    def save_credentials(self, username: str, password: str) -> None:
        self.credential_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"username": username, "password": password}).encode("utf-8")
        self.credential_file.write_bytes(dpapi_protect(payload))
        self.logger.info("Saved DPAPI-protected credentials to %s", self.credential_file)

    def write_report(self, report: dict[str, object]) -> None:
        self.report_file.parent.mkdir(parents=True, exist_ok=True)
        report["warnings"] = self.warnings
        report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


class DavClient:
    def __init__(self, base_url: str, username: str, password: str, logger: logging.Logger):
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.auth = "Basic " + token
        self.logger = logger

    def url(self, rel: str = "") -> str:
        return self.base_url + ("/" + quote_path(rel.strip("/")) if rel.strip("/") else "")

    def request(self, method: str, rel: str = "", data: bytes | None = None, headers: dict[str, str] | None = None, expected: Iterable[int] = (200, 201, 204, 207)) -> tuple[int, bytes, dict[str, str]]:
        hdr = {"Authorization": self.auth, "User-Agent": "viewer-sustech-sync/1"}
        if headers:
            hdr.update(headers)
        req = urllib.request.Request(self.url(rel), data=data, headers=hdr, method=method)
        last: Exception | None = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120) as response:
                    body = response.read()
                    if response.status not in expected:
                        raise SyncError(f"Unexpected DAV status {response.status} for {method} {rel}")
                    return response.status, body, dict(response.headers)
            except urllib.error.HTTPError as exc:
                if exc.code in expected:
                    return exc.code, exc.read(), dict(exc.headers)
                if exc.code in (401, 403):
                    raise SyncError(f"WebDAV authentication/permission failed ({exc.code})") from exc
                if exc.code == 507:
                    raise SyncError("Cloudreve capacity is insufficient (507)") from exc
                last = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            if attempt < 3:
                time.sleep((1, 3, 10)[attempt])
        raise SyncError(f"WebDAV request failed: {method} {rel}: {last}")

    def list_tree(self, rel: str) -> dict[str, dict[str, object]]:
        body = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/><d:getetag/></d:prop></d:propfind>'
        _, xml, _ = self.request("PROPFIND", rel, body, {"Depth": "infinity", "Content-Type": "application/xml"}, (207,))
        root = ET.fromstring(xml)
        result: dict[str, dict[str, object]] = {}
        base_path = urllib.parse.urlsplit(self.url(rel)).path.rstrip("/")
        for response in root.findall(f"{{{DAV_NS}}}response"):
            href = response.findtext(f"{{{DAV_NS}}}href") or ""
            path = urllib.parse.unquote(urllib.parse.urlsplit(href).path).rstrip("/")
            if path == base_path:
                continue
            if not path.startswith(base_path + "/"):
                continue
            item = path[len(base_path) + 1 :]
            prop = response.find(f".//{{{DAV_NS}}}prop")
            if prop is None:
                continue
            is_dir = prop.find(f"{{{DAV_NS}}}resourcetype/{{{DAV_NS}}}collection") is not None
            result[item] = {
                "dir": is_dir,
                "size": int(prop.findtext(f"{{{DAV_NS}}}getcontentlength") or 0),
                "etag": (prop.findtext(f"{{{DAV_NS}}}getetag") or "").strip('"'),
            }
        return result

    def ensure_dir(self, rel: str) -> None:
        current = ""
        for part in PurePosixPath(rel).parts:
            current = f"{current}/{part}".strip("/")
            # Cloudreve v4 may return 200 when MKCOL targets an existing directory.
            self.request("MKCOL", current, expected=(200, 201, 405))

    def put(self, rel: str, path: Path | None = None, data: bytes | None = None) -> str:
        if data is None:
            assert path is not None
            data = path.read_bytes()
        mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        _, _, headers = self.request("PUT", rel, data, {"Content-Type": mime}, (200, 201, 204))
        return headers.get("ETag", "").strip('"')

    def delete(self, rel: str) -> None:
        self.request("DELETE", rel, expected=(200, 202, 204, 404))

    def get(self, rel: str) -> bytes:
        return self.request("GET", rel, expected=(200,))[1]


def run_sync(pub: Publisher, dry_run: bool) -> int:
    report: dict[str, object] = {"version": VERSION, "mode": "dry-run" if dry_run else "sync", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        local = pub.build()
        username, password = pub.load_credentials()
        dav = DavClient(pub.site_url + pub.dav_path, username, password, pub.logger)
        dav.request("PROPFIND", "", b"", {"Depth": "0"}, (207,))
        try:
            remote = dav.list_tree(pub.remote_root)
        except SyncError as exc:
            if "404" not in str(exc):
                raise
            remote = {}
        marker_rel = f"{pub.remote_root}/{pub.marker_name}"
        marker_ok = False
        if pub.marker_name in remote and not remote[pub.marker_name]["dir"]:
            marker_ok = dav.get(marker_rel).decode("utf-8", "replace").strip() == pub.marker_value
        uploads: list[str] = []
        for rel, meta in local.items():
            item = remote.get(rel)
            if item is None or item["dir"] or int(item["size"]) != int(meta["size"]):
                uploads.append(rel)
                continue
            # ETag is recorded after a successful upload. A changed ETag forces restoration.
            state = {}
            if pub.state_file.exists():
                try:
                    state = json.loads(pub.state_file.read_text(encoding="utf-8")).get("files", {})
                except Exception:
                    state = {}
            previous = state.get(rel, {})
            if previous.get("sha256") != meta["sha256"] or (previous.get("etag") and previous.get("etag") != item.get("etag")):
                uploads.append(rel)
        protected = {pub.marker_name}
        deletes = sorted((p for p, x in remote.items() if not x["dir"] and p not in local and p not in protected), key=lambda x: x.count("/"), reverse=True)
        delete_dirs = sorted((p for p, x in remote.items() if x["dir"] and not any(k == p or k.startswith(p + "/") for k in local)), key=lambda x: x.count("/"), reverse=True)
        report.update({"local_files": len(local), "upload": uploads, "delete": deletes, "delete_dirs": delete_dirs, "marker_ok": marker_ok})
        if dry_run:
            pub.logger.info("Dry-run: %d local, %d uploads, %d file deletes, %d directory deletes", len(local), len(uploads), len(deletes), len(delete_dirs))
            pub.write_report(report)
            return 0
        dav.ensure_dir(pub.remote_root)
        if not marker_ok:
            if remote and pub.marker_name not in remote:
                raise SyncError("Remote root is not empty and has no valid safety marker; refusing to sync/delete")
            dav.put(marker_rel, data=(pub.marker_value + "\n").encode())
            marker_ok = True
        dirs = sorted({str(PurePosixPath(rel).parent) for rel in local if str(PurePosixPath(rel).parent) != "."}, key=lambda x: x.count("/"))
        for directory in dirs:
            dav.ensure_dir(f"{pub.remote_root}/{directory}")
        new_state: dict[str, dict[str, object]] = {}
        total_uploads = len(uploads)
        upload_index = 0
        for rel, meta in local.items():
            if rel in uploads:
                upload_index += 1
                pub.logger.info("Uploading %d/%d %s", upload_index, total_uploads, rel)
                etag = dav.put(f"{pub.remote_root}/{rel}", path=meta["path"])
            else:
                etag = str(remote.get(rel, {}).get("etag", ""))
            new_state[rel] = {"sha256": meta["sha256"], "size": meta["size"], "etag": etag}
        if not marker_ok:
            raise SyncError("Safety marker validation failed; refusing remote deletion")
        for rel in deletes:
            pub.logger.info("Deleting file %s", rel)
            dav.delete(f"{pub.remote_root}/{rel}")
        for rel in delete_dirs:
            pub.logger.info("Deleting directory %s", rel)
            dav.delete(f"{pub.remote_root}/{rel}")
        pub.state_file.parent.mkdir(parents=True, exist_ok=True)
        pub.state_file.write_text(json.dumps({"version": VERSION, "files": new_state}, ensure_ascii=False, indent=2), encoding="utf-8")
        report["success"] = True
        pub.write_report(report)
        pub.logger.info("Sync complete: %d uploads, %d files deleted", len(uploads), len(deletes))
        return 0
    except Exception as exc:
        report["success"] = False
        report["error"] = str(exc)
        pub.write_report(report)
        pub.logger.exception("Sync failed: %s", exc)
        return 1


def status(pub: Publisher) -> int:
    if pub.report_file.exists():
        print(pub.report_file.read_text(encoding="utf-8"))
        return 0
    print("No sync report exists yet.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("sync-config.toml"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--sync", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--set-credentials", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--password-env", default="VIEWER_DAV_PASSWORD")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    pub = Publisher(args.config)
    pub.setup_logging(args.verbose)
    if args.set_credentials:
        username = args.username or input("WebDAV username: ").strip()
        password = os.environ.get(args.password_env) or getpass.getpass("WebDAV password: ")
        if not username or not password:
            raise SyncError("Username and password are required")
        pub.save_credentials(username, password)
        return 0
    if args.status:
        return status(pub)
    return run_sync(pub, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
