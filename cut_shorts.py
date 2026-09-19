#!/usr/bin/env python3
"""
cut_shorts.py - Tu dong cat Shorts/Reels doc tu video ngang.

Phan tich 2 tin hieu de tim "khoanh khac manh":
  1. Visual change (scdet)  -> chuyen dong + cat canh
  2. Audio loudness (ebur128) -> nang luong + bien thien am thanh

Chi can ffmpeg/ffprobe. Khong can thu vien ngoai.
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def die(msg):
    print(f"[LOI] {msg}", file=sys.stderr)
    sys.exit(1)


def need(tool):
    if not shutil.which(tool):
        die(f"Khong tim thay '{tool}'. Cai bang: brew install ffmpeg")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


# ---------------------------------------------------------------- probe

def probe(path):
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    if p.returncode != 0:
        die(f"ffprobe that bai: {p.stderr.strip()[:400]}")
    data = json.loads(p.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if not v:
        die("File khong co luong video.")
    dur = float(data["format"].get("duration") or v.get("duration") or 0)
    if dur <= 0:
        die("Khong doc duoc thoi luong video.")
    def _rate(key, fallback="30/1"):
        num, den = (v.get(key) or fallback).split("/")
        den = float(den or 1)
        return float(num) / den if den else 0.0

    fps = _rate("r_frame_rate")
    avg = _rate("avg_frame_rate") or fps
    return {
        "path": str(path),
        "duration": dur,
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": fps,
        # avg khac r nhieu = nguon VFR. Vd 7680522415873506469 khai r=60/1
        # nhung avg chi ~39.95 - khong bat duoc thi xuat ra file gap khuc.
        "avg_fps": avg,
        "has_audio": a is not None,
        "color_space": (v.get("color_space") or "").lower(),
        "color_primaries": (v.get("color_primaries") or "").lower(),
        "color_transfer": (v.get("color_transfer") or "").lower(),
        "color_range": (v.get("color_range") or "").lower(),
    }


# ---------------------------------------------------------------- khung hinh

def _frame_count(path, start, dur, vf=None):
    cmd = ["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(dur),
           "-i", str(path), "-an"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-f", "null", os.devnull]
    m = re.findall(r"frame=\s*(\d+)", run(cmd).stderr)
    return int(m[-1]) if m else 0


def unique_frame_ratio(path, start=5.0, dur=8.0):
    """Ty le khung THAT SU khac nhau. Nguon 60fps danh nghia thuong chi mang
    ~25 khung doc lap moi giay - giu 60fps cho no la gap doi dung luong de
    chep khung trung."""
    total = _frame_count(path, start, dur)
    if not total:
        return 1.0
    uniq = _frame_count(path, start, dur, "mpdecimate")
    return uniq / total


def target_fps(info):
    """Khung hinh dau ra. Ep 30 mu quang lam hong ca hai phia: nguon 31fps mat
    ~1 khung/giay, nguon 25fps bi chen 5 khung trung/giay."""
    r = info.get("fps") or 30.0
    a = info.get("avg_fps") or r
    if r > 0 and abs(r - a) / r > 0.02:          # VFR -> ep ve CFR
        return 60.0 if a > 45 else 30.0
    if r > 32:                                    # 50/60 danh nghia: do that
        return 60.0 if unique_frame_ratio(info["path"]) > 0.75 else 30.0
    return round(r, 3)                            # 25, 29.97, 30, 31 -> giu nguyen


def color_args(info):
    """Tra ve (filter_chuyen_mau, co_gan_the).

    KHONG gan bt709 hang loat: mot nguon trong kho la BT.601 (bt470bg/
    smpte170m), gan bua se lam lech mau dung cai muon tranh.
    """
    tag = ["-colorspace", "bt709", "-color_primaries", "bt709",
           "-color_trc", "bt709", "-color_range", "tv"]
    cs, cp, ct = info.get("color_space"), info.get("color_primaries"), info.get("color_transfer")

    if cs == "bt709" and cp == "bt709" and ct == "bt709":
        return "", tag                            # dung roi, chi gan the

    if not cs and not cp and not ct:
        if info["height"] >= 720:
            print("  [mau] nguon khong gan the, cao >=720 -> gia dinh bt709")
            return "", tag
        print(f"  [mau] nguon khong gan the, cao {info['height']} -> gia dinh "
              "smpte170m, chuyen sang bt709")
        return ("zscale=matrixin=170m:transferin=smpte170m:primariesin=170m:"
                "rangein=limited:matrix=709:transfer=709:primaries=709:"
                "range=limited:dither=error_diffusion,"), tag

    # Co the nhung khong phai bt709. De zscale tu doc thuoc tinh khung hinh
    # thay vi tu anh xa ten - an toan hon nhieu.
    print(f"  [mau] nguon {cs or '?'}/{ct or '?'}/{cp or '?'} -> chuyen sang bt709")
    return ("zscale=matrix=709:transfer=709:primaries=709:range=limited:"
            "dither=error_diffusion,"), tag


# ---------------------------------------------------------------- phan tich

def analyze_visual(path, sample_fps=4):
    """Tra ve list (time, score) - do thay doi hinh anh."""
    cmd = ["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), "-an",
           "-vf", f"fps={sample_fps},scdet=threshold=0,"
                  "metadata=print:key=lavfi.scd.score:file=-",
           "-f", "null", os.devnull]
    p = run(cmd)
    out = []
    t = None
    for line in p.stdout.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.scd\.score=([\d.eE+-]+)", line)
        if m and t is not None:
            try:
                out.append((t, float(m.group(1))))
            except ValueError:
                pass
    return out


def analyze_audio(path):
    """Tra ve list (time, loudness_LUFS) tu ebur128 momentary."""
    cmd = ["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), "-vn",
           "-filter_complex", "ebur128=metadata=1", "-f", "null", os.devnull]
    p = run(cmd)
    out = []
    for line in p.stderr.splitlines():
        m = re.search(r"t:\s*([\d.]+).*?M:\s*(-?[\d.]+|-inf)", line)
        if m:
            val = m.group(2)
            lufs = -70.0 if val == "-inf" else float(val)
            out.append((float(m.group(1)), max(lufs, -70.0)))
    return out


# ---------------------------------------------------------------- cham diem

def series_slice(series, start, end):
    return [v for t, v in series if start <= t < end]


def pct_rank(values):
    """Chuyen list so -> percentile rank 0..1 (chong outlier)."""
    n = len(values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: values[i])
    rank = [0.0] * n
    for pos, i in enumerate(order):
        rank[i] = pos / (n - 1) if n > 1 else 1.0
    return rank


def build_candidates(vis, aud, duration, lo, hi, step, weights):
    lengths = sorted({lo, (lo + hi) // 2, hi} | set(range(lo, hi + 1, 5)))
    lengths = [L for L in lengths if lo <= L <= hi]
    cands = []
    t = 0.0
    while t + lo <= duration:
        for L in lengths:
            if t + L > duration:
                continue
            vs = series_slice(vis, t, t + L)
            as_ = series_slice(aud, t, t + L)
            if len(vs) < 3:
                continue
            cands.append({
                "start": round(t, 2),
                "len": L,
                "_vis_mean": statistics.fmean(vs),
                "_vis_peak": max(vs),
                "_aud_mean": statistics.fmean(as_) if as_ else -70.0,
                "_aud_var": statistics.pstdev(as_) if len(as_) > 1 else 0.0,
            })
        t += step

    if not cands:
        return []

    ranks = {k: pct_rank([c[k] for c in cands])
             for k in ("_vis_mean", "_vis_peak", "_aud_mean", "_aud_var")}

    for i, c in enumerate(cands):
        base = (weights["vis_mean"] * ranks["_vis_mean"][i]
                + weights["vis_peak"] * ranks["_vis_peak"][i]
                + weights["aud_mean"] * ranks["_aud_mean"][i]
                + weights["aud_var"] * ranks["_aud_var"][i])
        # nhich nhe cho do dai gan 30s - vung ngot cua Shorts
        c["score"] = base * (1 + 0.10 * (1 - min(abs(c["len"] - 30) / 15.0, 1.0)))
        for k in list(c):
            if k.startswith("_"):
                del c[k]
    return cands


def snap_to_cut(start, vis, window=2.5):
    """Dich diem bat dau ve ngay sau mot cu chuyen canh manh gan do."""
    if not vis:
        return start
    near = [(t, s) for t, s in vis if abs(t - start) <= window]
    if not near:
        return start
    thr = statistics.fmean([s for _, s in vis]) * 2.2
    peaks = [t for t, s in near if s >= thr]
    if not peaks:
        return start
    best = min(peaks, key=lambda t: abs(t - start))
    return round(max(best + 0.08, 0.0), 2)


def select(cands, count, min_gap, vis, duration):
    picked = []
    for c in sorted(cands, key=lambda x: -x["score"]):
        if len(picked) >= count:
            break
        s, e = c["start"], c["start"] + c["len"]
        if any(not (e + min_gap <= p["start"] or s >= p["start"] + p["len"] + min_gap)
               for p in picked):
            continue
        c = dict(c)
        snapped = snap_to_cut(c["start"], vis)
        if snapped + c["len"] <= duration:
            c["start"] = snapped
        picked.append(c)
    return sorted(picked, key=lambda c: c["start"])


# ---------------------------------------------------------------- render

def find_font():
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return f
    return None


def esc_drawtext(s):
    return (s.replace("\\", "\\\\").replace(":", "\\:")
             .replace("'", "\u2019").replace("%", "\\%"))


def build_vf(crop_rect=None, title=None, color_pre="", fps_pre=""):
    """Giu nguyen khung ngang cua nguon. Chi cat ria nhiem chu Trung.

    Khong scale, khong pad, khong ghep khung doc - nen KHONG CO PHEP CO GIAN
    nao trong chuoi nay. Do la tran chat luong lay duoc tu nguon.
    """
    chain = fps_pre
    if crop_rect:
        top, bottom, right = crop_rect
        if top > 0 or bottom < 1.0 or right > 0:
            cw = f"trunc(iw*{1 - min(right, 0.5):.4f}/2)*2"
            ch = f"trunc(ih*{max(bottom - top, 0.05):.4f}/2)*2"
            cy = f"trunc(ih*{max(top, 0.0):.4f}/2)*2"
            chain += f"crop={cw}:{ch}:0:{cy},"
    # setsar ngay sau crop: mot so nguon co SAR le (vd 10240:10239)
    chain += "setsar=1,"
    chain += color_pre

    if title:
        font = find_font()
        if font:
            # Co chu theo chieu cao khung, khong phai so cung - khung ngang
            # cao chi bang mot phan khung doc cu.
            chain += (
                f"drawtext=fontfile='{font}':text='{esc_drawtext(title)}':"
                "fontcolor=white:fontsize=h/16:line_spacing=10:"
                "x=(w-text_w)/2:y=h/18:box=1:boxcolor=black@0.55:boxborderw=22,"
            )
        else:
            print("[!] Khong tim thay font he thong, bo qua chu tren video.")

    # format=yuv420p PHAI o cuoi, sau drawtext - dat truoc thi chu duoc ve vao
    # chroma da lay mau giam, ra vien chu am mau.
    return chain + "format=yuv420p"


def render(src, clip, out, title, has_audio, crf, preset,
           crop_rect=None, info=None):
    info = info or probe(src)
    color_pre, color_flags = color_args(info)

    top, bottom, right = crop_rect or (0.0, 1.0, 0.0)
    crop_w = int(info["width"] * (1 - min(right, 0.5))) // 2 * 2
    crop_h = int(info["height"] * max(bottom - top, 0.05)) // 2 * 2

    fps = target_fps(info)
    fps_pre = ""
    if abs(fps - (info.get("fps") or fps)) > 0.01:
        fps_pre = f"fps={fps},"                   # chi chen khi that su can doi
    gop = max(2, int(round(fps * 2)))

    vf = build_vf(crop_rect, title, color_pre, fps_pre)

    cmd = ["ffmpeg", "-y", "-nostats", "-hide_banner",
           "-ss", str(clip["start"]), "-i", str(src), "-t", str(clip["len"]),
           "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-profile:a", "aac_low",
                "-b:a", "192k", "-ar", "48000", "-ac", "2",
                # soxr thay cho kernel mac dinh (nguon 44.1k -> 48k), va
                # async/first_pts chan lech tieng vai ms do -ss phia input.
                "-af", "aresample=resampler=soxr:precision=28:async=1:first_pts=0"]
    else:
        cmd += ["-an"]
    # Tran bitrate phai co theo kich thuoc khung, khong dat cung. Tran 16M co
    # dinh khong bao gio cham toi o 1080p (CRF 19 chi can ~4.5M) nhung lai bop
    # nghet khung 4K (6.8 MP can >16M) - thanh ra hai nhom clip khac chat luong.
    px = max(1, int(crop_w * crop_h)) if crop_w and crop_h else info["width"] * info["height"]
    maxrate = min(50, max(12, round(px / 1e6 * 6)))
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-maxrate", f"{maxrate}M", "-bufsize", f"{maxrate * 2}M",
            "-profile:v", "high", "-level", "4.2",
            "-fps_mode", "cfr", "-r", str(fps),
            "-g", str(gop), "-keyint_min", str(int(round(fps))),
            # -ss dat truoc -i nen ep IDR ngay dau clip. Dung danh sach MOC
            # THOI GIAN ("0"), khong dung expr:gte(t,0) - bieu thuc do dung voi
            # MOI khung nen ep toan bo thanh keyframe, ra file all-intra.
            "-force_key_frames", "0"]
    cmd += color_flags
    cmd += ["-movflags", "+faststart", str(out)]

    r = run(cmd)
    if r.returncode != 0:
        print(f"[LOI] render {out.name}:\n{r.stderr[-700:]}", file=sys.stderr)
        return False
    return True


def fmt_t(sec):
    return f"{int(sec // 60)}m{int(sec % 60):02d}s"


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Cat clip tu video dai, giu nguyen khung ngang, tu dong bo ria nhiem chu Trung.")
    ap.add_argument("video", help="File video nguon")
    ap.add_argument("-n", "--count", type=int, default=4, help="So Shorts (mac dinh 4)")
    ap.add_argument("--min-len", type=int, default=20, help="Do dai toi thieu, giay")
    ap.add_argument("--max-len", type=int, default=40, help="Do dai toi da, giay")
    ap.add_argument("-o", "--outdir", default=None, help="Thu muc xuat")
    ap.add_argument("--title", default=None, help="Chu hien tren dau video")
    ap.add_argument("--min-gap", type=float, default=3.0, help="Khoang cach toi thieu giua cac doan")
    ap.add_argument("--step", type=float, default=1.0, help="Buoc quet, giay")
    ap.add_argument("--auto-crop", action="store_true", default=True,
                    help="Tu dong do va cat phu de ghi chet + vien nguon (mac dinh bat)")
    ap.add_argument("--no-auto-crop", dest="auto_crop", action="store_false")
    ap.add_argument("--crf", type=int, default=19)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--dry-run", action="store_true", help="Chi in doan da chon, khong render")
    args = ap.parse_args()

    need("ffmpeg"); need("ffprobe")

    src = Path(args.video).expanduser().resolve()
    if not src.exists():
        die(f"Khong thay file: {src}")
    if args.min_len > args.max_len:
        die("--min-len phai nho hon --max-len")
    if args.max_len > 60:
        print("[!] Shorts/Reels gioi han 60s. Ha --max-len xuong 60.")
        args.max_len = 60

    info = probe(src)
    print(f"\nNguon : {src.name}")
    print(f"Thong tin: {info['width']}x{info['height']} | {info['duration']:.1f}s "
          f"| {info['fps']:.2f}fps | audio: {'co' if info['has_audio'] else 'khong'}")

    if info["duration"] < args.min_len:
        die(f"Video ngan hon {args.min_len}s, khong cat duoc.")

    print("Dang phan tich hinh anh...", flush=True)
    vis = analyze_visual(src)
    if not vis:
        die("Khong phan tich duoc tin hieu hinh anh.")

    aud = []
    if info["has_audio"]:
        print("Dang phan tich am thanh...", flush=True)
        aud = analyze_audio(src)

    weights = ({"vis_mean": 0.34, "vis_peak": 0.22, "aud_mean": 0.14, "aud_var": 0.30}
               if aud else {"vis_mean": 0.60, "vis_peak": 0.40, "aud_mean": 0.0, "aud_var": 0.0})

    cands = build_candidates(vis, aud, info["duration"],
                             args.min_len, args.max_len, args.step, weights)
    if not cands:
        die("Khong tim duoc doan phu hop. Thu ha --min-len.")

    picked = select(cands, args.count, args.min_gap, vis, info["duration"])
    if not picked:
        die("Khong chon duoc doan nao.")
    if len(picked) < args.count:
        print(f"[!] Chi tim duoc {len(picked)}/{args.count} doan khong chong nhau "
              f"(video {info['duration']:.0f}s). Giam --min-gap neu muon nhieu hon.")

    print(f"\nDa chon {len(picked)} doan:\n")
    print(f"  {'#':<3} {'Bat dau':<10} {'Dai':<7} {'Diem':<7}")
    print(f"  {'-'*3} {'-'*10} {'-'*7} {'-'*7}")
    for i, c in enumerate(picked, 1):
        print(f"  {i:<3} {fmt_t(c['start']):<10} {str(c['len'])+'s':<7} {c['score']:.3f}")

    if args.dry_run:
        print("\n(dry-run: chua render)\n")
        return

    outdir = Path(args.outdir).expanduser() if args.outdir else src.parent / f"{src.stem}_shorts"
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"\nXuat ra: {outdir}\n")
    results = []
    for i, c in enumerate(picked, 1):
        out = outdir / f"{src.stem}_short{i:02d}_{fmt_t(c['start'])}.mp4"
        print(f"  [{i}/{len(picked)}] render {out.name} ...", flush=True)
        rect = None
        if args.auto_crop:
            from detect_band import detect_crop
            rect = detect_crop(src)
            print(f"  tu dong cat: giu doc {rect[0]:.3f}->{rect[1]:.3f}")
        ok = render(src, c, out, args.title, info["has_audio"],
                    args.crf, args.preset, rect, info)
        results.append({**c, "file": str(out), "ok": ok,
                        "size_mb": round(out.stat().st_size / 1e6, 2) if ok and out.exists() else 0})

    report = outdir / "report.json"
    report.write_text(json.dumps(
        {"source": str(src), "info": info, "clips": results},
        ensure_ascii=False, indent=2), encoding="utf-8")

    good = [r for r in results if r["ok"]]
    print(f"\nXong: {len(good)}/{len(results)} clip -> {outdir}")
    for r in good:
        print(f"  {Path(r['file']).name}  ({r['size_mb']} MB)")
    print(f"\nBao cao: {report}")
    print(f"Dang len: python3 upload.py --from-report \"{report}\"\n")


if __name__ == "__main__":
    main()
