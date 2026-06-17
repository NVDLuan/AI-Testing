# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mục đích

Framework test độ chính xác của hệ thống phân tích video (computer vision) bên thứ 3.
Flow: load config → đọc ground truth (Google Sheet) → check video thiếu → tải từ S3 →
upload → trigger phân tích → chờ kết quả → so khớp với ground truth → (publish evidence)
→ report FAR/Recall.
Đơn vị so khớp là **mỗi video một outcome**: binary (có/không vi phạm) cho linfox/EWS,
hoặc 3-trạng-thái (No Trigger / Trigger-No Violation / Trigger-Violation) cho bv_c.
Luật chấm điểm là **cắm được** (`validator/strategies.py`) để sau này thêm cách verify
khác (vd detection-level) mà không đụng lõi.

## Lệnh

```bash
.venv/bin/pip install -r requirements.txt

# Liệt kê tenant / job / rule
.venv/bin/python main.py --list

# Chạy offline (mock client + fake ground truth, không cần mạng/secret)
.venv/bin/python main.py --tenant linfox_vn --job SMOKE -v
.venv/bin/python main.py -t linfox_vn -j SMOKE --rule USEPHONE --rule COLLISION

# Chạy thật (Google Sheet riêng tư + API + S3); secret nạp từ .env (cp .env.example .env)
.venv/bin/python main.py -t linfox_vn -j SMOKE \
    --source gsheet --credentials sa.json \
    --analysis http --api-url http://103.104.122.36:8000 \
    --s3-bucket BUCKET --s3-region ap-southeast-1 \
    --formats console json csv

# Sheet PUBLIC ("ai có link đều xem"): dùng --source gsheet_public, KHÔNG cần --credentials
.venv/bin/python main.py -t linfox_vn -j SMOKE \
    --source gsheet_public \
    --analysis http --api-url http://103.104.122.36:8000 \
    --s3-bucket BUCKET --s3-region ap-southeast-1 \
    --formats console json html

# Report HTML + nhúng link evidence (tải qua File Browser -> upload S3 public)
.venv/bin/python main.py -t linfox_vn -j SMOKE \
    --source gsheet --credentials sa.json \
    --analysis http --api-url http://103.104.122.36:8000 \
    --s3-bucket BUCKET --s3-region ap-southeast-1 \
    --file-browser-url http://FB_HOST --evidence-field FIELD_NAME \
    --formats console json html

# Tests (plain module, không dùng pytest runner)
.venv/bin/python -m tests.test_metrics
.venv/bin/python -m tests.test_s3
```

Không có build step / linter cấu hình sẵn. Python 3.12, venv ở `.venv/`.
Exit codes: `0` ok · `1` có video thiếu kết quả · `2` pipeline bị dừng (vd health fail).

## Kiến trúc

Bốn trừu tượng chính, mỗi cái nằm rải nhiều file — đọc cùng nhau mới nắm được:

**1. Config 3 tầng** (`configs/`, đọc `loader.py` + `schema.py` cùng nhau)
- `configs/config.yaml` → `RootConfig` → `TenantConfig` (jobs/version/load_dir).
- `configs/tenants/<load_dir>/rules.yaml` → `RuleDef` (name/code/sheet/type/time_sleep).
- `configs/tenants/<load_dir>/<version>/<CODE>/<CODE>_config.json` → `CameraConfig` (keyed theo camera_id, chứa `cam_zones`).
- `ConfigLoader` resolve path tenant tương đối theo vị trí `config.yaml`; nạp rules/cameras **lười** và graceful khi thiếu folder.

**2. Pipeline = Chain of Responsibility** (`pipeline/`)
- `chain.py` lắp thứ tự: HealthCheck → LoadGroundTruth → CheckMissing → Upload → UpdateRuleConfig → TriggerAndWait → Validate → **PublishEvidence** → Report. Sửa flow = sửa list này.
- `handlers.py`: mỗi bước một `Handler`; state truyền qua `PipelineContext` (`context.py`).
- `PublishEvidenceHandler`: chỉ chạy khi report có format `html` **và** có `ctx.evidence_publisher`; tải evidence (File Browser) → upload S3 → gắn URL vào `comparison.detail["evidence_urls"]`. Không cấu hình → bỏ qua êm, HTML vẫn ra (không link).
- **Cơ chế abort**: khi `ctx.abort()` (vd health fail), các handler thường tự bỏ qua nhưng chain vẫn chạy tới cuối; handler đặt `run_on_abort = True` (chỉ `ReportHandler`) vẫn chạy → **luôn có report** (file `*_ABORTED.json` / `.html`).
- `runner.py` mỏng: dùng factory dựng context + chain rồi `chain.handle(ctx)`.

**3. TenantFactory chọn implementation theo khách hàng** (`tenants/factory.py`)
- `get_factory(tenant)` tra registry theo `load_dir`; mặc định `TenantFactory`, override bằng `@register("<load_dir>")`.
- Đây là nơi 2 "flavor" API được tách: linfox/EWS dùng `LinfoxHttpClient` (generic `/api/*`), bv_c dùng `HandWashingHttpClient` (`/api/hand-washing/*`) — set qua `http_client_cls`.
- Factory cũng quyết định: ground-truth source (gsheet/fake), video store (S3/no-op), **luật chấm** (`create_validation_strategy`, None = tự chọn theo dữ liệu) và **evidence publisher** (`create_evidence_publisher`, None nếu mock/chưa cấu hình) theo `RunOptions`.
- `LinfoxVNFactory` / `BvCFactory` còn map cột Google Sheet thật (xem `sheet_columns`).

**4. Interfaces + adapters** (`interfaces/` định nghĩa hợp đồng, `adapters/` hiện thực)
- `AnalysisClient`: `health_check` / `prime` / `update_rule_config` / `check_missing` / `upload` / `trigger` / `poll_results`. `Mock` cho offline; 2 HTTP client cho 2 flavor.
- `GroundTruthSource`: `GoogleSheet` (riêng tư, cần gspread + service account) / `PublicGoogleSheet` (sheet public, đọc export CSV qua gviz, chỉ cần requests) / `FakeSheet` (offline). `VideoStore`: `S3VideoStore` (lazy — chỉ validate key/khởi tạo boto3 khi download) / `NoOpVideoStore`.
- `EvidenceFetcher` / `EvidenceUploader` (`interfaces/evidence.py`): `FileBrowserFetcher` (login lấy cookie `auth=`, tải `/api/raw<path>`, fallback strip `/evidence`) + `S3PublicUploader` (upload public-read, trả public URL). `EvidencePublisher` (`report/evidence.py`) ghép 2 bước, nuốt lỗi từng evidence.
- `ValidationStrategy` (`validator/strategies.py`): luật chấm cắm được — `BinaryViolationStrategy` (linfox) / `TriggerViolationStrategy` (bv_c). Thêm cách verify mới = thêm 1 class.

## Quy ước quan trọng

- **`video_id` vs `name`** (`validator/models.VideoRef`): `video_name` từ sheet chỉ unique *trong rule*, nên khoá nội bộ `video_id = "<rule_code>/<video_name>"` (toàn cục) để không đụng giữa các rule; `name` mới là tên gửi cho API. HTTP client dùng `v.name` cho API và key prediction theo `v.video_id`.
- **Định nghĩa vi phạm** (per flavor, trong `poll_results`): linfox = tồn tại *evidence* cho video (khớp `video_code` hoặc tên file trong `original_video_path`, phân trang `/api/evidences`); hand-washing = `event.violation == True`, và `Prediction.triggered` = có event nào không. So khớp tên qua `_http_base.norm_name` (bỏ path + đuôi).
- **Health check** (`adapters/_http_base.py`): gọi `/api/get-overall-health-status`; **dừng** (abort → report `*_ABORTED`) nếu `deepstream_is_healthy=False` HOẶC bất kỳ field nào trong {cpu/ram/gpu/disk}`_usage_percentage` > `RESOURCE_USAGE_THRESHOLD_PERCENT` (80%). Field vượt ngưỡng ghi vào `detail["over_threshold"]`.
- **Luật chấm = strategy** (`validator/strategies.py`, gọi từ `validate()`): mỗi video → `classify()` trả `TP/FP/FN/TN`; `validate()` chỉ gom `ConfusionMatrix` overall + theo rule + theo camera. positive = "có vi phạm". `FAR = FP/(FP+TN)`, `Recall = TP/(TP+FN)`.
  - **bv_c 3-state** (`TriggerViolationStrategy`, **1 ma trận gộp**): `TP`=trigger đúng & violation khớp; `FP`=có trigger nhưng sai (violation sai *hoặc* lẽ ra không trigger); `FN`=cần trigger mà không trigger; `TN`=không trigger đúng. Lưu ý: "trigger đúng nhưng bỏ sót vi phạm" tính **FP** (không phải FN).
- **Cột Google Sheet** (`adapters/_rows.py`, map ở factory): nhãn binary qua `parse_bool` (token gồm `approve` cho linfox); 3-state qua `parse_trigger_status` khi `columns` có key `trigger_status` (bv_c, cột "Expected Status": No Trigger / Trigger (No Violation) / Trigger (Violation)).
- **Chế độ offline**: `--analysis mock` dùng `MockAnalysisClient` (sinh prediction từ ground truth rồi tiêm lỗi theo `far_rate`/`miss_rate`; có mô phỏng cả `triggered` cho 3-state) + `FakeSheet` + `NoOpVideoStore` → chạy end-to-end không cần secret. Chỉ để thử, không phản ánh độ chính xác thật.
- **Report** (`report/reporter.py`): formats `console`/`json`/`csv`/`html`. HTML có bảng metrics + bảng từng video; cột Evidence là **link** (public S3 URL) lấy từ `comparison.detail["evidence_urls"]` do `PublishEvidenceHandler` gắn.
- **Secret**: nạp từ `.env` qua `configs/env.py` (không ghi đè env shell). `API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `FILE_BROWSER_URL`/`FILE_BROWSER_USER`/`FILE_BROWSER_PASSWORD`, `S3_PUBLIC_BASE` (tuỳ chọn).
- **API thật là draft ("FakeAgent")**: bước `upload` và mapping kết quả viết theo OpenAPI hiện có, còn vài giả định (upload bytes vs định danh; endpoint File Browser; tên `evidence_field` trong event) đánh dấu TODO trong `adapters/*_client.py` + `adapters/file_browser.py` — xác nhận với spec thật trước khi tin tưởng.
