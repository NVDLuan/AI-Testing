# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mục đích

Framework test độ chính xác của hệ thống phân tích video (computer vision) bên thứ 3.
Flow: load config → đọc ground truth (Google Sheet) → check video thiếu → tải từ S3 →
upload → trigger phân tích → chờ kết quả → so khớp với ground truth → report FAR/Recall.
Đơn vị so khớp là **mỗi video một nhãn boolean** (có/không vi phạm).

## Lệnh

```bash
.venv/bin/pip install -r requirements.txt

# Liệt kê tenant / job / rule
.venv/bin/python main.py --list

# Chạy offline (mock client + fake ground truth, không cần mạng/secret)
.venv/bin/python main.py --tenant linfox_vn --job SMOKE -v
.venv/bin/python main.py -t linfox_vn -j SMOKE --rule USEPHONE --rule COLLISION

# Chạy thật (Google Sheet + API + S3); secret nạp từ .env (cp .env.example .env)
.venv/bin/python main.py -t linfox_vn -j SMOKE \
    --source gsheet --credentials sa.json \
    --analysis http --api-url http://103.104.122.36:8000 \
    --s3-bucket BUCKET --s3-region ap-southeast-1 \
    --formats console json csv

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
- `chain.py` lắp thứ tự: HealthCheck → LoadGroundTruth → CheckMissing → Upload → UpdateRuleConfig → TriggerAndWait → Validate → Report. Sửa flow = sửa list này.
- `handlers.py`: mỗi bước một `Handler`; state truyền qua `PipelineContext` (`context.py`).
- **Cơ chế abort**: khi `ctx.abort()` (vd health fail), các handler thường tự bỏ qua nhưng chain vẫn chạy tới cuối; handler đặt `run_on_abort = True` (chỉ `ReportHandler`) vẫn chạy → **luôn có report** (file `*_ABORTED.json`).
- `runner.py` mỏng: dùng factory dựng context + chain rồi `chain.handle(ctx)`.

**3. TenantFactory chọn implementation theo khách hàng** (`tenants/factory.py`)
- `get_factory(tenant)` tra registry theo `load_dir`; mặc định `TenantFactory`, override bằng `@register("<load_dir>")`.
- Đây là nơi 2 "flavor" API được tách: linfox/EWS dùng `LinfoxHttpClient` (generic `/api/*`), bv_c dùng `HandWashingHttpClient` (`/api/hand-washing/*`) — set qua `http_client_cls`.
- Factory cũng quyết định ground-truth source (gsheet/fake) và video store (S3/no-op) theo `RunOptions`.

**4. Interfaces + adapters** (`interfaces/` định nghĩa hợp đồng, `adapters/` hiện thực)
- `AnalysisClient`: `health_check` / `prime` / `update_rule_config` / `check_missing` / `upload` / `trigger` / `poll_results`. `Mock` cho offline; 2 HTTP client cho 2 flavor.
- `GroundTruthSource`: `GoogleSheet` (thật) / `FakeSheet` (offline). `VideoStore`: `S3VideoStore` (lazy — chỉ validate key/khởi tạo boto3 khi download) / `NoOpVideoStore`.

## Quy ước quan trọng

- **`video_id` vs `name`** (`validator/models.VideoRef`): `video_name` từ sheet chỉ unique *trong rule*, nên khoá nội bộ `video_id = "<rule_code>/<video_name>"` (toàn cục) để không đụng giữa các rule; `name` mới là tên gửi cho API. HTTP client dùng `v.name` cho API và key prediction theo `v.video_id`.
- **Định nghĩa vi phạm** (per flavor, trong `poll_results`): linfox = tồn tại *evidence* cho video; hand-washing = `event.violation == True`. So khớp tên qua `_http_base.norm_name` (bỏ path + đuôi).
- **Metrics** (`validator/metrics.py`): positive = "có vi phạm". `FAR = FP/(FP+TN)`, `Recall = TP/(TP+FN)`. `validate()` gom overall + theo rule + theo camera.
- **Chế độ offline**: `--analysis mock` dùng `MockAnalysisClient` (sinh prediction từ ground truth rồi tiêm lỗi theo `far_rate`/`miss_rate`) + `FakeSheet` + `NoOpVideoStore` → chạy end-to-end không cần secret. Chỉ để thử, không phản ánh độ chính xác thật.
- **Secret**: nạp từ `.env` qua `configs/env.py` (không ghi đè env shell). `API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.
- **API thật là draft ("FakeAgent")**: bước `upload` và mapping kết quả viết theo OpenAPI hiện có, còn vài giả định (upload bytes vs định danh) đánh dấu TODO trong `adapters/*_client.py` — xác nhận với spec thật trước khi tin tưởng.
