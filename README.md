# AI-Testing — Bộ test kết quả phân tích video (Computer Vision)

Framework test linh hoạt để đánh giá độ chính xác của hệ thống phân tích video
bên thứ 3. Cấu hình theo **tenant (khách hàng) → job → rule → camera → zone**,
mọi điểm tích hợp đều là interface và được lắp ráp qua **factory theo tenant**.

## Flow (Chain of Responsibility)

```
HealthCheck → LoadGroundTruth → CheckMissing → Upload → UpdateRuleConfig → TriggerAndWait → Validate → Report
```

Mỗi bước là một `Handler` trong `pipeline/handlers.py`. Test **theo từng rule**
(`videos_by_rule`). **HealthCheck** chạy đầu tiên; hệ thống không khoẻ → bỏ qua các
bước test nhưng **vẫn gen report** (file `*_ABORTED.json` ghi lý do + trạng thái
health), exit code `2`.
**UpdateRuleConfig** đẩy config camera (`POST /api/rule`) trước khi trigger — chỉ áp
dụng cho generic API (linfox/EWS); hand-washing để no-op nên tự bỏ qua.
Thêm/bớt/đổi thứ tự = sửa `pipeline/chain.py` (`--skip-health` để bỏ qua health).
Đơn vị so khớp: **mỗi video một nhãn boolean** (có/không vi phạm).

- **Recall** = TP / (TP + FN) — bắt đúng ca vi phạm
- **FAR** (False Alarm Rate) = FP / (FP + TN) — báo động giả

## Cấu hình (3 tầng)

| File | Nội dung |
|---|---|
| `configs/config.yaml` | Danh sách **tenant**: `jobs` (sheet_id), `version`, `load_dir`… |
| `configs/tenants/<load_dir>/rules.yaml` | Danh sách **rule**: `name`, `code`, `sheet` (tab), `type`, `time_sleep`… |
| `configs/tenants/<load_dir>/<version>/<CODE>/<CODE>_config.json` | Config **camera/zone** theo rule |

**Google Sheet**: mỗi rule nằm ở 1 tab riêng (`rule.sheet`). Mỗi dòng = 1 video với
cột `path_video` (nguồn S3 để tải) + `video_name` (unique *trong rule*) + cột nhãn
`expected`. Vì `video_name` chỉ unique trong rule, khoá nội bộ được namespace
`rule_code/video_name` để không đụng giữa các rule.

Loader (`configs/loader.py`) tự resolve path tương đối theo vị trí `config.yaml`.

## Secrets (.env)

Các khoá bí mật (`API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) nạp tự động
từ file `.env` ở thư mục gốc — **đã được `.gitignore`, không commit**. Copy template:

```bash
cp .env.example .env   # rồi điền giá trị thật
```

Biến set sẵn ngoài shell vẫn ưu tiên hơn `.env`. Có thể vẫn truyền qua CLI
(`--api-key`, `--s3-access-key`…) nếu muốn override.

## Chạy

```bash
.venv/bin/pip install -r requirements.txt

# Liệt kê tenant / job / rule
python main.py --list

# Offline (fake sheet + mock client) — không cần mạng/credential
python main.py --tenant linfox_vn --job SMOKE -v

# Lọc rule (theo name hoặc code)
python main.py -t linfox_vn -j SMOKE --rule USEPHONE --rule COLLISION

# Chạy thật: Google Sheet + API bên thứ 3
export API_KEY=...                      # hoặc dùng --api-key
python main.py -t linfox_vn -j SMOKE \
    --source gsheet --credentials sa.json \
    --analysis http --api-url http://103.104.122.36:8000 \
    --formats console json csv
```

Report in console + ghi `reports/`. Exit code: `2` = pipeline bị dừng (vd health fail),
`1` = có video thiếu kết quả, `0` = ok (tiện CI).

### API bên thứ 3 (FakeAgent)

Mỗi tenant trỏ tới một "flavor" API khác nhau, được chọn tự động qua factory:

| Tenant | Client | Endpoints |
|---|---|---|
| linfox, EWS | `LinfoxHttpClient` | `/api/videos/check`, `/api/videos/analyze`, `/api/evidences` |
| bv_c | `HandWashingHttpClient` | `/api/hand-washing/videos/{check,trigger}`, `/api/hand-washing/events` |

- **Auth**: header `X-API-Key` (CLI `--api-key` hoặc env `API_KEY`).
- **Health**: `GET /health` (public) + `GET /api/get-overall-health-status`
  (cần key, kiểm `deepstream_is_healthy` + tài nguyên).
- **Quy ước vi phạm**: linfox = tồn tại *evidence* cho video; bv_c = *event.violation == True*.

### Nguồn video (S3)

Video gốc nằm trên S3. Sau bước `CheckMissing`, `UploadHandler` **tải các video
thiếu từ S3** (`S3VideoStore`, dùng boto3) rồi upload bytes thật lên API, và xoá
file tạm sau khi upload.

```bash
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...   # hoặc --s3-access-key/--s3-secret-key
python main.py -t linfox_vn -j SMOKE \
    --source gsheet --credentials sa.json \
    --analysis http --api-url http://103.104.122.36:8000 \
    --s3-bucket my-bucket --s3-region ap-southeast-1
```

S3 **bắt buộc** access key + secret key (CLI `--s3-access-key/--s3-secret-key` hoặc env
`AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY`); `--s3-endpoint` cho S3 tự host (MinIO).
`path_video` chấp nhận `s3://bucket/key`, URL https S3 (virtual-hosted / path-style),
hoặc **key thuần** (khi đó dùng `--s3-bucket` + `--s3-prefix`). Khi `--analysis mock`,
video store là no-op (không tải gì).

## Cấu trúc

| Thư mục | Vai trò |
|---|---|
| `configs/` | Schema (Tenant/Job/Rule/Camera) + loader 3 tầng |
| `interfaces/` | `AnalysisClient`, `GroundTruthSource` — hợp đồng trừu tượng |
| `adapters/` | `MockAnalysisClient`, `LinfoxHttpClient`, `HandWashingHttpClient`, `GoogleSheet` / `FakeSheet` source |
| `tenants/` | `TenantFactory` + registry override theo `load_dir` |
| `pipeline/` | `context`, `handlers` (chain), `chain`, `runner` (lắp ráp) |
| `validator/` | Models, confusion matrix, logic so khớp |
| `report/` | Xuất console / JSON / CSV |
| `tests/` | Test metrics/validator |

## Mở rộng

- **Thêm tenant** có hành vi riêng: tạo subclass `TenantFactory`, gắn
  `@register("<load_dir>")` trong `tenants/factory.py` (xem `LinfoxVNFactory`).
  Runner tự chọn factory đúng qua `get_factory(tenant)` — không cần if/else.
- **Cắm API thật**: `--analysis http --api-url ... --api-key ...`. Hai client đã viết
  theo spec FakeAgent (`adapters/linfox_client.py`, `adapters/handwashing_client.py`);
  tenant mới chỉ cần subclass `TenantFactory` và đặt `http_client_cls`.
- **Cắm Google Sheet thật**: `--source gsheet --credentials sa.json`, chỉnh map
  cột trong `GoogleSheetGroundTruthSource` (hoặc override trong factory tenant).
- **Đổi flow**: sửa danh sách handler trong `pipeline/chain.py`, hoặc override
  `TenantFactory.create_chain()` cho riêng một tenant.

## Mock / Fake (offline)

`FakeSheetGroundTruthSource` sinh ground truth từ **camera_id thật** trong các file
`<CODE>_config.json`. `MockAnalysisClient` sinh prediction từ nhãn thật rồi tiêm lỗi
theo `far_rate`/`miss_rate` (trong `RunOptions`). Chỉ để chạy thử — không phản ánh
độ chính xác hệ thống thật.

## Test

```bash
python -m tests.test_metrics      # hoặc: pytest -q
```
