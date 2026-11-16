from collections.abc import Iterator

from .external_hid.backend import key_usage
from .ir import duration_nanoseconds
from .output import BackendCapabilities


def _steps(steps: list[dict]) -> Iterator[dict]:
    for step in steps:
        yield step
        for branch in ("steps", "then", "else"):
            yield from _steps(step.get(branch, []))


def _train_fits(step: dict) -> bool:
    frequency, count = step["frequency_hz"], step["count"]
    if isinstance(frequency, dict) or isinstance(count, dict) or 1_000_000 % frequency:
        return False
    if "hold_ns" in step:
        hold_ns = step["hold_ns"]
    else:
        duration = step["duration"]
        if isinstance(duration["value"], dict):
            return False
        hold_ns = duration_nanoseconds(duration["value"], duration["unit"])
    return (hold_ns % 1000 == 0 and
            0 < hold_ns < 1_000_000_000 // frequency and key_usage(step["key"]) <= 0x73)


def device_steps(program: dict, capabilities: BackendCapabilities) -> frozenset[int]:
    """Select steps by identity within the validated IR used by this runtime."""
    handlers = [handler for handler in program["handlers"] if handler["enabled"]]
    # The firmware excludes streamed input while a job owns the device.
    if program.get("state_machine") or len(handlers) != 1 or handlers[0]["policy"] == "parallel":
        return frozenset()
    return frozenset(
        id(step)
        for workflow in program["workflows"].values()
        for step in _steps(workflow["steps"])
        if (step["op"] == "tap_sequence" and capabilities.onboard_tap_train and _train_fits(step))
        or (step["op"] == "timed_batch" and capabilities.timed_batch
            and all("key" in edge and key_usage(edge["key"]) <= 0x73 for edge in step["events"]))
    )
