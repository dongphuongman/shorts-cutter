#!/usr/bin/env python3
"""
detect_band.py - Tu dong do vung can cat tren tung video nguon.

Nguon Douyin khong dong nhat: co cai la khung ngang thuan, co cai da duoc ghep
san thanh khung doc voi noi dung that chi la mot dai ngang o giua. Mot ty le
crop co dinh vi vay khong the dung cho moi nguon - do la nguyen nhan lo phu de
tieng Trung van lot qua o nguon doc.

Cach do, tren nang luong Laplacian theo tung HANG (trung binh nhieu khung):
  - Vung nen mo   : nang luong rat thap  (~0.3)
  - Vung noi dung : nang luong trung binh (~5-15)
  - Dong phu de   : DINH NHON ro rang    (~29-37)
Chu trang net tren nen toi cho dinh cao gap 2-4 lan noi dung xung quanh.
"""

import io
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def sample_frames(video, n=7, width=320):
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip() or 0)
    out = []
    for i in range(n):
        t = dur * (i + 1) / (n + 1)
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={width}:-1,format=gray",
             "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
        if p.returncode == 0 and p.stdout:
            out.append(np.asarray(Image.open(io.BytesIO(p.stdout)), dtype=np.float32))
    return out


def detect_crop(video, right_default=0.05, margin=0.012):
    """Tra ve (top, bottom, right) dang ty le 0..1.

    top/bottom: gioi han doc cua vung GIU LAI.
    right:      ty le cat ben phai (dai chu canh bao doc).
    """
    frames = sample_frames(video)
    if not frames:
        return 0.0, 1.0 - 0.13, right_default
    h = frames[0].shape[0]

    rowE = np.zeros(h)
    for f in frames:
        rowE += np.abs(np.diff(f, n=2, axis=1)).mean(axis=1)
    rowE /= len(frames)

    # 1. Dai noi dung: bo vien mo (nang luong duoi 8% dinh)
    content = rowE > rowE.max() * 0.08
    if not content.any():
        return 0.0, 1.0 - 0.13, right_default
    idx = np.where(content)[0]
    top_i, bot_i = int(idx[0]), int(idx[-1])

    # 1b. Watermark goc TREN TRAI: dinh nhon o dau dai noi dung, chi ben trai.
    #     Vd "科普新紀元" - crop ben phai khong cham toi.
    wm = np.zeros(h)
    for f in frames:
        wm += np.abs(np.diff(f[:, :int(f.shape[1] * 0.32)], n=2, axis=1)).mean(axis=1)
    wm /= len(frames)
    wm_med = float(np.median(wm[content])) if content.any() else 0.0
    head = wm[top_i:top_i + max(3, int(h * 0.12))]
    if wm_med > 0 and len(head) and head.max() / wm_med > 1.7:
        # bo qua dai co watermark roi lui them chut cho chac
        off = int(np.argmax(head))
        top_i = min(top_i + off + max(2, int(h * 0.035)), top_i + int(h * 0.12))

    # 1c. Watermark canh PHAI: cat cung 5% khong du cho moi nguon. Do that tren
    #     7660395732079267110: chu "彪子动物" an vao ~12.6% chieu rong.
    #     Do bang nang luong theo COT trong dai tren cua vung noi dung - chu
    #     tinh tren nen chuyen dong cho dinh ro.
    right_cut = right_default
    head_h = max(3, int((bot_i - top_i + 1) * 0.22))
    strip = [f[top_i:top_i + head_h, :] for f in frames]
    if strip and strip[0].size:
        colE = np.zeros(strip[0].shape[1])
        for f in strip:
            colE += np.abs(np.diff(f, n=2, axis=0)).mean(axis=0)
        colE /= len(strip)
        w = len(colE)
        base = float(np.median(colE[: int(w * 0.6)]))   # nua trai lam nen
        if base > 0:
            lim = int(w * 0.75)                          # chi soi 25% ben phai
            hot = [x for x in range(lim, w) if colE[x] > base * 1.9]
            if len(hot) >= max(3, int(w * 0.02)):
                edge = min(hot)
                cand = 1.0 - edge / w + 0.012            # bien an toan
                right_cut = min(max(right_default, cand), 0.20)

    # 2. Dong phu de: dinh nhon trong 45% duoi cua dai noi dung
    band = rowE[top_i:bot_i + 1]
    med = float(np.median(band))
    lo = top_i + int(len(band) * 0.55)
    spike = [y for y in range(lo, bot_i + 1) if rowE[y] > med * 1.6]

    if spike:
        cut_i = min(spike)
        bottom = max(0.35, cut_i / h - margin)
    else:
        # KHONG do duoc khong co nghia la sach. Voi canh quay nhieu chi tiet
        # (da, nuoc, co) thi chu manh khong tao dinh du cao so voi trung vi, va
        # phu de lot qua - da vap that tren nguon 7684916158625420389.
        # Luc do lui ve con so NGUOI DA DO: cat 13% day dai noi dung
        # (video.crop_bottom trong channels/hosotunhien.yaml).
        band_h = (bot_i - top_i + 1) / h
        bottom = bot_i / h - 0.13 * band_h
    top = top_i / h
    if bottom - top < 0.2:                      # do hong -> ve mac dinh an toan
        top, bottom = 0.0, 1.0 - 0.13
    return round(top, 4), round(bottom, 4), round(right_cut, 4)


def main():
    for v in sys.argv[1:]:
        t, b, r = detect_crop(v)
        print(f"{Path(v).name[:44]:<46} giu doc {t:.3f}->{b:.3f}  cat phai {r}")


if __name__ == "__main__":
    main()
