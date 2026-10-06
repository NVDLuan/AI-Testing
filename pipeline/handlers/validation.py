"""So khớp prediction với ground truth theo chain strategy -> ctx.reports."""
from __future__ import annotations

from validator.strategies import default_strategy
from validator.validator import validate

from ..context import PipelineContext
from .base import Handler


class ValidateHandler(Handler):
    """So khớp prediction với ground truth -> metrics.

    Chạy `validate()` cho TỪNG strategy trong `ctx.strategy_chain` (rỗng -> 1 strategy
    tự chọn theo dữ liệu), mỗi strategy 1 report riêng lưu vào `ctx.reports[name]` —
    report coi mỗi report là 1 "rule ảo" (xem `report/template.py`).
    """

    def process(self, ctx: PipelineContext) -> None:
        chain = ctx.strategy_chain or [default_strategy(ctx.ground_truth)]
        ctx.reports = {}
        for strat in chain:
            rep = validate(ctx.ground_truth, ctx.predictions, strat)
            ctx.reports[strat.name] = rep
            ov = rep.overall
            ctx.log.info(
                "  [%s] TP=%d FP=%d FN=%d TN=%d | Recall=%.3f FAR=%.3f",
                strat.name, ov.tp, ov.fp, ov.fn, ov.tn, ov.recall, ov.far,
            )
