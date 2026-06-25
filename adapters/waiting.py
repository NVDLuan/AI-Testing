import logging
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
            confirm_time: int = 60,
            poll_interval: int = 60,
            timeout: int = 10,
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
