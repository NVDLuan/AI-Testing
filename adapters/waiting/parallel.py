import logging
import threading

from .base import WaitingBase

logger = logging.getLogger("Waiting")


class WaitingParallel(WaitingBase):
    """Chạy nhiều waiter ĐỒNG THỜI (thread), chỉ trả về khi TẤT CẢ đã xong (AND).

    Dùng cho tenant cần nhiều tín hiệu xác nhận AI rảnh cùng lúc (vd Kafka lag=0 VÀ
    Grafana log idle) mà không muốn cộng dồn thời gian chờ của từng cái — mỗi waiter
    tự poll theo nhịp riêng, `wait()` chỉ trả về khi mọi thread đều xong.
    1 waiter lỗi -> log rõ waiter nào lỗi, vẫn đợi các waiter còn lại xong rồi mới raise
    (không bỏ dở các waiter khác đang chạy).

    Ví dụ: `WaitingParallel([WaitingKafka(...), WaitingGrafana(...)])`.
    """

    def __init__(self, waiters: list[WaitingBase]):
        if not waiters:
            raise ValueError("WaitingParallel cần ít nhất 1 waiter")
        self.waiters = waiters

    def wait(self) -> None:
        errors: list[tuple[WaitingBase, BaseException]] = []

        def run(w: WaitingBase) -> None:
            try:
                w.wait()
            except BaseException as exc:  # noqa: BLE001 — gom lỗi để chờ đủ waiter khác
                errors.append((w, exc))

        threads = [threading.Thread(target=run, args=(w,), daemon=True) for w in self.waiters]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if errors:
            for w, exc in errors:
                logger.error("WaitingParallel: %s lỗi: %s", type(w).__name__, exc)
            raise errors[0][1]
