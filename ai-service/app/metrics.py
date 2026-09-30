"""Small Prometheus text endpoint; aggregate timings only, never content or IDs."""

import threading
from collections import defaultdict


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._count = defaultdict(int)
        self._sum = defaultdict(float)
        self._counters = defaultdict(int)

    def increment(self, name: str, label: str = "") -> None:
        with self._lock:
            self._counters[(name, label)] += 1

    def observe(self, name: str, milliseconds: float, label: str = "") -> None:
        key = (name, label)
        with self._lock:
            self._count[key] += 1
            self._sum[key] += milliseconds / 1000

    def render(self) -> str:
        lines = []
        with self._lock:
            for (name, label), count in sorted(self._counters.items()):
                suffix = f'{{tool="{label}"}}' if label else ""
                lines.append(f"{name}{suffix} {count}")
            for (name, label), count in sorted(self._count.items()):
                suffix = f'{{tool="{label}"}}' if label else ""
                lines.append(f"{name}_count{suffix} {count}")
                lines.append(f"{name}_sum{suffix} {self._sum[(name,label)]:.6f}")
        return "\n".join(lines) + "\n"


METRICS = Metrics()
