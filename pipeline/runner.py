"""Runner mỏng: dựng context bằng factory rồi chạy chain.

Toàn bộ logic từng bước nằm ở handlers.py; nơi đây chỉ lắp ráp.
"""
from __future__ import annotations

import logging
import time

from configs.loader import ConfigLoader
from configs.schema import JobDef, RuleDef, TenantConfig
from tenants.factory import RunOptions, get_factory

from .context import PipelineContext

log = logging.getLogger("ai_testing.runner")


def run_job(
    loader: ConfigLoader,
    tenant: TenantConfig,
    job: JobDef,
    rules: list[RuleDef],
    opts: RunOptions,
    report_formats: list[str],
    report_dir: str = "reports",
) -> PipelineContext:
    factory = get_factory(tenant)
    log.info("Tenant=%s job=%s factory=%s", tenant.name, job.name, type(factory).__name__)

    ctx = PipelineContext(
        loader=loader,
        tenant=tenant,
        job=job,
        rules=rules,
        client=factory.create_client(tenant, opts),
        gt_source=factory.create_ground_truth_source(tenant, opts),
        video_store=factory.create_video_store(tenant, opts),
        strategy=factory.create_validation_strategy(tenant, opts),
        evidence_publisher=factory.create_evidence_publisher(tenant, opts),
        report_formats=report_formats,
        report_dir=report_dir,
        started_at=time.time(),
        waiting = factory.create_waiting(tenant, opts),
    )
    chain = factory.create_chain(tenant, opts)
    return chain.handle(ctx)
