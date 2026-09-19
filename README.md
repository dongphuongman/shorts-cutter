# shorts-cutter — cắt clip từ video dài, hẹn giờ đăng YouTube + Facebook

Cắt đoạn hay nhất từ video tài liệu đã lồng tiếng Việt, **giữ nguyên khung ngang
của nguồn**, tự dò và bỏ dấu vết tiếng Trung ở rìa, rồi hẹn giờ đăng lên cả hai
nền tảng bằng lịch của chính nền tảng — máy tắt vẫn đăng.

Chỉ cần `ffmpeg`, `python3`, `numpy`, `Pillow`, `requests`.

---

## Dùng nhanh

```bash
python3 autopost.py build --days 7      # cắt clip từ nguồn trong danh sách trắng
python3 autopost.py plan  --days 12     # xem lịch, không đăng gì
python3 autopost.py push  --days 12     # đẩy và hẹn giờ
python3 autopost.py push  --days 12 --only youtube
python3 autopost.py status              # đã đăng gì, còn gì
```

---

## Luật bắt buộc: soi khung trước khi đẩy

**`ffprobe` không phát hiện được phụ đề cháy vào hình, watermark, hay logo kênh
khác.** Chỉ nhìn khung hình mới thấy. Đã vấp năm lần trong một ngày, ở năm vị
trí khác nhau: đáy, giữa khung, góc trên trái, góc trên phải, và đồ hoạ nội dung.

```bash
# trích khung rồi ghép thành bảng để soi nhanh
ffmpeg -ss 8 -i clips/XXX.mp4 -frames:v 1 -vf scale=520:-2 /tmp/a.png
ffmpeg -i /tmp/a.png -i /tmp/b.png -i /tmp/c.png -filter_complex vstack=inputs=3 /tmp/sheet.png
```

Dùng `vstack` cho khung ngang (xếp dọc), `hstack` cho khung dọc. **Không dùng
`tile`** — filter đó chỉ nhận một luồng vào, ghép nhiều ảnh bằng nó chỉ ra ảnh
đầu tiên, phần còn lại đen.

`exclude_sources.json` là **danh sách trắng đảo ngược**: nguồn chưa soi khung thì
nằm trong đó và không được dùng. Soi xong thấy sạch mới gỡ ra.

### Nguồn đã loại vĩnh viễn

| Nguồn | Lý do | Cứu được? |
|---|---|---|
| 7665372852459212058 | Đồ hoạ chữ Trung giữa khung (thẻ tiêu đề, sơ đồ FCI/IHCC) | Không |
| 7670893440074337571 | Hoạt hình chữ Trung (同渗透, 去淡水, nhãn ống nghiệm) | Không |
| 7681657113810193704 | Người Trung nói trước camera, khẩu hình không khớp | Không |
| 7686042064798223670 | Watermark 动物百科老七 + logo CRUNCHLABS | Không |

Crop chỉ xử lý được dấu vết ở **rìa**. Chữ hay logo giữa khung thì phải loại cả nguồn.

---

## Dò vùng cắt — `detect_band.py`

Lấy 7 khung rải đều, hạ 320px, chuyển xám, tính **năng lượng Laplacian** theo
hàng và cột. Chữ trắng viền đen cho đạo hàm bậc hai cao hơn hẳn nội dung.

| Vùng dò | Cách | Ngưỡng |
|---|---|---|
| Dải nội dung | năng lượng theo hàng | > 8% đỉnh |
| Dòng phụ đề (đáy) | đỉnh nhọn trong 45% dưới | > 1,6× trung vị |
| Watermark trên trái | năng lượng vùng trái 32% | > 1,7× trung vị |
| Watermark trên phải | năng lượng theo cột, 25% bên phải | > 1,9× nền nửa trái |
| Dự phòng | khi không dò được đỉnh nào | cắt 13% đáy |

**Không dò được ≠ sạch.** Trên cảnh nhiều chi tiết (đá, nước, cỏ) trung vị vốn
đã cao nên chữ mảnh không vượt ngưỡng — đã vấp thật trên `7684916158625420389`.
Khi không thấy đỉnh, bộ dò lùi về con số 13% đo tay trong `hosotunhien.yaml`.

Nguồn không đồng nhất, nên mỗi nguồn đo riêng:

```
1920x1080 ngang thuần      -> giữ 0.072-0.882, cắt phải 0.05
1080x1920 ghép sẵn khung dọc -> giữ 0.341-0.608  (nội dung thật chỉ là dải giữa)
1824x940  đã cắt sẵn        -> giữ 0.000-0.864, cắt phải 0.134 (watermark phải)
```

---

## Chọn đoạn — `cut_shorts.py`

Quét mọi cửa sổ 20–40 giây, bước 1 giây, hai tín hiệu: **thay đổi hình ảnh**
(`scdet`, 4 mẫu/giây) và **âm thanh** (`ebur128`). Mỗi chỉ số quy về thứ hạng
phần trăm trước khi cộng, nên một cảnh nổ hình không lấn át thang điểm.

| Tín hiệu | Trọng số |
|---|---|
| Chuyển động trung bình | 0,34 |
| Biến thiên âm lượng | 0,30 |
| Đỉnh chuyển động | 0,22 |
| Âm lượng trung bình | 0,14 |

Đoạn gần 30 giây được cộng tối đa 10%. Điểm bắt đầu dịch về ngay sau một cú
chuyển cảnh trong ±2,5 giây để clip không mở giữa chừng.

Tiêu đề lấy từ `work/hosotunhien/meta/<id>.json` — trường `chapters` có nhãn kèm
mốc thời gian nên mỗi clip đặt tên theo đúng đoạn nội dung nó rơi vào.

---

## Dựng video

**Giữ nguyên khung ngang của nguồn.** Không scale, không pad, không ghép khung
dọc — nên chuỗi filter **không có phép co giãn nào**. Đó là trần chất lượng lấy
được từ nguồn.

```
crop=<dải nội dung>,setsar=1[,zscale=<chuyển màu>][,drawtext],format=yuv420p
```

Sau khi cắt rìa, tỷ lệ thành 2,0–2,28:1 chứ không còn đúng 16:9. Muốn 16:9 chuẩn
thì phải pad — tức mang viền đen quay lại.

### Cờ mã hóa

```
-c:v libx264 -preset medium -crf 19 -maxrate <co theo khung> -bufsize <2x>
-profile:v high -level 4.2 -fps_mode cfr -r <fps nguồn>
-g <2*fps> -keyint_min <fps> -force_key_frames 0 -movflags +faststart
-c:a aac -profile:a aac_low -b:a 192k -ar 48000 -ac 2
-af aresample=resampler=soxr:precision=28:async=1:first_pts=0
```

### Ba cái bẫy trong khối cờ này

**`-force_key_frames expr:gte(t,0)` là SAI.** Biểu thức đó đúng với *mọi* khung
hình (t luôn ≥ 0) nên ép toàn bộ thành keyframe → file all-intra, bitrate vọt từ
4,5 lên 17 Mbps. Phải dùng danh sách mốc thời gian `"0"`.

**Trần bitrate phải co theo số điểm ảnh.** Trần cố định 16M không bao giờ chạm ở
1080p (CRF 19 chỉ cần 4,5M) nhưng bóp nghẹt khung 4K (6,8 MP cần ~38M) — thành
ra hai nhóm clip khác chất lượng. Công thức: `min(50, max(12, MP × 6))` Mbps.

**Đừng ép 30fps mù quáng.** Đo bằng `mpdecimate`: nguồn 31fps ép về 30 mất
19 khung độc lập trong 20 giây; nguồn 25fps bị chèn đúng 100 khung trùng. Nhưng
nguồn "60fps" thì chỉ có ~507 khung độc lập trên 1201 — ép về 30 chỉ mất 2 khung,
giữ 60fps là gấp đôi dung lượng để chép khung trùng.

### Màu

**Không gắn bt709 hàng loạt.** Một nguồn trong kho là BT.601 (`bt470bg`/
`smpte170m`), gắn bừa sẽ lệch màu đúng cái muốn tránh. Nguồn đã đúng thì chỉ gắn
thẻ phòng thủ; nguồn khác thì `zscale` chuyển rồi mới gắn; nguồn không gắn thẻ
thì giả định theo chiều cao và in log nói rõ.

---

## Đăng bài

| Nền tảng | Endpoint | Hẹn giờ |
|---|---|---|
| YouTube | `videos.insert` | `status.publishAt` ngay trong lệnh upload |
| Facebook | `/{page}/videos` trên `graph-video` | `scheduled_publish_time` ngay lúc tạo |

### Facebook: KHÔNG dùng `/video_reels`

Endpoint đó **chỉ nhận khung dọc**. Đã đo thật: file 1824×874 (2,08:1) qua được
pha upload và processing nhưng kết thúc ở `video_status: error`, publishing
không bao giờ bắt đầu.

Hẹn giờ phải truyền **ngay lúc tạo**. Đặt sau khi tạo thì API trả
`"You only set a scheduled publish time on an unpublished post"`.

### Upload theo khối cho file lớn

POST một lần chết ở **413** khi file vượt ~100 MB — đo được: 83 MB qua, 137 MB
không. Nguồn 4K cho clip 30 giây nặng ~140 MB nên bắt buộc có đường này.
`fb_push()` tự chuyển sang `upload_phase: start → transfer → finish` khi file
trên 90 MB.

### Phân loại nội dung

Meta đã gộp **mọi video Facebook thành Reels**, bất kể đăng qua endpoint nào.
Business Suite ghi rõ: *"Mọi video bạn đăng trên Facebook giờ sẽ là thước phim"*.
Nên permalink `/reel/...` là đúng, và `/video_reels` khi **đọc** liệt kê cả video
ngang dài 4 phút cũng không phải lỗi API.

Muốn kiểm tra loại nội dung: **Business Suite → Nội dung → Đã lên lịch**. Đó là
nguồn đáng tin duy nhất; permalink và danh sách API đều không phản ánh đúng.

### Quota YouTube

10.000 đơn vị/ngày ÷ 1.600 mỗi lần tải lên = **6 video/ngày**. Xoá video **không**
hoàn lại quota.

**Reset lúc nửa đêm giờ Thái Bình Dương = 14:00 giờ Việt Nam.** Sổ đếm
`yt_quota.json` khóa theo ngày Thái Bình Dương, không phải ngày VN — đếm theo giờ
VN thì sáng sớm hôm sau sẽ tưởng còn quota trong khi chưa reset, và ăn 403.

Sổ này tách riêng khỏi `posted.json` để không mất khi phải cắt lại clip.

### Scope token

`youtube.upload` đủ để tải lên và hẹn giờ, **không** đủ để đổi chế độ riêng tư
hay xoá video (cần `videos.update` / `videos.delete`). Muốn xoá phải vào Studio.

`FB_PAGE_TOKEN` là **user token** không hết hạn; Page token lấy qua
`/me/accounts` mỗi lần chạy.

---

## Dùng chung clip giữa hai nền tảng

Mỗi nền tảng có hàng đợi riêng — một clip đăng lên Facebook vẫn còn dùng được cho
YouTube. Khán giả hai bên gần như không trùng nhau.

`posted.json` khóa theo `<nền_tảng>::<file>`. Trước đây khóa theo file nên
Facebook (3 bài/ngày) nuốt gần sạch hàng đợi trước khi YouTube kịp lấy.

---

## Chạy tự động

```
~/Library/LaunchAgents/com.hosotunhien.pushyoutube.plist
→ 14:30 giờ VN mỗi ngày → push_youtube.sh → youtube_cron.log
```

Chọn 14:30 vì quota reset lúc 14:00 giờ VN. Dùng `launchd` chứ không `cron`: nếu
máy đang ngủ, launchd chạy bù khi máy thức, cron thì bỏ qua luôn.

```bash
launchctl list | grep hosotunhien          # xem job còn sống không
tail -f youtube_cron.log                   # xem log
launchctl unload ~/Library/LaunchAgents/com.hosotunhien.pushyoutube.plist   # tắt
```

---

## Khung giờ

| Nền tảng | Giờ (VN) |
|---|---|
| YouTube | 19:00 |
| Facebook | 06:30 · 12:00 · 20:30 |

---

## File trong thư mục

| File | Việc |
|---|---|
| `autopost.py` | Điều phối. Đọc credential và metadata tiếng Việt từ `douyin2vietsub` |
| `detect_band.py` | Dò dải nội dung, phụ đề, watermark bốn phía |
| `cut_shorts.py` | Chấm điểm chọn đoạn, render |
| `push_youtube.sh` | Script cho launchd gọi |
| `exclude_sources.json` | Danh sách trắng đảo ngược |
| `queue.json` | Hàng đợi clip kèm tiêu đề, mô tả, tag, điểm |
| `posted.json` | Đã lên lịch, khóa `<nền_tảng>::<file>` |
| `yt_quota.json` | Sổ quota theo ngày Thái Bình Dương |
| `docs/` | Tài liệu HTML: pipeline và quy trình |
| `upload.py` | Uploader độc lập, không dùng trong luồng này |

---

## Sự cố thường gặp

| Hiện tượng | Nguyên nhân |
|---|---|
| `Khong tim thay 'ffmpeg'` | `brew install ffmpeg` |
| Facebook 413 khi upload | File > ~100 MB, phải dùng đường theo khối |
| Facebook `video_status: error` | Đang gọi `/video_reels` với khung ngang |
| YouTube 403 quotaExceeded | Chưa tới 14:00 giờ VN, quota chưa reset |
| YouTube 403 insufficient scopes | Token chỉ có `youtube.upload` |
| Bitrate vọt lên 17 Mbps | `-force_key_frames` dùng expr thay vì mốc thời gian |
| Video giật | Đang ép `-r 30` trên nguồn 25 hoặc 31 fps |
| Màu nhợt sau khi đăng | Nguồn BT.601 bị gắn thẻ bt709 mà không chuyển |
| Ghép ảnh chỉ ra ảnh đầu | Dùng `tile` thay vì `hstack`/`vstack` |
| Chữ tiếng Việt mất dấu | Thiếu font, script tự tìm Arial Unicode/Helvetica |
