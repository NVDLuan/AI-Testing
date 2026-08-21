import logging
import re
import time

import requests

from .base import WaitingBase

logger = logging.getLogger("Waiting")


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
                        "uid": "ee9uaeacloq9se",  # hardcode loki uid => find in line 108
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
                if any("[DONE] ds-app IDLE — nothing left to process" in item for item in
                       log_confirm):  # TODO: cần check lại phần này để validate cho từng dự án
                    return
            logger.info("AI processing.... Retry after %ss seconds", self.poll_interval)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    a = WaitingGrafana("https://grafana.rainscales.com", "nvdluan2", "nvdluan2", "server-thor-ops-edge-0024",
                       "karter-ds-app-1", ["[DONE] STAGE 1/3 (ds-app, detection) FINISHED ALL",
                                           "[DONE] ds-app IDLE — nothing left to process"])
    a.wait()
