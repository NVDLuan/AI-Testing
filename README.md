# AI-Testing — Bộ test kết quả phân tích video (Computer Vision)

Framework test linh hoạt để đánh giá độ chính xác của hệ thống phân tích video
bên thứ 3. Cấu hình theo **tenant (khách hàng) → job → rule → camera → zone**,
mọi điểm tích hợp đều là interface và được lắp ráp qua **factory theo tenant**.

## Flow (Chain of Responsibility)

```
HealthCheck → LoadGroundTruth → CheckMissing → Upload → UpdateRuleConfig
            → TriggerAndWait → Validate → PublishEvidence → Report
```

Mỗi bước là một `Handler` trong `pipeline/handlers.py`. Test **theo từng rule**
(`videos_by_rule`). **HealthCheck** chạy đầu tiên; hệ thống không khoẻ → bỏ qua các
bước test nhưng **vẫn gen report** (file `*_ABORTED.*` ghi lý do + trạng thái health),
exit code `2`. **UpdateRuleConfig** đẩy config camera (`POST /api/rule`) trước khi
trigger — chỉ áp dụng generic API (linfox/EWS); hand-washing để no-op nên tự bỏ qua.
**PublishEvidence** tải video evidence (File Browser) → upload S3 → gắn link vào report
HTML; chưa cấu hình thì bỏ qua êm. Thêm/bớt/đổi thứ tự = sửa `pipeline/chain.py`
(`--skip-health` để bỏ qua health).

### Đơn vị so khớp & metrics

Mỗi video một **outcome** (`TP/FP/FN/TN`), gom thành `ConfusionMatrix` overall + theo
rule + theo camera:

- **Recall** = TP / (TP + FN) — bắt đúng ca vi phạm
- **FAR** (False Alarm Rate) = FP / (FP + TN) — báo động giả

**Luật chấm là cắm được** (`validator/strategies.py`): mỗi luật quyết outcome cho 1
video, lõi chỉ lo gom ma trận → thêm cách verify mới = thêm 1 class.

| Luật | Dùng cho | Outcome dựa trên |
|---|---|---|
| `BinaryViolationStrategy` | linfox, EWS | có/không vi phạm (boolean) |
| `TriggerViolationStrategy` | bv_c (base) | 3-trạng-thái: No Trigger / Trigger-No Violation / Trigger-Violation (1 ma trận gộp) |
| `TriggerStrategy`/`ViolationStrategy`/`SequenceStrategy`/`CountStrategy` | bv_c | chain, xem [Luật chấm bv_c](#luật-chấm-bv_c-hand-washing) |

## Cấu hình (3 tầng)

| File | Nội dung |
|---|---|
| `configs/config.yaml` | Danh sách **tenant**: `jobs` (sheet_id), `version`, `config`, `dir`, `load_dir`… |
| `configs/tenants/<load_dir>/rules.yaml` | Danh sách **rule**: `name`, `code`, `sheet` (tab), `type`, `time_sleep`… |
| `configs/tenants/<load_dir>/<version>/<CODE>/<CODE>_config.json` | Config **camera/zone** theo rule |

**Google Sheet**: mỗi rule nằm ở 1 tab riêng (`rule.sheet`). Mỗi dòng = 1 video. Map
cột logic → cột thật do **factory tenant** quyết (`sheet_columns`): `video_url` (nguồn
S3), `video_name` (unique *trong rule*), nhãn (`expected` boolean **hoặc**
`trigger_status` 3-state cho bv_c), `camera`. Vì `video_name` chỉ unique trong rule,
khoá nội bộ được namespace `rule_code/video_name`.

Loader (`configs/loader.py`) tự resolve path tương đối theo vị trí `config.yaml`.

## Secrets (.env)

Khoá bí mật nạp tự động từ `.env` ở gốc — **đã `.gitignore`, không commit**:

```bash
cp .env.example .env   # rồi điền giá trị thật
```

| Biến | Dùng cho |
|---|---|
| `API_KEY` | header `X-API-Key` của API phân tích |
| `S3_BUCKET` | **bucket dùng chung**: download key-thuần + upload evidence |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | creds S3 (dùng chung) |
| `AWS_DEFAULT_REGION` / `S3_ENDPOINT` | region / endpoint S3 (MinIO: `https://host`) |
| `FILE_BROWSER_URL` / `FILE_BROWSER_USER` / `FILE_BROWSER_PASSWORD` | tải evidence cho report HTML |
| `S3_PUBLIC_BASE` | domain/CloudFront công khai để dựng URL evidence (tuỳ chọn) |

Biến set sẵn ngoài shell ưu tiên hơn `.env`; vẫn override được qua CLI.

## Chạy

```bash
.venv/bin/pip install -r requirements.txt

# Liệt kê tenant / job / rule
python main.py --list

# Offline (fake sheet + mock client) — không cần mạng/credential
python main.py --tenant linfox_vn --job SMOKE -v

# Lọc rule (theo name hoặc code)
python main.py -t linfox_vn -j SMOKE --rule USEPHONE --rule COLLISION

# Chạy thật: Google Sheet RIÊNG TƯ + API + S3
python main.py -t linfox_vn -j SMOKE \
    --source gsheet --credentials sa.json \
    --analysis http --api-url http://103.104.122.36:8000 \
    --s3-bucket my-bucket --s3-region ap-southeast-1 \
    --formats console json csv

# Sheet PUBLIC ("ai có link đều xem"): KHÔNG cần --credentials
python main.py -t linfox_vn -j SMOKE --source gsheet_public \
    --analysis http --api-url http://103.104.122.36:8000 \
    --s3-bucket my-bucket --formats console html template

# Report HTML + nhúng link evidence (File Browser → S3 public)
python main.py -t linfox_vn -j SMOKE --source gsheet_public \
    --analysis http --api-url http://103.104.122.36:8000 \
    --s3-bucket my-bucket \
    --file-browser-url http://FB_HOST --evidence-field evidence_path \
    --formats console html json
```

Report in console + ghi `reports/`. Exit code: `2` = pipeline dừng (vd health fail),
`1` = có video thiếu kết quả, `0` = ok (tiện CI).

### Định dạng report (`--formats`)

`console` · `csv` · `html` (bảng metrics + bảng từng video, cột Evidence là link S3) ·
`json` / `template` (đều xuất `reports/test_results_<stamp>.json` theo **mẫu QC**:
`{version_run, summary, details{batch_code:{… test_case_validation_result …}}, health}`).

### API bên thứ 3 (FakeAgent)

Mỗi tenant trỏ tới một "flavor" API khác nhau, chọn tự động qua factory:

| Tenant | Client | Endpoints |
|---|---|---|
| linfox, EWS | `LinfoxHttpClient` | `/api/videos/check`, `/api/videos/analyze`, `/api/evidences` |
| bv_c | `HandWashingHttpClient` | `/api/hand-washing/videos/{check,trigger,upload}`, `/api/hand-washing/events` |

- **Auth**: header `X-API-Key` (CLI `--api-key` hoặc env `API_KEY`).
- **Health**: `GET /health` (public) + `GET /api/get-overall-health-status` — **dừng** nếu
  `deepstream_is_healthy=False` HOẶC cpu/ram/gpu/disk usage > **80%**.
- **Quy ước vi phạm**: linfox = tồn tại *evidence* cho video; bv_c = *event.violation == True*
  (và `triggered` = có event nào không).
- **Upload bv_c** cần kèm file **keypoints `.txt`** (cùng tên video) trong `--recordings-dir`;
  linfox chỉ gửi video.

### Nguồn video (S3 / MinIO / HTTP public)

`VideoStore.fetch(video) → (bytes, filename)`: `S3VideoStore` lấy video vào **RAM** rồi
upload thẳng (không ghi đĩa). Định tuyến theo URL: thử **HTTP public** trước, nếu 401/403
thì fallback **S3 API** với endpoint + path-style **suy từ host của URL** (MinIO/AWS đều
được). `--cache-dir <dir>`: lưu xuống đĩa và **tái dùng nếu đã có** (bỏ qua tải lại).

```bash
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... S3_BUCKET=my-bucket
# S3_ENDPOINT=https://minio.rainscales.com   # nếu video/bucket trên MinIO
```

`video_url` chấp nhận `s3://bucket/key`, URL https (virtual-hosted / path-style), hoặc
**key thuần** (dùng `--s3-bucket` + `--s3-prefix`). Khi `--analysis mock`, store là no-op.

### Evidence → S3 → report HTML

Cần `--formats html` **và** đủ cấu hình (`FILE_BROWSER_URL` + bucket). `FileBrowserFetcher`
login lấy cookie `auth=`, tải `/api/raw<path>`; `S3PublicUploader` upload public-read trả
URL. `--evidence-field` trỏ field trong event chứa path evidence (linfox: `evidence_path`).
Thiếu cấu hình → bỏ qua êm (HTML vẫn ra, không link); log warning nói thiếu gì.

### Luật chấm bv_c (hand-washing)

Mỗi tiêu chí là 1 `ValidationStrategy` ĐỘC LẬP (`validator/hand_washing_strategy.py`),
có `name` riêng, xếp thành **chain** qua `--hw-layers` (chuỗi token phân tách bởi dấu
phẩy, mặc định CLI `trigger,violation` — `RunOptions.hw_layers` khi dựng trực tiếp
mặc định đủ 4 layer `trigger,violation,sequence_exact,count`):

| Token | Class | Outcome | Phạm vi |
|---|---|---|---|
| `trigger` | `TriggerStrategy` | có/không trigger đúng lúc | mọi video |
| `violation` | `ViolationStrategy` | `event.violation` so với GT **+ 1 điều kiện phụ (`cond`) về count** | chỉ video GT cần trigger & model có trigger |
| `sequence` | `SequenceStrategy(exact=False)` | `Step Sequence` là subsequence của event | như trên + sheet có khai Step Sequence |
| `sequence_exact` | `SequenceStrategy(exact=True)` | tập bước khớp CHÍNH XÁC | như trên |
| `count` | `CountStrategy` | mọi cột `B0..B6` cùng phía ngưỡng 5 | như trên + sheet có khai cột B |

`ViolationStrategy` chấm theo ma trận chuẩn (positive = có vi phạm) NHƯNG violation flag
khớp GT vẫn có thể bị hạ thành **FP** nếu `cond` (count) không xác nhận: GT **No
Violation** đòi mọi count (`B0..B6`) `>= 5`; GT **Violation** đòi mọi count lệch expected
`<= 1`. Không khai cột B nào -> `cond` coi như luôn đạt (chỉ chấm theo violation flag).

Mỗi strategy trong chain chạy `validate()` riêng, sinh 1 `ValidationReport` riêng —
report coi mỗi cái là **1 "rule ảo"**, tên `"<rule_code> - <Name>"` (vd
`"Hygiene - Trigger"`, `"Hygiene - Violation"`) — mỗi batch/entry riêng trong
`test_results_*.json` (`details{}`/`summary{}`), dashboard không cần sửa vì đã render
generic theo từng entry. Video ngoài phạm vi một layer thì layer đó bỏ qua hẳn (không
tính vào total/pass-fail của layer, không hiện MISSING). Thêm tiêu chí mới = thêm 1
class + đăng ký token trong `BvCFactory.create_validation_chain`, không sửa layer khác.
Tenant chỉ 1 strategy (linfox/EWS, `--hw-layers` không áp dụng) giữ nguyên tên rule,
không suffix.

## Cấu trúc

| Thư mục | Vai trò |
|---|---|
| `configs/` | Schema (Tenant/Job/Rule/Camera) + loader 3 tầng + nạp `.env` |
| `interfaces/` | `AnalysisClient`, `GroundTruthSource`, `VideoStore`, `EvidenceFetcher/Uploader` — hợp đồng |
| `adapters/` | client HTTP (linfox/hand-washing) + mock, GoogleSheet (private/public) / Fake, `S3VideoStore` / `S3PublicUploader`, `FileBrowserFetcher` |
| `tenants/` | `TenantFactory` + registry override theo `load_dir` |
| `pipeline/` | `context`, `handlers` (chain), `chain`, `runner` (lắp ráp) |
| `validator/` | Models, confusion matrix, `strategies.py` (luật cắm được), `hand_washing_strategy.py` |
| `report/` | `reporter` (console/csv/html), `template` (mẫu QC JSON), `evidence` (publish) |
| `tests/` | Test metrics/validator, S3 parse |

## Mở rộng

- **Thêm tenant** có hành vi riêng: subclass `TenantFactory`, gắn `@register("<load_dir>")`
  (xem `LinfoxVNFactory` / `BvCFactory`). Runner tự chọn qua `get_factory(tenant)`.
- **Luật chấm mới**: thêm 1 class `ValidationStrategy` (vd verify detection-level đọc
  `pred.events`), trả về từ `factory.create_validation_strategy()`. Lõi không đổi.
- **Cắm API/Sheet thật**: đặt `http_client_cls`, map `sheet_columns` trong factory tenant.
- **Đổi flow**: sửa `pipeline/chain.py` hoặc override `TenantFactory.create_chain()`.

## Mock / Fake (offline)

`FakeSheetGroundTruthSource` sinh ground truth từ **camera_id thật** trong các file
`<CODE>_config.json`. `MockAnalysisClient` sinh prediction từ nhãn thật rồi tiêm lỗi
theo `far_rate`/`miss_rate` (mô phỏng cả `triggered` cho 3-state). Chỉ để chạy thử —
không phản ánh độ chính xác hệ thống thật.

## Test

```bash
python -m tests.test_metrics
python -m tests.test_s3
```
