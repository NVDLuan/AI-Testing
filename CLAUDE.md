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
.venv/bin/python main.py --tenant linfox_vn --job SMOKE --analysis mock --source fake
.venv/bin/python main.py -t linfox_vn -j SMOKE --rule USEPHONE --rule COLLISION

# Chạy thật (Google Sheet riêng tư + API + S3). API/S3/File Browser/Kafka/Grafana KHÔNG có
# cờ CLI — chỉ đọc từ env file (cp .env.example .env; nhiều bộ secret -> --env-file .env.<tenant>)
.venv/bin/python main.py -t linfox_vn -j SMOKE --env-file .env.lfvn \
    --source gsheet --analysis http \
    --formats console json csv

# Sheet PUBLIC ("ai có link đều xem"): dùng --source gsheet_public, KHÔNG cần service account
.venv/bin/python main.py -t linfox_vn -j SMOKE \
    --source gsheet_public --analysis http --formats console json html

# Report HTML + nhúng link evidence (tải qua File Browser -> upload S3 public): cần env
# FILE_BROWSER_URL/USER/PASSWORD + S3_BUCKET
.venv/bin/python main.py -t linfox_vn -j SMOKE \
    --source gsheet --analysis http \
    --formats console json html

# Tests (plain module, không dùng pytest runner)
.venv/bin/python -m tests.test_metrics
.venv/bin/python -m tests.test_s3
.venv/bin/python -m tests.test_qr_video_tagger   # cần ffmpeg/ffprobe
.venv/bin/python -m tests.test_qr_alignment       # cần ffmpeg/ffprobe cho phần e2e
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
- `chain.py` lắp thứ tự: HealthCheck → LoadGroundTruth → CheckMissing → Upload → UpdateRuleConfig → TriggerAndWait → (ReadEvidenceQR, chỉ khi `--qr-validate`) → Validate → **PublishEvidence** → Report. Sửa flow = sửa list này.
- `handlers/` (package, `__init__` re-export mọi handler): mỗi bước một `Handler`, chia module theo nhóm bước — `base` (lớp `Handler` + cơ chế abort) · `health` · `ingest` (LoadGroundTruth/CheckMissing/Upload) · `analysis` (UpdateRuleConfig/Trigger/Wait/GetResults) · `draw_zone` · `evidence_qr` (ReadEvidenceQR) · `validation` · `publish_evidence` · `reporting`. State truyền qua `PipelineContext` (`context.py`).
- `PublishEvidenceHandler`: chỉ chạy khi report có format `html` **và** có `ctx.evidence_publisher`; tải evidence (File Browser) → upload S3 → gắn URL vào `comparison.detail["evidence_urls"]`. Không cấu hình → bỏ qua êm, HTML vẫn ra (không link).
- **Cơ chế abort**: khi `ctx.abort()` (vd health fail), các handler thường tự bỏ qua nhưng chain vẫn chạy tới cuối; handler đặt `run_on_abort = True` (chỉ `ReportHandler`) vẫn chạy → **luôn có report** (file `*_ABORTED.json` / `.html`).
- `runner.py` mỏng: dùng factory dựng context + chain rồi `chain.handle(ctx)`.

**3. TenantFactory chọn implementation theo khách hàng** (`tenants/`: `base.py` factory mặc định · `options.py` `RunOptions` · `registry.py` `register`/`get_factory` · mỗi tenant 1 file `<load_dir>.py`, import trong `tenants/__init__.py` để đăng ký; `factory.py` chỉ re-export cho import cũ)
- `get_factory(tenant)` tra registry theo `load_dir`; mặc định `TenantFactory`, override bằng `@register("<load_dir>")`.
- Đây là nơi 2 "flavor" API được tách: linfox/EWS dùng `LinfoxHttpClient` (generic `/api/*`), bv_c dùng `HandWashingHttpClient` (`/api/hand-washing/*`) — set qua `http_client_cls`.
- Factory cũng quyết định: ground-truth source (gsheet/fake), video store (S3/no-op), **luật chấm** (`create_validation_chain`, trả `list[ValidationStrategy]` — rỗng = tự chọn 1 strategy theo dữ liệu, không suffix rule; >1 phần tử = CHAIN, mỗi strategy chạy `validate()` riêng, report coi là 1 "rule ảo" `"<rule> - <strategy.name>"`), **evidence publisher** (`create_evidence_publisher`, None nếu mock/chưa cấu hình) và **cách chờ AI xử lý xong** (`create_waiting`, trả `WaitingBase`, gọi ở `WaitHandler` — `ctx.waiting.wait()`, block tới khi có tín hiệu "rảnh" mới sang bước lấy kết quả) theo `RunOptions`.
- `LinfoxVNFactory` / `BvCFactory` map cột Google Sheet thật qua class attr `default_sheet_columns` (áp khi đọc gsheet mà không truyền `sheet_columns`). Tenant cần fetcher evidence riêng thì override `evidence_fetcher_cls()` (vd bv_c dùng `DownloadEvidenceHandWashingFetcher`), không phải copy `create_evidence_publisher`.
- **`create_waiting`** (`adapters/waiting/`, mỗi cách chờ 1 module: `timeout.py`/`kafka.py`/`grafana.py`/`parallel.py`): `TenantFactory` mặc định `WaitingTimeout` (ngủ cố định `RunOptions.waiting_time` "H:M:S"); `LinfoxVNFactory` dùng `WaitingKafka` (poll `{kafka_watcher_api_url}/status`, `total_lag==0` phải giữ nguyên sau `confirm_time`s mới coi là xong, tránh dừng sớm khi lag chỉ tạm về 0); `EwsUsaFactory` dùng `WaitingGrafana` (poll log Loki qua Grafana API — login lấy cookie, dò datasource uid từ trang chủ, query log theo `{server=...,container=~...}`, coi là idle khi thấy 1 trong `target_logs` hoặc cửa sổ log rỗng). `WaitingParallel([...])` chạy nhiều waiter **đồng thời** (thread) và chỉ trả về khi **TẤT CẢ** xong (AND — an toàn hơn race, vd tenant cần cả Kafka lag=0 lẫn Grafana log idle); 1 waiter lỗi vẫn đợi các waiter còn lại xong rồi mới raise, có log rõ waiter nào lỗi. Cấu hình Grafana nạp qua `.env` (`GRAFANA_URL`/`GRAFANA_USERNAME`/`GRAFANA_PASSWORD`/`GRAFANA_SERVER_NAME`/`GRAFANA_SERVICE_NAME`/`GRAFANA_TARGET_LOGS`/`GRAFANA_POLL_INTERVAL_SEC`).

**4. Interfaces + adapters** (`interfaces/` định nghĩa hợp đồng, `adapters/` hiện thực — chia theo package con: `api_agent/` HTTP+mock client, `ground_truth/` nguồn sheet, `storage/` S3 (connector + VideoStore/Uploader), `evidence/` File Browser, `waiting/` chờ AI rảnh, `visualization/` vẽ zone debug)
- `AnalysisClient` (`adapters/api_agent/`): `health_check` / `prime` / `update_rule_config` / `check_missing` / `upload` / `trigger` / `poll_results`. `mock_client.py` cho offline; `linfox_client.py`/`handwashing_client.py` cho 2 flavor HTTP, cùng kế thừa `base.BaseHttpClient`.
- `GroundTruthSource` (`adapters/ground_truth/`): `GoogleSheet` (riêng tư, cần gspread + service account) / `PublicGoogleSheet` (sheet public, đọc export CSV qua gviz, chỉ cần requests) / `FakeSheet` (offline). `VideoStore.fetch(video) -> (bytes, filename)` (`adapters/storage/video_store.py`): `S3VideoStore` lấy video vào **RAM** rồi upload thẳng; định tuyến theo URL — thử HTTP public trước, 401/403 thì fallback S3 API với endpoint + path-style suy từ host (`endpoint_for_url`). env `CACHE_DIR` (RunOptions.cache_dir): bật thì lưu video xuống `cache_dir/<bucket>/<key>` và **tái dùng nếu đã có** (bỏ qua tải); bỏ trống = chỉ RAM. `NoOpVideoStore` cho offline. `AnalysisClient.upload(video, content: bytes, filename)` gửi multipart từ bytes.
- `EvidenceFetcher` / `EvidenceUploader`: `FileBrowserFetcher` (`adapters/evidence/file_browser.py`, login lấy cookie `auth=`, tải `/api/raw<path>`, fallback strip `/evidence`) + `S3PublicUploader` (`adapters/storage/video_store.py`, upload public-read, trả public URL, uỷ quyền `S3StorageConnector` trong `adapters/storage/s3_storage.py`). `EvidencePublisher` (`report/evidence.py`) ghép 2 bước, nuốt lỗi từng evidence.
- `ValidationStrategy` (`validator/strategies.py`): luật chấm cắm được — `BinaryViolationStrategy` (linfox) / `TriggerViolationStrategy` (bv_c, base, dùng khi không chain). `validator/hand_washing_strategy.py` (bv_c): thay vì 1 class gộp nhiều tiêu chí qua đa kế thừa, MỖI tiêu chí là 1 strategy độc lập, có `name` riêng — `TriggerStrategy` (trigger đúng/sai, mọi video), `ViolationStrategy` (`pred.has_violation` vs `gt.expected_violation`, thuần boolean — giống `BinaryViolationStrategy` linfox, không có điều kiện phụ nào; chỉ video GT cần trigger & model có trigger — video khác `classify()` trả `[]`), `SequenceStrategy(exact=False|True)` (Step Sequence subsequence/khớp chính xác, cùng phạm vi Violation + sheet có khai Step Sequence), `CountStrategy` (cột `B0..B6` cùng phía ngưỡng 5, cùng phạm vi + sheet có khai cột B) — đọc từ `gt.raw` vs `pred.events` (chuẩn hoá ở `handwashing_client._parse_event` từ `detection_rule_infos[].params`). `BvCFactory.create_validation_chain` xếp các strategy này thành CHAIN theo `--hw-layers` (chuỗi token phân tách dấu phẩy: `trigger,violation,sequence,sequence_exact,count`; mặc định CLI `trigger,violation`, `RunOptions.hw_layers` mặc định `trigger,violation,sequence_exact,count`). Mỗi strategy trong chain chạy `validate()` riêng (`pipeline.handlers.ValidateHandler` -> `ctx.reports[strategy.name]`) — `report/template.py` biến mỗi report thành 1 batch riêng trong `test_results_*.json`, tên `"<rule.code> - <strategy.name>"` (vd "Hygiene - Trigger", "Hygiene - Violation"); dashboard không cần sửa vì render generic theo từng entry `details{}`. Thêm tiêu chí mới = thêm 1 class + đăng ký token trong `create_validation_chain`, không đụng strategy khác; output giàu nằm ở `Comparison.detail` (free dict -> JSON/HTML).
- **QC note layer Violation (bv_c)** (`report/notes_handwashing.py`): outcome (TP/FP/FN/TN) vẫn CHỈ tính theo `pred.has_violation` vs `gt.expected_violation` (thuần boolean, không đổi) — nhưng `expect_note`/`actual_note` giờ kèm thêm step count (`B0..B6`) + Session Duration (cột sheet `"Session Duration"`, giây) để QC xem đủ ngữ cảnh (không ảnh hưởng outcome). `validation_note` (cột QC Note) ghép 2 dòng độc lập bởi `" - "` (dashboard tách thành nhiều `<div>` qua `formatNoteLines`/`renderNoteDetailLines`): (1) **check logic output** — CHỈ khi model tự báo Violation, đối chiếu với CHÍNH count/duration nó tự báo (không so GT): model báo "rửa tay không đạt" thì PHẢI có ít nhất 1 chỉ số CHƯA đủ ngưỡng (count < `COUNT_THRESHOLD`=5, hằng số public trong `validator/hand_washing_strategy.py`, ở bước nào đó HOẶC duration làm tròn < 30s) mới hợp lý — nếu CẢ count lẫn duration đều ĐỦ ngưỡng (số liệu cho thấy đã rửa tay đạt) mà vẫn báo Violation thì mâu thuẫn, note "sai logic code"; No Violation bỏ qua dòng này. (2) **cảnh báo lệch count** — so step count actual (model tự báo) với expected (cột B sheet), lệch quá dung sai ±1 (`abs(actual-expected) > 1`) ở bất kỳ step nào thì note "Warning: count steps lệch nhiều".

**5. QR frame_id** (`qr_video_tagger/`, package độc lập — không import gì từ code dự án, xem `qr_video_tagger/README.md`)
- Mỗi frame được nhúng QR (góc trên-phải) có nội dung = **số thứ tự frame trong video đó, bắt đầu từ 0** (chuỗi số thuần), để đọc lại evidence -> biết đoạn evidence ứng với frame nào của video gốc.
- `QRVideoGenerator.process_video` (1 video), `LongVideoQRBuilder.run` (video bị cắt `<base>_<part>.mp4`, >= 2 phần -> ghép rồi nhúng; QR đếm liên tục qua các phần), `QRVideoReader` (`read_first_last(bytes)` -> `(int|None, int|None)`, `read_all_from_bytes`, `read_fast`). Vị trí/kích thước QR dùng chung `QRLayout` cho cả tạo lẫn đọc — đổi thì phải truyền cùng layout cho cả hai.
- Cần `ffmpeg`/`ffprobe` trên PATH; `pyzbar` cần `libzbar0` (thiếu thì tự fallback `cv2.QRCodeDetector`).
- **Chưa nối bước nhúng QR vào pipeline**: `GenerateQRCodeIntoVideoHandler` (nhúng QR vào video thiếu trước Upload) KHÔNG có trong code và cờ `--embed-qr` đã bị bỏ — video upload là bản gốc; muốn chấm `--qr-validate` thì video nguồn phải được nhúng QR sẵn (dùng `qr_video_tagger` bên ngoài pipeline). `tests/test_qr_video_tagger.py::test_pipeline_handler` và `chain.build_full_flow_chain` còn tham chiếu bước này nên đang hỏng.
- **Chấm thời điểm vi phạm qua `--qr-validate`** (`RunOptions.qr_validate`/`qr_fps`=25/`qr_tolerance_sec`=1): `ReadEvidenceQRHandler` (sau GetResults, trước Validate) tải từng clip evidence qua `ctx.evidence_fetcher` (`factory.create_evidence_fetcher`, cần env `FILE_BROWSER_URL`; mock -> None -> bỏ qua), `QRVideoReader.read_first_last` -> gắn `event["qr_first_frame"/"qr_last_frame"]` (frame GỐC; None nếu clip không có QR/lỗi tải; 1 clip đọc 1 lần dù nhiều event trỏ tới). `TenantFactory.create_validation_chain` trả `[BinaryViolationStrategy, QRAlignmentStrategy]` -> report thêm layer `"<rule> - QR Alignment"`. `validator/qr_alignment_strategy.py` (thuần, không I/O): khoảng kỳ vọng = cột sheet `ExpectedResult` trong `gt.raw` (JSON hoặc python-literal list `{eventStart, eventEnd: "MM:SS", area}`, giống repo cũ) đổi sang frame theo `qr_fps`. GT vi phạm + có evidence: khoảng QR của ÍT NHẤT 1 clip giao 1 khoảng kỳ vọng (± tolerance) -> `TP`; không clip nào giao -> `["FN","FP"]` (bỏ sót + báo sai thời điểm); không verify được (thiếu `ExpectedResult` / không clip nào có QR) -> `TP` như binary, ghi `detail.qr_status`. GT không vi phạm -> như binary. bv_c KHÔNG hỗ trợ (`BvCFactory.create_evidence_fetcher` trả None + warning). Lưu ý: QR = frame index thật của video nguồn, còn `qr_fps` là giả định chung (mặc định 25 như repo cũ) — video nguồn khác fps thì phải truyền `--qr-fps` đúng.

## Quy ước quan trọng

- **`video_id` vs `name`** (`validator/models.VideoRef`): `video_name` từ sheet chỉ unique *trong rule*, nên khoá nội bộ `video_id = "<rule_code>/<video_name>"` (toàn cục) để không đụng giữa các rule; `name` mới là tên gửi cho API. HTTP client dùng `v.name` cho API và key prediction theo `v.video_id`.
- **Định nghĩa vi phạm** (per flavor, trong `poll_results`): linfox = tồn tại *evidence* cho video (khớp `video_code` hoặc tên file trong `original_video_path`, phân trang `/api/evidences`); hand-washing = `event.violation == True`, và `Prediction.triggered` = có event nào không. So khớp tên qua `api_agent.base.norm_name` (bỏ path + đuôi).
- **Health check** (`adapters/api_agent/base.py`): gọi `/api/get-overall-health-status`; **dừng** (abort → report `*_ABORTED`) nếu `deepstream_is_healthy=False` HOẶC bất kỳ field nào trong {cpu/ram/gpu/disk}`_usage_percentage` > `RESOURCE_USAGE_THRESHOLD_PERCENT` (80%). Field vượt ngưỡng ghi vào `detail["over_threshold"]`.
- **Luật chấm = strategy** (`validator/strategies.py`, gọi từ `validate()`): mỗi video → `classify()` trả `TP/FP/FN/TN`; `validate()` chỉ gom `ConfusionMatrix` overall + theo rule + theo camera. positive = "có vi phạm". `FAR = FP/(TP+FP)`, `Recall = TP/(TP+FN)`.
  - **bv_c 3-state** (`TriggerViolationStrategy`, **1 ma trận gộp**): `TP`=trigger đúng & violation khớp; `FP`=có trigger nhưng sai (violation sai *hoặc* lẽ ra không trigger); `FN`=cần trigger mà không trigger; `TN`=không trigger đúng. Lưu ý: "trigger đúng nhưng bỏ sót vi phạm" tính **FP** (không phải FN).
- **Cột Google Sheet** (`adapters/ground_truth/rows.py`, map ở factory): nhãn binary qua `parse_bool` (token gồm `approve` cho linfox); 3-state qua `parse_trigger_status` khi `columns` có key `trigger_status` (bv_c, cột "Expected Status" — vocab thật: `No trigger`→(F,F) · `No violation`→(T,F) · `Violation`→(T,T)). Cột expected cho HandWashingStrategy (`B1..B6`, `Step Sequence`) nằm nguyên trong `gt.raw`.
- **Chế độ offline**: `--analysis mock` dùng `MockAnalysisClient` (sinh prediction từ ground truth rồi tiêm lỗi theo `far_rate`/`miss_rate`; có mô phỏng cả `triggered` cho 3-state) + `FakeSheet` + `NoOpVideoStore` → chạy end-to-end không cần secret. Chỉ để thử, không phản ánh độ chính xác thật.
- **Report — 1 schema JSON chuẩn duy nhất cho HTML** (`report/template.py`, `report/dashboard_template.html`): với lượt chạy THÀNH CÔNG, hễ `--formats` có `json`/`template`/`html` (bất kỳ 1 trong 3, không cần đủ cả) là `ReportHandler` dựng `reports/test_results_<stamp>.json` theo mẫu QC (`{version_run, summary, details{batch_code:{...test_case_validation_result...}}, health}`, qua `report.template.write_template`); nếu có `html` thì luôn nạp đúng file JSON đó vào `report/dashboard_template.html` (thay `{{ TEST_REPORT_DATA_PLACEHOLDER }}`, qua `report/generate_report.py`) ra `test_results_<stamp>.html` — đây là HTML report DUY NHẤT cho lượt chạy thành công (không còn HTML bảng đơn giản kiểu cũ). `console`/`csv` vẫn qua `report/reporter.py` (`write_reports`) song song, độc lập với JSON/HTML. Khi pipeline **abort** (health fail) thì không có comparison để dựng schema QC → `json`/`html` rơi về bản rút gọn của `write_reports` (`*_ABORTED.json` / `.html`), tách biệt khỏi schema QC. JSON ghi **compact** (không indent — `cam_zones` indent làm file phình ~4.5x); `rule_config.config`/`api_response.config` chỉ giữ camera có video trong lượt chạy (`_filter_cameras`, giữ cả key `<cam>_1/_2` cho fallback dashboard). Payload evidence thô (`Prediction.events`) KHÔNG nằm trong test_results mà tách ra `evidence_raw_<stamp>.json` (`write_evidence_raw`, `{version_run, evidences{rule_code:{video_name:[event]}}}`, cùng stamp) — tên không bắt đầu bằng `test_results_` để `find_latest_json` không bắt nhầm.
- **Secret + endpoint**: CHỈ nạp từ env file (`--env-file`, mặc định `.env`) qua `configs/env.py` (không ghi đè env shell) — không có cờ CLI tương ứng. `AGENT_API`, `API_KEY`, `S3_BUCKET`/`S3_PREFIX`/`AWS_DEFAULT_REGION`, `CACHE_DIR`, `GSHEET_CREDENTIALS` (service account JSON cho `--source gsheet`, mặc định `configs/service-google-sheet.json`), `KAFKA_WATCHER_API_URL`, `TIME_WAITING`, `GRAFANA_*`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `FILE_BROWSER_URL`/`FILE_BROWSER_USER`/`FILE_BROWSER_PASSWORD`, `S3_PUBLIC_BASE` (tuỳ chọn).
  - **`S3_ENDPOINT` chỉ dùng cho S3-compatible tự host (MinIO...)**, host KHÔNG được chứa tên bucket. Với AWS thật thì để trống/comment out — nếu set thành URL virtual-hosted-style đã có sẵn bucket (vd `https://<bucket>.s3.amazonaws.com`), boto3 vẫn ghép thêm `/<bucket>` vào path (vì có `endpoint_url` tuỳ chỉnh) → request bị double-bucket-in-path (`.../<bucket>/<bucket>`) → 403/404 kiểu "không có bucket". Để trống thì boto3 tự resolve đúng theo `AWS_DEFAULT_REGION` (`https://<bucket>.s3.<region>.amazonaws.com/`).
- **Upload bv_c cần keypoints**: `POST /api/hand-washing/videos/upload` nhận 2 field multipart bắt buộc — `video` (bytes từ fetch) + `keypoints` (file `.txt`). Client đọc keypoints theo quy ước `recordings_dir/<tên_video>.txt` (mặc định `downloads/recordings/`, đổi qua `--recordings-dir`); thiếu file -> raise. Code sinh keypoints ghi vào thư mục này (sẽ bổ sung sau). linfox upload chỉ gửi `videos` (1 file).
- **API thật là draft ("FakeAgent")**: bước `upload` và mapping kết quả viết theo OpenAPI hiện có, còn vài giả định (upload bytes vs định danh; endpoint File Browser) đánh dấu TODO trong `adapters/api_agent/*_client.py` + `adapters/evidence/file_browser.py` — xác nhận với spec thật trước khi tin tưởng. `evidence_field` mặc định `"evidence"` — cả `linfox_client.py` (đọc `payload.videoMetadata.filename`) lẫn `handwashing_client.py` (đọc `video_metadata.filename`) đều gắn evidence path vào key này khi build `Prediction.events`, nên không cần đổi `--evidence-field` theo tenant.
