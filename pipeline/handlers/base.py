"""Lớp cơ sở `Handler` (Chain of Responsibility) + cơ chế bỏ qua khi abort."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..context import PipelineContext


class Handler(ABC):
    """Một mắt xích. Trả về context để các handler nối tiếp nhau.

    Khi pipeline đã abort (vd health fail), các handler thường bị BỎ QUA nhưng chain
    vẫn chạy tiếp tới cuối; handler đặt `run_on_abort = True` (vd Report) vẫn chạy.
    """

    run_on_abort: bool = False

    def __init__(self) -> None:
        self._next: Handler | None = None

    def set_next(self, nxt: "Handler") -> "Handler":
        self._next = nxt
        return nxt  # cho phép nối chuỗi .set_next().set_next()

    def handle(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.aborted and not self.run_on_abort:
            ctx.log.info("⊘ bỏ qua %s (đã dừng: %s)", self.__class__.__name__, ctx.abort_reason)
        else:
            ctx.log.info("→ %s", self.__class__.__name__)
            self.process(ctx)
        if self._next is not None:
            return self._next.handle(ctx)
        return ctx

    @abstractmethod
    def process(self, ctx: PipelineContext) -> None:
        ...
