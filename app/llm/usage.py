"""Token and latency tracking for every LLM call.

Free tiers are limited by requests per minute and tokens per day, so knowing what each
endpoint consumes matters as much as it would with a paid API.
"""

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Usage:
    model: str
    operation: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    ts: float = field(default_factory=time.time)


class UsageTracker:
    def __init__(self, log_path: str | None = None):
        self._records: list[Usage] = []
        self._lock = threading.Lock()
        self._log_path = Path(log_path) if log_path else None

    def record(self, usage: Usage) -> None:
        with self._lock:
            self._records.append(usage)
            if self._log_path:
                try:
                    self._log_path.parent.mkdir(parents=True, exist_ok=True)
                    with self._log_path.open("a") as f:
                        f.write(json.dumps(asdict(usage)) + "\n")
                except OSError:
                    pass  # read-only filesystem (e.g. Lambda outside /tmp): keep in memory

    def summary(self) -> dict:
        with self._lock:
            records = list(self._records)
        by_op: dict[str, dict] = {}
        for r in records:
            s = by_op.setdefault(
                r.operation, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "ms": 0}
            )
            s["calls"] += 1
            s["prompt_tokens"] += r.prompt_tokens
            s["completion_tokens"] += r.completion_tokens
            s["ms"] += r.latency_ms
        for s in by_op.values():
            s["avg_latency_ms"] = s.pop("ms") // max(s["calls"], 1)
        return {
            "calls": len(records),
            "prompt_tokens": sum(r.prompt_tokens for r in records),
            "completion_tokens": sum(r.completion_tokens for r in records),
            "by_operation": by_op,
        }
