"""Chạy phân tích trên hệ thống bên thứ 3: đẩy config rule -> trigger -> chờ -> lấy kết quả."""
from __future__ import annotations

from datetime import datetime

from ..context import PipelineContext
from .base import Handler


class UpdateRuleConfigHandler(Handler):
    """Đẩy config camera của từng rule lên hệ thống (generic API).

    Hand-washing client để no-op nên handler này tự động bị bỏ qua với bv_c.
    """

    def process(self, ctx: PipelineContext) -> None:
        for rule in ctx.rules:
            videos = ctx.videos_by_rule.get(rule.code)
            if not videos:
                continue
            ctx.client.update_rule_config(rule, videos)


class TriggerHandler(Handler):
    """Trigger phân tích theo từng rule rồi chờ kết quả (gộp để giữ ngữ cảnh rule)."""

    @staticmethod
    def make_batch_code(customer: str, rule_code: str) -> str:
        return f"{customer.replace(' ', '_')}_{rule_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        # return "Karter_School_CNT08_20260806_152825"

    def process(self, ctx: PipelineContext) -> None:
        rule_by_code = {r.code: r for r in ctx.rules}
        for code, videos in ctx.videos_by_rule.items():
            if not videos:
                continue
            rule = rule_by_code[code]
            batch_code = self.make_batch_code(ctx.tenant.name, rule.code)
            ctx.client.trigger(videos, rule, batch_code)
            ctx.batch_codes[code] = batch_code
            ctx.log.info("  rule=%s: trigger job=%s", rule.name, batch_code)


class WaitHandler(Handler):

    def process(self, ctx: PipelineContext) -> None:
        ctx.log.info("Waiting Result Return..........................")
        ctx.waiting.wait()


class GetResultsHandler(Handler):

    def process(self, ctx: PipelineContext) -> None:
        rule_by_code = {r.code: r for r in ctx.rules}
        for code, videos in ctx.videos_by_rule.items():
            rule = rule_by_code[code]
            job_id = ctx.batch_codes.get(code, "")
            preds = ctx.client.poll_results(job_id, videos)
            ctx.predictions.update(preds)
            ctx.log.info("  rule=%s: nhận %d kết quả", rule.name, len(preds))
