"""Pipeline observability: per-stage metrics collection + Prometheus export."""

import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class StageMetrics:
    name: str
    total: int = 0
    success: int = 0
    failure: int = 0
    total_duration_ms: float = 0.0
    durations: list[float] = field(default_factory=list)
    last_error: str = ""
    last_error_time: float = 0.0


class MetricsCollector:
    """Collects pipeline metrics for all stages."""

    def __init__(self):
        self._stages: dict[str, StageMetrics] = {}
        self._total_requests = 0
        self._total_failures = 0
        self._start_time = time.monotonic()

    def get_stage(self, name: str) -> StageMetrics:
        if name not in self._stages:
            self._stages[name] = StageMetrics(name=name)
        return self._stages[name]

    def record(
        self, stage: str, success: bool, duration_ms: float, error: str = ""
    ):
        s = self.get_stage(stage)
        s.total += 1
        s.total_duration_ms += duration_ms
        s.durations.append(duration_ms)
        if success:
            s.success += 1
        else:
            s.failure += 1
            s.last_error = error
            s.last_error_time = time.monotonic()
        self._total_requests += 1
        if not success:
            self._total_failures += 1

    @property
    def success_rate(self) -> float:
        if self._total_requests == 0:
            return 1.0
        return 1.0 - (self._total_failures / self._total_requests)

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._start_time

    def p50(self, stage: str) -> float:
        return self._percentile(stage, 0.5)

    def p95(self, stage: str) -> float:
        return self._percentile(stage, 0.95)

    def p99(self, stage: str) -> float:
        return self._percentile(stage, 0.99)

    def _percentile(self, stage: str, pct: float) -> float:
        durations = sorted(
            self._stages.get(stage, StageMetrics(name="")).durations
        )
        if not durations:
            return 0.0
        idx = int(len(durations) * pct)
        return durations[min(idx, len(durations) - 1)]

    def summary(self) -> dict:
        return {
            "total_requests": self._total_requests,
            "total_failures": self._total_failures,
            "success_rate": f"{self.success_rate:.1%}",
            "uptime_seconds": self.uptime_seconds,
            "stages": {
                name: {
                    "total": s.total,
                    "success": s.success,
                    "failure": s.failure,
                    "avg_ms": s.total_duration_ms / max(s.total, 1),
                    "p50_ms": self.p50(name),
                    "p95_ms": self.p95(name),
                    "p99_ms": self.p99(name),
                }
                for name, s in self._stages.items()
            },
        }

    def prometheus_format(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = [
            "# HELP apex_requests_total Total requests processed",
            "# TYPE apex_requests_total counter",
            f"apex_requests_total {self._total_requests}",
            "# HELP apex_failures_total Total failures",
            "# TYPE apex_failures_total counter",
            f"apex_failures_total {self._total_failures}",
            "# HELP apex_success_rate Current success rate",
            "# TYPE apex_success_rate gauge",
            f"apex_success_rate {self.success_rate}",
        ]
        for name, s in self._stages.items():
            safe_name = name.replace(" ", "_")
            lines += [
                f"# HELP apex_stage_duration_ms Stage {name} duration",
                "# TYPE apex_stage_duration_ms gauge",
                f'apex_stage_duration_ms{{stage="{safe_name}",quantile="0.5"}} {self.p50(name)}',
                f'apex_stage_duration_ms{{stage="{safe_name}",quantile="0.95"}} {self.p95(name)}',
                f'apex_stage_duration_ms{{stage="{safe_name}",quantile="0.99"}} {self.p99(name)}',
            ]
        return "\n".join(lines)


class AlertRules:
    """Built-in alerting thresholds."""

    RULES = {
        "high_failure_rate": {"threshold": 0.3, "message": "Failure rate > 30%"},
        "high_p99_latency": {
            "threshold": 30000,
            "message": "P99 latency > 30s",
        },
        "high_degrade_rate": {
            "threshold": 0.2,
            "message": "Degrade rate > 20%",
        },
        "low_proxy_pool": {
            "threshold": 3,
            "message": "Proxy pool < 3 available",
        },
    }

    @classmethod
    def check(
        cls, metrics: MetricsCollector, proxy_count: int = 10
    ) -> list[str]:
        alerts = []
        if metrics.success_rate < 0.7:
            alerts.append(cls.RULES["high_failure_rate"]["message"])
        p99 = max([metrics.p99(s) for s in metrics._stages] or [0])
        if p99 > 30000:
            alerts.append(cls.RULES["high_p99_latency"]["message"])
        if proxy_count < 3:
            alerts.append(cls.RULES["low_proxy_pool"]["message"])
        return alerts
