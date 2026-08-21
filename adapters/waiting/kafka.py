import logging
import time

from adapters.api_agent.base import BaseHttpClient

from .base import WaitingBase

logger = logging.getLogger("Waiting")


class WaitingKafka(WaitingBase, BaseHttpClient):

    def __init__(
            self,
            kafka_watch_api_url: str,
            confirm_time: int = 300,
            poll_interval: int = 60,
            timeout: int = 50,
            endpoint: str = "status",
            api_key: str = "",
    ):
        super().__init__(
            base_url=kafka_watch_api_url,
            api_key=api_key,
            poll_interval_sec=poll_interval,
            timeout_sec=timeout,
        )
        self.confirm_time = confirm_time
        self.endpoint = endpoint

    def get_status_message(self) -> dict:
        response = self._session.get(self._url(self.endpoint), timeout=self._timeout_sec)
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

            time.sleep(self._poll_interval_sec)
