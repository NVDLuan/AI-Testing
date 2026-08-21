import logging
import time

from .base import WaitingBase

logger = logging.getLogger("Waiting")


class WaitingTimeout(WaitingBase):

    def __init__(self, seconds: int = 0, minutes: int = 0, hours: int = 0):
        self.timeout = seconds + 60 * minutes + 3600 * hours

    def wait(self):
        logger.info(f"Waiting for {self.timeout} seconds")
        time.sleep(self.timeout)
