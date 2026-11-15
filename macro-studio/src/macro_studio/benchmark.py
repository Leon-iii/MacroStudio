from math import ceil
from statistics import fmean

from .ir import ValidationError, validate_program

SECOND_NS = 1_000_000_000
HOLD_NS = 2_000_000
BENCHMARK_RATES = (100, 200)


def make_program(hz: int, count: int, key: str) -> dict:
    if type(hz) is not int or hz not in BENCHMARK_RATES:
        raise ValidationError("benchmark frequency must be 100 or 200 Hz")
    return validate_program({
        "ir_version": 1,
        "project_id": "input-benchmark",
        "workflows": {
            "benchmark": {"steps": [{
                "op": "tap_sequence",
                "block_id": "benchmark-sequence",
                "frequency_hz": hz,
                "hold_ns": HOLD_NS,
                "count": count,
                "key": key,
            }]},
        },
        "handlers": [{
            "id": "benchmark-start",
            "event": {"type": "project_start"},
            "workflow_id": "benchmark",
        }],
    })


def summarize(
    hz: int,
    rows: list[dict],
    missed_ticks: int,
    stop_latency_ns: int | None,
    *,
    elapsed_ns: int | None = None,
) -> dict:
    """Report injection results. A null scheduled_ns identifies a cleanup edge."""
    if type(hz) is not int or hz not in BENCHMARK_RATES:
        raise ValueError("benchmark frequency must be 100 or 200 Hz")
    scheduled = [row for row in rows if row["scheduled_ns"] is not None]
    inserted = [row for row in scheduled if row["inserted"]]
    jitter = sorted(row["actual_ns"] - row["scheduled_ns"] for row in scheduled)
    cleanup_edges = len(rows) - len(scheduled)
    failed_cleanup_edges = sum(
        row["scheduled_ns"] is None and not row["inserted"] for row in rows
    )

    if elapsed_ns is None and scheduled:
        period_ns = SECOND_NS // hz
        first = scheduled[0]["scheduled_ns"]
        last = scheduled[-1]
        last_period = (last["scheduled_ns"] - first) // period_ns
        elapsed_ns = max((last_period + 1) * period_ns, last["actual_ns"] - first)

    inserted_pairs = 0
    previous_down_inserted = False
    for row in scheduled:
        if row["action"] == "key_down":
            previous_down_inserted = row["inserted"]
        elif row["action"] == "key_up":
            inserted_pairs += previous_down_inserted and row["inserted"]
            previous_down_inserted = False

    return {
        "frequency_hz": hz,
        "elapsed_ns": elapsed_ns,
        "command_edges": len(scheduled),
        "injected_edges": len(inserted),
        "failed_edges": len(scheduled) - len(inserted),
        "injected_pairs": inserted_pairs,
        "missed_ticks": missed_ticks,
        "cleanup_edges": cleanup_edges,
        "failed_cleanup_edges": failed_cleanup_edges,
        "command_rate_hz": len(scheduled) * SECOND_NS / elapsed_ns if elapsed_ns else None,
        "injection_rate_hz": len(inserted) * SECOND_NS / elapsed_ns if elapsed_ns else None,
        "jitter_ns": {
            "mean": fmean(jitter) if jitter else None,
            "p95": jitter[ceil(len(jitter) * 0.95) - 1] if jitter else None,
            "p99": jitter[ceil(len(jitter) * 0.99) - 1] if jitter else None,
            "max": max(jitter) if jitter else None,
        },
        "stop_latency_ns": stop_latency_ns,
    }
