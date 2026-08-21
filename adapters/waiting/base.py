from abc import ABC


class WaitingBase(ABC):

    def wait(self):
        raise NotImplementedError()
