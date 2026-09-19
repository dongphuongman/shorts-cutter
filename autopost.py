#!/usr/bin/env python3
"""
autopost.py - Cat clip tu video da render roi HEN GIO dang tu dong.

Lich mac dinh (gio Viet Nam):
  YouTube        : 1 clip/ngay  -> 19:00
  Facebook       : 3 clip/ngay  -> 06:30, 12:00, 20:30

Dung lich HEN GIO CUA CHINH NEN TANG (YouTube publishAt / Facebook
scheduled_publish_time) nen may khong can bat.

  python3 autopost.py build   --days 7            # cat clip + dung hang doi
  python3 autopost.py plan                        # xem lich, khong dang
  python3 autopost.py push    --days 3            # day len that
  python3 autopost.py status                      # da dang nhung gi
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cut_shorts as cs

HERE = Path(__file__).resolve().parent
D2V = Path("/Users/dongman/Downloads/files/douyin2vietsub_extracted/douyin2vietsub")

CLIENT_SECRETS = D2V / "client_secrets.json"
YT_TOKEN_FILE = D2V / "yt_token.hosotunhien.json"
STATE_FILE = D2V / "state.hosotunhien.json"
META_DIR = D2V / "work/hosotunhien/meta"
SRC_DIR = D2V / "output/hosotunhien"

CLIP_DIR = HERE / "clips"
QUEUE = HERE / "queue.json"
POSTED = HERE / "posted.json"
QUOTA = HERE / "yt_quota.json"   # so luot tai len YouTube theo ngay

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Quota YouTube reset luc NUA DEM GIO THAI BINH DUONG, khong phai gio VN.
# Chenh nhau 14 tieng: 00:00 PT = 14:00 VN. Dem theo ngay VN thi sang som
# hom sau se tuong con quota trong khi thuc te chua reset -> an 403.
TZ_QUOTA = ZoneInfo("America/Los_Angeles")
FB_PAGE_ID = "1292439780625288"          # Ho So Tu Nhien
GRAPH = "v21.0"

YT_SLOTS = [(19, 0)]                       # 1 Short/ngay
FB_SLOTS = [(6, 30), (12, 0), (20, 30)]    # 3 Reels/ngay
YT_DAILY_UPLOAD_CAP = 6                    # quota 10.000 / 1.600 moi lan

# Nguon Douyin ghi chet phu de tieng Trung vao dai day va dai chu canh bao doc
# ben phai. Do san trong channels/hosotunhien.yaml: day 140px ~ 0.13 tren khung
# 1920x1080, phai 100px ~ 0.05. Khong cat thi Short ra chu Trung chay vao hinh.
# Do tu dong tren tung nguon (xem detect_band.py). Nguon khong dong nhat:
# co cai khung ngang thuan, co cai da ghep san thanh khung doc voi noi dung
# that chi la dai ngang o giua - ty le co dinh khong the dung cho ca hai.
# KHONG chuyen khung nua: giu nguyen khung ngang cua nguon, chi cat ria nhiem
# chu Trung. Nho vay chuoi filter khong con phep co gian nao - do la tran chat
# luong lay duoc tu nguon nay.
CRF = 19
PRESET = "medium"

_CROP_CACHE = {}


def crop_for(src_file):
    key = str(src_file)
    if key not in _CROP_CACHE:
        from detect_band import detect_crop
        _CROP_CACHE[key] = detect_crop(src_file)
    return _CROP_CACHE[key]


def jload(p, d=None):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else d


def jsave(p, o):
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- xac thuc

_yt_cache = {}


def yt_token():
    if _yt_cache.get("exp", 0) > time.time():
        return _yt_cache["tok"]
    cs_ = jload(CLIENT_SECRETS)
    if not cs_:
        sys.exit(f"[LOI] khong thay {CLIENT_SECRETS}")
    cs_ = cs_.get("installed") or cs_.get("web")
    tk = jload(YT_TOKEN_FILE)
    if not tk or not tk.get("refresh_token"):
        sys.exit(f"[LOI] khong thay refresh_token trong {YT_TOKEN_FILE}")
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "refresh_token": tk["refresh_token"], "client_id": cs_["client_id"],
        "client_secret": cs_["client_secret"], "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"[LOI] lam moi token YouTube that bai: {r.text[:300]}")
    d = r.json()
    _yt_cache.update(tok=d["access_token"], exp=time.time() + d.get("expires_in", 3600) - 60)
    return _yt_cache["tok"]


_fb_cache = {}


def fb_token():
    if "tok" in _fb_cache:
        return _fb_cache["tok"]
    user = os.environ.get("FB_PAGE_TOKEN")
    if not user:
        sys.exit("[LOI] chua co bien moi truong FB_PAGE_TOKEN")
    r = requests.get(f"https://graph.facebook.com/{GRAPH}/me/accounts",
                     params={"access_token": user, "fields": "id,name,access_token"}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"[LOI] /me/accounts that bai: {r.text[:300]}")
    for p in r.json().get("data", []):
        if p["id"] == FB_PAGE_ID:
            _fb_cache["tok"] = p["access_token"]
            _fb_cache["name"] = p["name"]
            return p["access_token"]
    sys.exit(f"[LOI] token khong quan ly duoc Page {FB_PAGE_ID}")


# ---------------------------------------------------------------- dung hang doi

def source_videos():
    """Video nguon da render, kem meta tieng Viet.

    Bo qua nguon trong exclude_sources.json: mot so video Douyin co do hoa chu
    Trung nam GIUA khung (the tieu de, so do) chu khong chi o dai phu de day -
    crop khong xu ly duoc, chi con cach khong dung.
    """
    state = jload(STATE_FILE, {}) or {}
    excluded = jload(HERE / "exclude_sources.json", {}) or {}
    out = []
    for vid, rec in state.items():
        if not isinstance(rec, dict) or vid in excluded:
            continue
        meta = jload(META_DIR / f"{vid}.json")
        if not meta:
            continue
        files = [Path(f) for f in rec.get("outputs", [])]
        files = [D2V / f if not f.is_absolute() else f for f in files]
        files = [f for f in files if f.exists() and f.stat().st_size > 1_000_000]
        if not files:
            continue
        out.append({"id": vid, "file": files[0], "meta": meta})
    return out


def chapter_of(meta, t):
    best = None
    for ch in meta.get("chapters") or []:
        if ch.get("t", 0) <= t:
            best = ch.get("label")
    return best


def clip_title(meta, start, idx):
    base = (meta.get("title") or "").strip()
    ch = chapter_of(meta, start)
    head = f"{ch}: {base}" if ch and ch.lower() not in ("mở đầu", "mo dau") else base
    head = head[:88].rstrip(" -—:")
    return head


def clip_desc(meta):
    d = (meta.get("hook") or meta.get("description") or "").strip()
    d = d[:600].rstrip()
    tags = meta.get("tags") or []
    hash_ = " ".join("#" + t.replace(" ", "") for t in tags[:6])
    return f"{d}\n\n{hash_}\n#dongvat #thiennhien".strip()


def cmd_build(args):
    CLIP_DIR.mkdir(exist_ok=True)
    queue = jload(QUEUE, []) or []
    have = {q["file"] for q in queue}
    posted = jload(POSTED, {}) or {}
    used_src = {p.get("src") for p in posted.values()}

    srcs = source_videos()
    random.Random(7).shuffle(srcs)
    per_day = len(YT_SLOTS) + len(FB_SLOTS)
    n_free = len([q for q in queue if q["file"] not in posted])
    need = len(queue) + max(0, args.days * per_day - n_free)
    print(f"\n{len(srcs)} video nguon | can {args.days * per_day} clip cho {args.days} ngay")
    print(f"Hang doi: {len(queue)} clip ({n_free} chua dang) -> can cat them "
          f"{max(0, args.days * per_day - n_free)}\n")

    made = 0
    for s in srcs:
        if len(queue) >= need:
            break
        if s["id"] in used_src and not args.force:
            continue
        try:
            info = cs.probe(s["file"])
        except SystemExit:
            continue
        if info["duration"] < args.min_len:
            continue
        _r = crop_for(s["file"])
        print(f"  cat {s['meta'].get('title','?')[:44]} "
              f"[giu {_r[0]:.2f}-{_r[1]:.2f}]", flush=True)
        vis = cs.analyze_visual(s["file"])
        if not vis:
            continue
        aud = cs.analyze_audio(s["file"]) if info["has_audio"] else []
        w = ({"vis_mean": .34, "vis_peak": .22, "aud_mean": .14, "aud_var": .30} if aud
             else {"vis_mean": .60, "vis_peak": .40, "aud_mean": 0, "aud_var": 0})
        cands = cs.build_candidates(vis, aud, info["duration"],
                                    args.min_len, args.max_len, 1.0, w)
        picks = cs.select(cands, args.per_video, 3.0, vis, info["duration"])
        for i, c in enumerate(picks, 1):
            out = CLIP_DIR / f"{s['id']}_s{i:02d}.mp4"
            if str(out) in have:
                continue
            if not out.exists():
                ok = cs.render(s["file"], c, out, None,
                               info["has_audio"], CRF, PRESET,
                               crop_for(s["file"]), info)
                if not ok:
                    continue
            queue.append({
                "file": str(out), "src": s["id"], "start": c["start"],
                "len": c["len"], "score": round(c["score"], 3),
                "title": clip_title(s["meta"], c["start"], i),
                "desc": clip_desc(s["meta"]),
                "tags": (s["meta"].get("tags") or [])[:15],
            })
            made += 1
            print(f"      + {out.name}  ({c['len']}s, diem {c['score']:.2f})")

    queue.sort(key=lambda q: -q["score"])
    # chong trung tieu de: cung mot chapter cat ra nhieu clip
    import collections as _c
    _seen = _c.Counter()
    for _q in queue:
        _seen[_q["title"]] += 1
        if _seen[_q["title"]] > 1:
            _core = _q["title"].rstrip(" -—:,.")
            _suf = f" (phần {_seen[_q['title']]})"
            _q["title"] = _core[:100 - len(_suf)].rstrip(" -—:,.") + _suf
    jsave(QUEUE, queue)
    print(f"\nDa them {made} clip. Hang doi: {len(queue)} clip -> {QUEUE.name}")
    print(f"Xem lich: python3 autopost.py plan\n")


# ---------------------------------------------------------------- lich

def slots(days, start_day=None):
    """Sinh danh sach (datetime_local, 'youtube'|'facebook')."""
    now = datetime.now(TZ)
    d0 = start_day or now.date()
    out = []
    for d in range(days):
        day = d0 + timedelta(days=d)
        for h, m in YT_SLOTS:
            out.append((datetime.combine(day, datetime.min.time(), TZ).replace(hour=h, minute=m), "youtube"))
        for h, m in FB_SLOTS:
            out.append((datetime.combine(day, datetime.min.time(), TZ).replace(hour=h, minute=m), "facebook"))
    # bo cac moc da qua / qua gan (Facebook doi toi thieu 10 phut)
    out = [(t, p) for t, p in out if t > now + timedelta(minutes=20)]
    out.sort()
    return out


def pkey(plat, f):
    """Khoa cua posted.json. Co ca nen tang vi MOT clip dang duoc o CA HAI noi."""
    return f"{plat}::{f}"


def assign(days, only=None):
    queue = jload(QUEUE, []) or []
    posted = jload(POSTED, {}) or {}
    taken = {(p.get("when"), p.get("platform")) for p in posted.values()}

    # Moi nen tang co hang doi RIENG. Cung mot clip dang duoc ca YouTube lan
    # Facebook - khan gia hai ben gan nhu khong trung nhau, va nhu vay moi tan
    # dung het cong cat. Truoc day clip dang o dau la coi nhu dung het, nen
    # Facebook (3 bai/ngay) nuot gan sach hang doi truoc khi YouTube kip lay.
    done = {}
    for v in posted.values():
        done.setdefault(v.get("platform"), set()).add(v.get("file", ""))
    free = {pl: [q for q in queue if q["file"] not in done.get(pl, set())]
            for pl in ("youtube", "facebook")}
    idx = {"youtube": 0, "facebook": 0}

    plan = []
    for when, plat in slots(days):
        if only and plat != only:
            continue
        if (when.isoformat(), plat) in taken:
            continue
        if idx[plat] >= len(free[plat]):
            continue
        plan.append({"when": when, "platform": plat, "clip": free[plat][idx[plat]]})
        idx[plat] += 1
    return plan, max(len(free["youtube"]), len(free["facebook"]))


def cmd_plan(args):
    plan, nfree = assign(args.days)
    if not plan:
        print("\nHang doi rong. Chay: python3 autopost.py build --days 7\n")
        return
    print(f"\nLich {args.days} ngay toi | {nfree} clip san sang\n")
    print(f"  {'Thoi diem (VN)':<18} {'Nen tang':<10} {'Dai':<6} Tieu de")
    print(f"  {'-'*18} {'-'*10} {'-'*6} {'-'*46}")
    cur = None
    for p in plan:
        d = p["when"].strftime("%d/%m")
        if d != cur:
            cur = d
            print(f"  --- {p['when'].strftime('%a %d/%m')} ---")
        print(f"  {p['when'].strftime('%d/%m %H:%M'):<18} "
              f"{'YouTube' if p['platform']=='youtube' else 'Facebook':<10} "
              f"{str(p['clip']['len'])+'s':<6} {p['clip']['title'][:46]}")
    ny = sum(1 for p in plan if p["platform"] == "youtube")
    print(f"\n  Tong: {len(plan)} bai ({ny} YouTube, {len(plan)-ny} Facebook)")
    print(f"  Quota YouTube: toi da {YT_DAILY_UPLOAD_CAP} lan tai len/ngay\n")


# ---------------------------------------------------------------- dang

def yt_push(clip, when_utc):
    tok = yt_token()
    p = Path(clip["file"])
    body = {"snippet": {"title": clip["title"][:100], "description": clip["desc"][:5000],
                        "tags": clip["tags"], "categoryId": "27"},
            "status": {"privacyStatus": "private",
                       "publishAt": when_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "selfDeclaredMadeForKids": False}}
    r = requests.post("https://www.googleapis.com/upload/youtube/v3/videos",
                      params={"uploadType": "resumable", "part": "snippet,status"},
                      headers={"Authorization": f"Bearer {tok}",
                               "Content-Type": "application/json; charset=UTF-8",
                               "X-Upload-Content-Length": str(p.stat().st_size),
                               "X-Upload-Content-Type": "video/mp4"},
                      data=json.dumps(body).encode(), timeout=60)
    if r.status_code not in (200, 201):
        return False, f"init [{r.status_code}] {r.text[:200]}"
    loc = r.headers.get("Location")
    with open(p, "rb") as f:
        up = requests.put(loc, data=f, headers={"Content-Type": "video/mp4"}, timeout=1800)
    if up.status_code not in (200, 201):
        return False, f"upload [{up.status_code}] {up.text[:200]}"
    return True, up.json().get("id")


def _fb_upload_chunked(tok, f, size):
    """Upload theo khoi cho file lon. Tra ve video_id hoac (None, loi).

    POST mot lan chet o 413 khi file vuot ~100 MB - do that: 83 MB qua duoc,
    137 MB thi khong. Nguon 4K cho ra clip 30 giay nang ~140 MB nen bat buoc
    phai co duong nay.
    """
    base = f"https://graph-video.facebook.com/{GRAPH}/{FB_PAGE_ID}/videos"
    r = requests.post(base, timeout=120, data={
        "access_token": tok, "upload_phase": "start", "file_size": str(size)})
    if r.status_code != 200:
        return None, f"start [{r.status_code}] {r.text[:200]}"
    d = r.json()
    sid, vid = d.get("upload_session_id"), d.get("video_id")
    so, eo = int(d.get("start_offset", 0)), int(d.get("end_offset", 0))
    if not sid:
        return None, f"start thieu upload_session_id: {r.text[:200]}"

    with open(f, "rb") as fh:
        while so < eo:
            fh.seek(so)
            chunk = fh.read(eo - so)
            t = requests.post(base, timeout=1800,
                              data={"access_token": tok, "upload_phase": "transfer",
                                    "upload_session_id": sid, "start_offset": str(so)},
                              files={"video_file_chunk": ("chunk", chunk,
                                                          "application/octet-stream")})
            if t.status_code != 200:
                return None, f"transfer @{so} [{t.status_code}] {t.text[:200]}"
            td = t.json()
            so, eo = int(td.get("start_offset", eo)), int(td.get("end_offset", eo))
    return (sid, vid), None


def fb_push(clip, when_local):
    """Dang len Page qua /{page}/videos, hen gio san.

    KHONG dung /video_reels: endpoint do chi nhan khung doc. Da do that - file
    1824x874 (2.08:1) qua duoc pha upload va processing nhung ket thuc o
    `video_status: error`, publishing_phase khong bao gio bat dau.

    Hen gio phai truyen NGAY LUC TAO: dat scheduled_publish_time sau do thi API
    tra "You only set a scheduled publish time on an unpublished post".
    """
    tok = fb_token()
    f = Path(clip["file"])
    size = f.stat().st_size
    base = f"https://graph-video.facebook.com/{GRAPH}/{FB_PAGE_ID}/videos"
    meta = {"access_token": tok,
            "description": f"{clip['title']}\n\n{clip['desc']}"[:2200],
            "published": "false",
            "scheduled_publish_time": str(int(when_local.timestamp()))}

    if size > 90 * 1024 * 1024:                   # tren nguong POST mot lan
        got, err = _fb_upload_chunked(tok, f, size)
        if err:
            return False, err
        sid, vid = got
        fin = requests.post(base, timeout=300,
                            data={**meta, "upload_phase": "finish",
                                  "upload_session_id": sid})
        if fin.status_code != 200:
            return False, f"finish [{fin.status_code}] {fin.text[:250]}"
        return True, vid or fin.json().get("video_id") or sid

    with open(f, "rb") as fh:
        r = requests.post(base, timeout=1800, data=meta,
                          files={"source": (f.name, fh, "video/mp4")})
    if r.status_code != 200:
        return False, f"upload [{r.status_code}] {r.text[:250]}"
    vid = r.json().get("id")
    if not vid:
        return False, f"khong nhan duoc video id: {r.text[:200]}"
    return True, vid


def yt_used_today(posted=None):
    """So lan tai len YouTube da dung HOM NAY.

    Doc tu so rieng chu khong tu posted.json: khi phai cat lai clip thi
    posted.json bi xoa, nhung quota da tieu thi khong tra lai - ke ca khi
    video da bi xoa khoi kenh. Dem nham se dam vao gioi han va an 403.
    """
    today = datetime.now(TZ_QUOTA).date().isoformat()
    return int((jload(QUOTA, {}) or {}).get(today, 0))


def yt_note_upload():
    today = datetime.now(TZ_QUOTA).date().isoformat()
    q = jload(QUOTA, {}) or {}
    q[today] = int(q.get(today, 0)) + 1
    jsave(QUOTA, q)


def cmd_push(args):
    plan, _ = assign(args.days, getattr(args, "only", None))
    if not plan:
        print("\nKhong co gi de dang. Chay build truoc.\n")
        return
    posted = jload(POSTED, {}) or {}
    yt_done = yt_used_today(posted)
    yt_left = YT_DAILY_UPLOAD_CAP - yt_done
    nyt = sum(1 for p in plan if p["platform"] == "youtube")
    print(f"\nDang day {len(plan)} bai len...")
    _pt = datetime.now(TZ_QUOTA).strftime("%d/%m %H:%M")
    print(f"Quota YouTube (ngay Thai Binh Duong {_pt}): da dung "
          f"{yt_done}/{YT_DAILY_UPLOAD_CAP}, con {yt_left} | ke hoach can {nyt}")
    if nyt > yt_left:
        print(f"-> {nyt - yt_left} Short se bi hoan, chay lai 'push' ngay mai la xong.")
    print()
    for p in plan:
        clip, plat, when = p["clip"], p["platform"], p["when"]
        if plat == "youtube" and yt_done >= YT_DAILY_UPLOAD_CAP:
            print(f"  {when.strftime('%d/%m %H:%M')} youtube  [HOAN] het quota hom nay")
            continue
        label = f"{when.strftime('%d/%m %H:%M')} {plat:<8}"
        print(f"  {label} {clip['title'][:48]}")
        if args.dry_run:
            print("           (dry-run)\n")
            continue
        if plat == "youtube":
            ok, info = yt_push(clip, when.astimezone(timezone.utc))
            yt_done += 1
            yt_note_upload()
        else:
            ok, info = fb_push(clip, when)
        if ok:
            posted[pkey(plat, clip["file"])] = {
                "platform": plat, "id": info, "src": clip["src"],
                "file": clip["file"], "when": when.isoformat(),
                "title": clip["title"], "uploaded_at": datetime.now(TZ).isoformat()}
            jsave(POSTED, posted)
            link = (f"https://youtu.be/{info}" if plat == "youtube"
                    else f"https://facebook.com/{info}")
            print(f"           OK  {link}\n")
        else:
            print(f"           LOI {info}\n")
        time.sleep(args.delay)
    print(f"Xong. Da luu vao {POSTED.name}\n")


def cmd_status(args):
    posted = jload(POSTED, {}) or {}
    queue = jload(QUEUE, []) or []
    done = {}
    for v in posted.values():
        done.setdefault(v.get("platform"), set()).add(v.get("file", ""))
    yt = len([q for q in queue if q["file"] not in done.get("youtube", set())])
    fb = len([q for q in queue if q["file"] not in done.get("facebook", set())])
    print(f"\nHang doi : {len(queue)} clip | chua dang: YouTube {yt}, Facebook {fb}")
    print(f"Da dang  : {len(posted)} bai\n")
    for f, p in sorted(posted.items(), key=lambda kv: kv[1]["when"]):
        print(f"  {p['when'][:16]}  {p['platform']:<9} {p['id']:<16} {p['title'][:44]}")
    print()


def main():
    ap = argparse.ArgumentParser(description="Cat clip va hen gio dang tu dong.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Cat clip va dung hang doi")
    b.add_argument("--days", type=int, default=7)
    b.add_argument("--per-video", type=int, default=4)
    b.add_argument("--min-len", type=int, default=20)
    b.add_argument("--max-len", type=int, default=40)
    b.add_argument("--force", action="store_true", help="Cat ca video da tung dung")
    b.set_defaults(fn=cmd_build)

    p = sub.add_parser("plan", help="Xem lich du kien")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(fn=cmd_plan)

    u = sub.add_parser("push", help="Day len va hen gio that")
    u.add_argument("--days", type=int, default=3)
    u.add_argument("--delay", type=float, default=15)
    u.add_argument("--only", choices=["youtube", "facebook"],
                   help="Chi day mot nen tang. Dung khi slot nen tang kia bi hoan "
                        "vi het quota - neu khong chung van an mat clip trong hang doi.")
    u.add_argument("--dry-run", action="store_true")
    u.set_defaults(fn=cmd_push)

    s = sub.add_parser("status", help="Xem da dang nhung gi")
    s.set_defaults(fn=cmd_status)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
