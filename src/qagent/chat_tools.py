"""Conversation operations over the hardware experiment service."""


class ChatOperations:
    def __init__(self, service, history):
        self.quantumx, self.history = service, history
        self.store = service.store
        self._started_this_turn = False
        self._closing = False
        service.bind_session(history.session_id)

    def begin_turn(self):
        self._started_this_turn = False

    def task_info(self):
        return self.quantumx.describe()

    @property
    def closing(self):
        return self._closing

    def wait(self, timeout=None):
        self.quantumx.wait(timeout)

    def pause(self):
        self._closing = True
        self.quantumx.pause()

    def close(self):
        self.pause()
        self.wait(timeout=2)


def create_tools(operations, call_id):
    from .integrations.quantumx.tools import create_tools as quantumx_tools

    return quantumx_tools(operations, call_id)
