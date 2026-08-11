import logging
import re
import threading
import time
from abc import ABC

import requests

logger = logging.getLogger("Waiting")


class WaitingBase(ABC):

    def wait(self):
        raise NotImplementedError()


class WaitingKafka(WaitingBase):

    def __init__(
            self,
            kafka_watch_api_url: str,
            confirm_time: int = 300,
            poll_interval: int = 60,
            timeout: int = 50,
    ):
        self.kafka_watch_api_url = kafka_watch_api_url
        self.confirm_time = confirm_time
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.session = requests.Session()

    def get_status_message(self) -> dict:
        url_status_message = f"{self.kafka_watch_api_url}/status"
        response = self.session.get(
            url=url_status_message, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def wait(self) -> None:
        logger.info(f"Waiting for result return")
        while True:
            res = self.get_status_message()
            total_lag = res.get("total_lag")

            if total_lag == 0:
                logger.info("Kafka lag is 0. Confirming in %ss...", self.confirm_time)
                time.sleep(self.confirm_time)

                res_check = self.get_status_message()
                if res_check.get("total_lag") == 0:
                    break
                logger.info("Kafka lag increased after confirm; continuing wait...")
                continue

            time.sleep(self.poll_interval)


class WaitingTimeout(WaitingBase):

    def __init__(self, seconds: int = 0, minutes: int = 0, hours: int = 0):
        self.timeout = seconds + 60 * minutes + 3600 * hours

    def wait(self):
        logger.info(f"Waiting for {self.timeout} seconds")
        time.sleep(self.timeout)


class WaitingGrafana(WaitingBase):
    """Chờ AI rảnh qua log Loki (Grafana) — poll tới khi thấy log xác nhận idle
    (hoặc cửa sổ log rỗng) trong log container gần nhất.
    """

    def __init__(
            self,
            base_url: str,
            username: str,
            password: str,
            server_name: str,
            service_name: str,
            target_logs: list[str],
            poll_interval: int = 120,
            timeout: int = 30,
            confirm_time: int = 300
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.server_name = server_name
        self.service_name = service_name
        self.target_logs = target_logs
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.session = requests.Session()
        self.uid = ""
        self.confirm_time = confirm_time

    def _login(self) -> None:
        res = self.session.post(
            f"{self.base_url}/login",
            json={"user": self.username, "password": self.password},
            timeout=self.timeout,
        )
        res.raise_for_status()

    def _get_uid(self) -> str:
        res = self.session.get(self.base_url, timeout=self.timeout)
        match = re.search(r'/api/datasources/proxy/uid/([^"/]+)', res.text)
        # matches = re.findall(r'/api/datasources/proxy/uid/[^"\s]+', res.text)
        if not match:
            raise RuntimeError("Grafana: không tìm thấy datasource uid trên trang chủ")
        return match.group(1)

    def _query_logs(self) -> list[str]:
        to_ms = int(time.time() * 1000)
        from_ms = to_ms - self.poll_interval * 1000
        query = {
            "queries": [
                {
                    "datasource": {
                        "type": "loki",
                        "uid": "ee9uaeacloq9se", # hardcode loki uid => find in line 108
                    },
                    "editorMode": "code",
                    "expr": f'{{server="{self.server_name}",container=~"{self.service_name}"}}',
                    "queryType": "range",
                    "refId": "A",
                    "maxLines": 1000,
                    "step": "",
                    "legendFormat": "",
                    "datasourceId": 2,
                    "intervalMs": 100,
                    "maxDataPoints": 1060,
                }
            ],
            "from": str(from_ms),
            "to": str(to_ms),
        }
        url = f"{self.base_url}/api/ds/query?ds_type=loki&requestId=test"
        res = self.session.post(url, json=query, timeout=self.timeout)
        if res.status_code == 401:
            logger.warning("Grafana query trả 401; đăng nhập lại rồi thử lại")
            self._login()
            self.uid = self._get_uid()
            res = self.session.post(url, json=query, timeout=self.timeout)
        res.raise_for_status()
        data = res.json()
        try:
            return (
                data.get("results", {})
                .get("A", {})
                .get("frames")[0]
                .get("data", {})
                .get("values")[2]
            )
        except (KeyError, IndexError, TypeError):
            return []

    def wait(self) -> None:
        logger.info(
            "Waiting for AI idle via Grafana/Loki [server=%s, service=%s]",
            self.server_name, self.service_name,
        )
        self._login()
        self.uid = self._get_uid()
        while True:
            time.sleep(self.poll_interval)
            logs = self._query_logs()
            if any(any(t in log for t in self.target_logs) for log in logs):
                logger.info("Grafana/Loki: AI idle confirmed")
                logger.info("Waiting for other service processing finish...")
                time.sleep(self.confirm_time)
                log_confirm = self._query_logs()
                if any("[DONE] ds-app IDLE — nothing left to process" in item for item in log_confirm): # TODO: cần check lại phần này để validate cho từng dự án
                    return
            logger.info("AI processing.... Retry after %ss seconds", self.poll_interval)


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


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    a = WaitingGrafana("https://grafana.rainscales.com", "nvdluan2", "nvdluan2", "server-thor-ops-edge-0024",
                       "karter-ds-app-1", ["[DONE] STAGE 1/3 (ds-app, detection) FINISHED ALL", "[DONE] ds-app IDLE — nothing left to process"])
    a.wait()
