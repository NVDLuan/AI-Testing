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

from configs.env import load_dotenv
from configs.loader import ConfigLoader
from configs.schema import RootConfig, TenantConfig
from pipeline.runner import run_job
from tenants.factory import RunOptions


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Video CV analysis testing")
    p.add_argument("--config", "-c", default="configs/config.yaml", help="File config gốc")
    p.add_argument("--list", action="store_true", help="Liệt kê tenant/job/rule rồi thoát")
    p.add_argument("--tenant", "-t", help="Tên hoặc load_dir của tenant")
    p.add_argument("--job", "-j", help="Tên job (SMOKE, REGRESSION...)")
    p.add_argument("--rule", action="append", default=[], help="Lọc rule (name/code, lặp lại được)")

    p.add_argument("--analysis", choices=["mock", "http"], default="mock")
    p.add_argument("--source", choices=["fake", "gsheet"], default="fake")
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

    p.add_argument("--formats", nargs="+", default=["console"], help="console json csv")
    p.add_argument("--report-dir", default="reports")
    p.add_argument("-v", "--verbose", action="store_true")
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


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # nạp secret từ .env trước khi đọc os.environ
    args = parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

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
        source=args.source,
        api_url=args.api_url,
        api_key=args.api_key or os.environ.get("API_KEY", ""),
        credentials=args.credentials,
        poll_interval_sec=args.poll_interval,
        timeout_sec=args.timeout,
        skip_health=args.skip_health,
        s3_bucket=args.s3_bucket,
        s3_region=args.s3_region or os.environ.get("AWS_DEFAULT_REGION", ""),
        s3_prefix=args.s3_prefix,
        s3_access_key=args.s3_access_key or os.environ.get("AWS_ACCESS_KEY_ID", ""),
        s3_secret_key=args.s3_secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        s3_endpoint=args.s3_endpoint or os.environ.get("S3_ENDPOINT", ""),
    )

    ctx = run_job(loader, tenant, job, rules, opts, args.formats, args.report_dir)
    # Exit codes (tiện CI): 2 = pipeline bị dừng (vd health fail); 1 = thiếu kết quả; 0 = ok.
    if ctx.aborted:
        return 2
    return 1 if ctx.report and ctx.report.missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
