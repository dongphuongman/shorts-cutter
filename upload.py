#!/usr/bin/env python3
"""
upload.py - Dang Shorts len YouTube va Reels len Facebook Page.

Chi dung 'requests' + thu vien chuan. Khong can google-api-python-client.

Dung:
  python3 upload.py auth-youtube                     # cap quyen YouTube (1 lan)
  python3 upload.py check                            # kiem tra cau hinh
  python3 upload.py --from-report .../report.json --platform both
  python3 upload.py clip.mp4 --title "..." --platform youtube
"""

import argparse
import http.server
import json
import mimetypes
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

try:
    import requests
except ImportError:
    print("[LOI] Thieu 'requests'. Cai: python3 -m pip install --user requests", file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
TOKENS = HERE / ".tokens.json"

YT_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YT_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
YT_TOKEN = "https://oauth2.googleapis.com/token"
YT_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"


class AuthError(Exception):
    pass


def die(msg):
    sys.stdout.flush()
    print(f"[LOI] {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(path, default=None):
    if not Path(path).exists():
        return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_tokens(data):
    TOKENS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(TOKENS, 0o600)


def load_config():
    cfg = load_json(CONFIG)
    if cfg is None:
        die(f"Chua co {CONFIG.name}. Copy config.example.json -> config.json roi dien thong tin.")
    return cfg


# ================================================================ YouTube OAuth

class _Handler(http.server.BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Handler.result = {k: v[0] for k, v in q.items()}
        ok = "code" in _Handler.result
        body = ("<h2>Xong. Quay lai terminal.</h2>" if ok
                else f"<h2>That bai</h2><p>{_Handler.result.get('error','')}</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body style='font-family:sans-serif;padding:40px'>{body}</body></html>"
                         .encode("utf-8"))

    def log_message(self, *a):
        pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def yt_auth(cfg):
    yt = cfg.get("youtube", {})
    cid, csec = yt.get("client_id"), yt.get("client_secret")
    if not cid or not csec:
        die("Thieu youtube.client_id / client_secret trong config.json. Xem README muc 'Lay khoa YouTube'.")

    port = free_port()
    redirect = f"http://127.0.0.1:{port}/"
    state = secrets.token_urlsafe(16)
    url = YT_AUTH + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": YT_SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
    })

    srv = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()

    print("\nMo trinh duyet de cap quyen YouTube...")
    print(f"Neu khong tu mo, dan link nay vao trinh duyet:\n\n{url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    for _ in range(300):
        if _Handler.result:
            break
        time.sleep(1)
    srv.server_close()

    res = _Handler.result
    if not res:
        die("Het thoi gian cho cap quyen.")
    if res.get("state") != state:
        die("State khong khop - huy vi ly do an toan.")
    if "code" not in res:
        die(f"Google tra ve loi: {res.get('error')}")

    r = requests.post(YT_TOKEN, data={
        "code": res["code"], "client_id": cid, "client_secret": csec,
        "redirect_uri": redirect, "grant_type": "authorization_code",
    }, timeout=30)
    if r.status_code != 200:
        die(f"Doi token that bai: {r.text[:400]}")
    tok = r.json()
    if "refresh_token" not in tok:
        die("Google khong tra refresh_token. Vao myaccount.google.com/permissions go quyen cua app roi thu lai.")

    t = load_json(TOKENS, {}) or {}
    t["youtube"] = {"refresh_token": tok["refresh_token"],
                    "access_token": tok.get("access_token"),
                    "expires_at": time.time() + tok.get("expires_in", 3600) - 60}
    save_tokens(t)
    print(f"Da luu token vao {TOKENS.name}. Khong can chay lai buoc nay nua.\n")


def yt_access_token(cfg):
    t = load_json(TOKENS, {}) or {}
    ytt = t.get("youtube")
    if not ytt:
        raise AuthError("chua cap quyen YouTube -> chay: python3 upload.py auth-youtube")
    if ytt.get("access_token") and ytt.get("expires_at", 0) > time.time():
        return ytt["access_token"]

    yt = cfg.get("youtube") or {}
    if not yt.get("client_id") or not yt.get("client_secret"):
        raise AuthError("thieu youtube.client_id / client_secret trong config.json")
    r = requests.post(YT_TOKEN, data={
        "refresh_token": ytt["refresh_token"], "client_id": yt["client_id"],
        "client_secret": yt["client_secret"], "grant_type": "refresh_token",
    }, timeout=30)
    if r.status_code != 200:
        raise AuthError(f"lam moi token that bai [{r.status_code}] -> chay lai: python3 upload.py auth-youtube")
    d = r.json()
    ytt["access_token"] = d["access_token"]
    ytt["expires_at"] = time.time() + d.get("expires_in", 3600) - 60
    t["youtube"] = ytt
    save_tokens(t)
    return ytt["access_token"]


def yt_upload(cfg, path, title, desc, tags, privacy, made_for_kids, publish_at=None):
    try:
        token = yt_access_token(cfg)
    except AuthError as e:
        return False, str(e)
    size = path.stat().st_size
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"

    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(made_for_kids)}
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at

    meta = {
        "snippet": {
            "title": title[:100],
            "description": desc[:5000],
            "tags": tags[:60],
            "categoryId": str(cfg.get("youtube", {}).get("category_id", 15)),
        },
        "status": status,
    }

    r = requests.post(
        YT_UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Length": str(size),
                 "X-Upload-Content-Type": mime},
        data=json.dumps(meta).encode("utf-8"), timeout=60)
    if r.status_code not in (200, 201):
        return False, f"khoi tao that bai [{r.status_code}] {r.text[:300]}"
    session_url = r.headers.get("Location")
    if not session_url:
        return False, "khong nhan duoc Location de upload"

    with open(path, "rb") as f:
        up = requests.put(session_url, data=f,
                          headers={"Content-Type": mime, "Content-Length": str(size)},
                          timeout=900)
    if up.status_code not in (200, 201):
        return False, f"upload that bai [{up.status_code}] {up.text[:300]}"
    vid = up.json().get("id")
    return True, vid


# ================================================================ Facebook Reels

def fb_upload(cfg, path, description, published=True):
    fb = cfg.get("facebook", {})
    page_id, token = fb.get("page_id"), fb.get("page_access_token")
    ver = fb.get("graph_version", "v23.0")
    if not page_id or not token:
        return False, "thieu facebook.page_id / page_access_token trong config.json"

    base = f"https://graph.facebook.com/{ver}/{page_id}/video_reels"

    r = requests.post(base, data={"upload_phase": "start", "access_token": token}, timeout=60)
    if r.status_code != 200:
        return False, f"start that bai [{r.status_code}] {r.text[:300]}"
    d = r.json()
    video_id, upload_url = d.get("video_id"), d.get("upload_url")
    if not video_id or not upload_url:
        return False, f"start thieu du lieu: {r.text[:300]}"

    size = path.stat().st_size
    with open(path, "rb") as f:
        up = requests.post(upload_url, data=f, timeout=900, headers={
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(size),
            "Content-Type": "application/octet-stream",
        })
    if up.status_code != 200:
        return False, f"upload that bai [{up.status_code}] {up.text[:300]}"

    time.sleep(2)
    fin = requests.post(base, data={
        "upload_phase": "finish", "video_id": video_id,
        "video_state": "PUBLISHED" if published else "DRAFT",
        "description": description[:2200], "access_token": token,
    }, timeout=120)
    if fin.status_code != 200:
        return False, f"finish that bai [{fin.status_code}] {fin.text[:300]}"
    return True, video_id


# ================================================================ helpers

def _filled(v):
    return bool(v) and "DIEN" not in str(v).upper()


def check(cfg):
    print("\nKiem tra cau hinh:\n")
    yt = cfg.get("youtube", {})
    ok_yt = _filled(yt.get("client_id")) and _filled(yt.get("client_secret"))
    tok = (load_json(TOKENS, {}) or {}).get("youtube")
    print(f"  YouTube  khoa API      : {'co' if ok_yt else 'THIEU'}")
    print(f"  YouTube  da cap quyen  : {'roi' if tok else 'CHUA -> chay: python3 upload.py auth-youtube'}")
    fb = cfg.get("facebook", {})
    print(f"  Facebook page_id       : {'co' if _filled(fb.get('page_id')) else 'THIEU'}")
    print(f"  Facebook page token    : {'co' if _filled(fb.get('page_access_token')) else 'THIEU'}")
    print(f"  Graph version          : {fb.get('graph_version','v23.0')}")
    if _filled(fb.get("page_access_token")):
        try:
            r = requests.get(f"https://graph.facebook.com/{fb.get('graph_version','v23.0')}/me",
                             params={"access_token": fb["page_access_token"]}, timeout=20)
            print(f"  Facebook token song    : {'OK - ' + r.json().get('name','?') if r.status_code == 200 else 'HONG: ' + r.text[:120]}")
        except Exception as e:
            print(f"  Facebook token song    : khong kiem tra duoc ({e})")
    print()


def collect(args):
    """Tra ve list (path, index)."""
    if args.from_report:
        rep = load_json(args.from_report)
        if not rep:
            die(f"Khong doc duoc {args.from_report}")
        out = []
        for i, c in enumerate(rep.get("clips", []), 1):
            if c.get("ok") and Path(c["file"]).exists():
                out.append((Path(c["file"]), i))
        if not out:
            die("Report khong co clip hop le.")
        return out
    if not args.files:
        die("Can truyen file video hoac --from-report")
    return [(Path(f).expanduser().resolve(), i) for i, f in enumerate(args.files, 1)]


def render_text(tpl, path, i, fallback=""):
    if not tpl:
        return fallback
    return (tpl.replace("{i}", str(i))
               .replace("{n}", f"{i:02d}")
               .replace("{stem}", path.stem)
               .replace("{name}", path.name))


def main():
    ap = argparse.ArgumentParser(description="Dang Shorts len YouTube va Reels len Facebook Page.")
    ap.add_argument("files", nargs="*", help="File video, hoac dung --from-report")
    ap.add_argument("--from-report", help="report.json do cut_shorts.py sinh ra")
    ap.add_argument("--platform", choices=["youtube", "facebook", "both"], default="both")
    ap.add_argument("--title", help="Tieu de. Ho tro {i} {n} {stem}")
    ap.add_argument("--desc", help="Mo ta. Ho tro {i} {n} {stem}")
    ap.add_argument("--tags", default="", help="The, cach nhau bang dau phay")
    ap.add_argument("--privacy", choices=["public", "unlisted", "private"], default="private",
                    help="Quyen rieng tu YouTube (mac dinh private - xem README ve gioi han API)")
    ap.add_argument("--made-for-kids", action="store_true", help="Danh cho tre em")
    ap.add_argument("--publish-at", help="Hen gio YouTube, dinh dang ISO 2026-09-25T09:00:00Z")
    ap.add_argument("--fb-draft", action="store_true", help="Dua Reels vao nhap thay vi dang ngay")
    ap.add_argument("--delay", type=float, default=30, help="Nghi giua cac lan dang, giay")
    ap.add_argument("--dry-run", action="store_true")
    args, extra = ap.parse_known_args()

    # ho tro lenh phu: auth-youtube / check
    sub = None
    for tok in list(args.files) + extra:
        if tok in ("auth-youtube", "check"):
            sub = tok
            if tok in args.files:
                args.files.remove(tok)

    cfg = load_config()
    if sub == "auth-youtube":
        return yt_auth(cfg)
    if sub == "check":
        return check(cfg)

    items = collect(args)
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    d = cfg.get("defaults", {})

    print(f"\n{len(items)} clip | nen tang: {args.platform} | YouTube privacy: {args.privacy}\n")

    results = []
    for idx, (path, i) in enumerate(items):
        title = render_text(args.title or d.get("title"), path, i, path.stem)
        desc = render_text(args.desc or d.get("description"), path, i, "")
        use_tags = tags or d.get("tags", [])

        print(f"[{idx+1}/{len(items)}] {path.name}")
        print(f"    tieu de: {title}")

        if args.dry_run:
            print("    (dry-run: khong dang)\n")
            continue

        if args.platform in ("youtube", "both"):
            ok, info = yt_upload(cfg, path, title, desc, use_tags,
                                 args.privacy, args.made_for_kids, args.publish_at)
            print(f"    YouTube : {'OK https://youtube.com/watch?v=' + info if ok else 'LOI ' + info}")
            results.append({"file": path.name, "platform": "youtube", "ok": ok, "info": info})

        if args.platform in ("facebook", "both"):
            ok, info = fb_upload(cfg, path, desc or title, published=not args.fb_draft)
            print(f"    Facebook: {'OK video_id=' + info if ok else 'LOI ' + info}")
            results.append({"file": path.name, "platform": "facebook", "ok": ok, "info": info})

        print()
        if idx < len(items) - 1 and args.delay > 0:
            time.sleep(args.delay)

    if results:
        good = sum(1 for r in results if r["ok"])
        print(f"Ket qua: {good}/{len(results)} lan dang thanh cong.")
        log = HERE / "upload_log.json"
        old = load_json(log, []) or []
        old.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results})
        log.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Nhat ky: {log}")


if __name__ == "__main__":
    main()
