"""CLI cho bộ test phân tích video (computer vision).

Ví dụ:
    # Liệt kê tenant / job / rule
    python main.py --list

    # Chạy offline (fake sheet + mock client) — không cần mạng/credential
    python main.py --tenant linfox_vn --job SMOKE -v

    # Chạy 1 vài rule
    python main.py --tenant "linfox Viet Nam" --job SMOKE --rule USEPHONE --rule COLLISION

    # Chạy thật: đọc Google Sheet + gọi API bên thứ 3
    python main.py --tenant linfox_vn --job SMOKE \
        --source gsheet --credentials sa.json \
        --analysis http --api-url https://api.example.com --api-key $KEY \
        --formats console json csv
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from configs.env import load_dotenv
from configs.loader import ConfigLoader
from configs.schema import RootConfig, TenantConfig
from pipeline.runner import run_job
from tenants.factory import RunOptions


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Video CV analysis testing")
    p.add_argument("--config", "-c", default="configs/config.yaml", help="File config gốc")
    p.add_argument("--env-file", default=".env",
                   help="File .env để nạp secret (mặc định '.env') — đổi để chạy nhiều bộ "
                        "secret khác nhau (vd .env.karter, .env.linfox) mà không cần đổi tên/copy đè")
    p.add_argument("--list", action="store_true", help="Liệt kê tenant/job/rule rồi thoát")
    p.add_argument("--tenant", "-t", help="Tên hoặc load_dir của tenant")
    p.add_argument("--job", "-j", help="Tên job (SMOKE, REGRESSION...)")
    p.add_argument("--rule", action="append", default=[], help="Lọc rule (name/code, lặp lại được)")

    p.add_argument("--analysis", choices=["mock", "http"], default="http")
    p.add_argument("--source", choices=["fake", "gsheet", "gsheet_public"], default="gsheet",
                   help="fake (offline) | gsheet (riêng tư, cần --credentials) | gsheet_public (public, không cần credential)")
    p.add_argument("--api-url", default="", help="Gốc host API, vd http://103.104.122.36:8000")
    p.add_argument("--api-key", default="", help="X-API-Key (hoặc đặt env API_KEY)")
    p.add_argument("--credentials", default="", help="Service account JSON (gsheet)")
    p.add_argument("--poll-interval", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--skip-health", action="store_true", help="Bỏ qua bước health check")
    p.add_argument("--s3-bucket", default="", help="Bucket mặc định khi sheet chỉ ghi key")
    p.add_argument("--s3-region", default="")
    p.add_argument("--s3-prefix", default="", help="Prefix ghép vào key thuần")
    p.add_argument("--s3-access-key", default="", help="hoặc env AWS_ACCESS_KEY_ID")
    p.add_argument("--s3-secret-key", default="", help="hoặc env AWS_SECRET_ACCESS_KEY")
    p.add_argument("--s3-endpoint", default="", help="endpoint S3 tuỳ chọn (MinIO...)")
    p.add_argument("--cache-dir", default="",
                   help="Lưu video tải về vào thư mục này + tái dùng (bỏ trống = giữ RAM, không ghi đĩa)")
    p.add_argument("--recordings-dir", default="video_raw/rainscales-static-assets/qc-automation/06. AutoHygiene",
                   help="Thư mục chứa file keypoints .txt kèm video (bv_c upload cần)")
    p.add_argument("--hw-layers", default="trigger,violation",
                   help="Chain luật chấm bv_c, phân tách bởi dấu phẩy — mỗi layer ra 1 "
                        "rule ảo '<rule> - <Name>' riêng trong report. Token hợp lệ: "
                        "trigger | violation | sequence (subsequence) | sequence_exact "
                        "(khớp chính xác tập bước) | count")

    p.add_argument("--formats", nargs="+", default=["console"],
                   help="console json csv html template "
                        "(json/template/html đều dùng chung 1 schema QC test_results_*.json; "
                        "html luôn nạp schema đó vào dashboard, không cần kèm json)")
    p.add_argument("--report-dir", default="reports")

    # Evidence video -> S3 -> link trong report HTML
    p.add_argument("--file-browser-url", default="", help="Gốc API File Browser tải evidence")
    p.add_argument("--file-browser-user", default="", help="Username File Browser (hoặc env FILE_BROWSER_USER)")
    p.add_argument("--file-browser-password", default="", help="Password File Browser (hoặc env FILE_BROWSER_PASSWORD)")
    p.add_argument("--evidence-field", default="evidence", help="Tên field trong event chứa file_path evidence")
    p.add_argument("--report-s3-bucket", default="", help="Bucket lưu evidence (mặc định dùng --s3-bucket)")
    p.add_argument("--report-s3-prefix", default="reports/evidence", help="Prefix key evidence trên S3")
    p.add_argument("--s3-public-base", default="", help="Domain/CloudFront công khai để dựng URL")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--log-file", action="store_true",
                   help="Ghi thêm log ra file trong thư mục logs/ (mặc định chỉ in ra stdout)")
    return p.parse_args(argv)


def _print_listing(root: RootConfig, loader: ConfigLoader) -> None:
    for t in root.tenants:
        print(f"\n● {t.name}  (load_dir={t.load_dir}, version={t.version})")
        print(f"  jobs: {', '.join(j.name for j in t.jobs) or '(none)'}")
        try:
            rules = loader.load_rules(t)
            print(f"  rules: {', '.join(f'{r.name}[{r.code}]' for r in rules) or '(none)'}")
        except FileNotFoundError:
            print("  rules: (chưa có rules.yaml)")


def _resolve_rules(loader: ConfigLoader, tenant: TenantConfig, rule_filter: list[str]):
    loader.load_rules(tenant)
    if not rule_filter:
        return tenant.rules
    selected = []
    for key in rule_filter:
        r = tenant.rule(key)
        if r is None:
            raise SystemExit(f"Không tìm thấy rule {key!r} trong tenant {tenant.name!r}")
        selected.append(r)
    return selected


def _setup_logging(log_file: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        filename = datetime.now().strftime("%Y%m%d-%H%M%S") + ".log"
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / filename, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    load_dotenv(args.env_file)  # nạp secret từ file .env chỉ định trước khi đọc os.environ
    _setup_logging(args.log_file)

    loader = ConfigLoader(args.config)
    root = loader.load_root()

    if args.list:
        _print_listing(root, loader)
        return 0

    if not args.tenant or not args.job:
        raise SystemExit("Cần --tenant và --job (hoặc dùng --list). Xem --help.")

    tenant = root.tenant(args.tenant)
    if tenant is None:
        raise SystemExit(f"Không tìm thấy tenant {args.tenant!r}. Dùng --list để xem.")

    job = tenant.job(args.job)
    if job is None:
        raise SystemExit(f"Tenant {tenant.name!r} không có job {args.job!r}.")

    rules = _resolve_rules(loader, tenant, args.rule)
    opts = RunOptions(
        analysis=args.analysis,
        source=args.source or 'gsheet_public',
        api_url=args.api_url or os.environ.get("AGENT_API", ""),
        api_key=args.api_key or os.environ.get("API_KEY", ""),
        credentials=args.credentials or 'configs/service-google-sheet.json',
        poll_interval_sec=args.poll_interval,
        timeout_sec=args.timeout,
        skip_health=args.skip_health,
        s3_bucket=args.s3_bucket or os.environ.get("S3_BUCKET", ""),
        s3_region=args.s3_region or os.environ.get("AWS_DEFAULT_REGION", ""),
        s3_prefix=args.s3_prefix or os.environ.get("S3_PREFIX", ""),
        s3_access_key=args.s3_access_key or os.environ.get("AWS_ACCESS_KEY_ID", ""),
        s3_secret_key=args.s3_secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        s3_endpoint=args.s3_endpoint or os.environ.get("S3_ENDPOINT", ""),
        cache_dir=args.cache_dir or os.environ.get("CACHE_DIR", ""),
        recordings_dir=args.recordings_dir,
        hw_layers=args.hw_layers,
        file_browser_url=args.file_browser_url or os.environ.get("FILE_BROWSER_URL", ""),
        file_browser_user=args.file_browser_user or os.environ.get("FILE_BROWSER_USER", ""),
        file_browser_password=args.file_browser_password or os.environ.get("FILE_BROWSER_PASSWORD", ""),
        evidence_field=args.evidence_field,
        report_s3_bucket=args.report_s3_bucket,  # rỗng -> factory fallback dùng chung s3_bucket
        report_s3_prefix="reports",
        s3_public_base=args.s3_public_base or os.environ.get("S3_PUBLIC_BASE", ""),
        waiting_time=os.environ.get("TIME_WAITING", ""),
        kafka_watcher_api_url=os.environ.get("KAFKA_WATCHER_API_URL", ""),
        grafana_url=os.environ.get("GRAFANA_URL", ""),
        grafana_username=os.environ.get("GRAFANA_USERNAME", ""),
        grafana_password=os.environ.get("GRAFANA_PASSWORD", ""),
        grafana_server_name=os.environ.get("GRAFANA_SERVER_NAME", ""),
        grafana_service_name=os.environ.get("GRAFANA_SERVICE_NAME", ""),
        grafana_target_logs=os.environ.get("GRAFANA_TARGET_LOGS", ""),
        grafana_poll_interval_sec=float(os.environ.get("GRAFANA_POLL_INTERVAL_SEC", "120")),
    )

    ctx = run_job(loader, tenant, job, rules, opts, args.formats, args.report_dir)
    # Exit codes (tiện CI): 2 = pipeline bị dừng (vd health fail); 1 = thiếu kết quả; 0 = ok.
    if ctx.aborted:
        return 2
    return 1 if any(rep.missing for rep in ctx.reports.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
