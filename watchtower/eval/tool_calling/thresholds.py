"""Pre-registered pass marks for the W4 gate (W4_TARGETS T3).

GO requires every HARD metric to meet its mark on the pooled trials. REPORT metrics are
recorded but don't decide the gate. Adjust these BEFORE the first live run, in their own
commit. The commit timestamp is the evidence that they were set first.

Run settings that are part of the pre-registration: 10 seeds (0-9) per case, temperature 0.2,
the full Tier 0 schema set on every call, num_ctx from the T1b GPU-fit step.
"""

from dataclasses import dataclass
from typing import Literal

SEEDS = tuple(range(10))
TEMPERATURE = 0.2
NUM_CTX = 8192  # revisit after T1b; record the value actually used in the run row


@dataclass(frozen=True)
class Threshold:
    metric: str
    op: Literal[">=", "<="]
    value: float
    hard: bool = True

    def met(self, observed: float | None) -> bool | None:
        if observed is None:
            return None
        return observed >= self.value if self.op == ">=" else observed <= self.value


THRESHOLDS: tuple[Threshold, ...] = (
    Threshold("structural_rate", ">=", 0.95),
    Threshold("selection_accuracy", ">=", 0.90),
    Threshold("arg_validity", ">=", 0.95),
    Threshold("arg_correctness", ">=", 0.85),
    Threshold("chaining_success", ">=", 0.80),
    Threshold("hallucination_rate", "<=", 0.05),
    Threshold("restraint_success", ">=", 0.0, hard=False),
    Threshold("recovery_success", ">=", 0.0, hard=False),
    Threshold("completion_rate", ">=", 0.98),  # a run with failed requests is not a gate run
)

LATENCY_FLAG_P50_MS = 10_000  # report flag, not a gate mark: P3 throughput depends on it
