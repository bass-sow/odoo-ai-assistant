"""Conversation memory for /agent: the last turns of each session, in process memory.

Only the user questions and the final answers are kept, not the tool calls and their
results: follow-up questions ("and the mozzarella?") need the context, not the raw data,
and it keeps the prompt small. On Lambda each instance has its own memory, so a session
can be lost when a new instance starts; the client then simply starts a new one.
"""

import threading
import time
import uuid
from collections import OrderedDict


class SessionStore:
    def __init__(self, max_turns: int = 6, ttl_seconds: int = 3600, max_sessions: int = 1000):
        self.max_turns = max_turns
        self.ttl = ttl_seconds
        self.max_sessions = max_sessions
        self._data: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
        self._lock = threading.Lock()

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def history(self, session_id: str) -> list[dict]:
        with self._lock:
            entry = self._data.get(session_id)
            if not entry or time.monotonic() - entry[0] > self.ttl:
                self._data.pop(session_id, None)
                return []
            return list(entry[1])

    def append(self, session_id: str, question: str, answer: str) -> None:
        with self._lock:
            _, messages = self._data.pop(session_id, (0.0, []))
            messages = messages + [{"role": "user", "content": question},
                                   {"role": "assistant", "content": answer}]
            self._data[session_id] = (time.monotonic(), messages[-2 * self.max_turns:])
            while len(self._data) > self.max_sessions:
                self._data.popitem(last=False)  # least recently used

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)
