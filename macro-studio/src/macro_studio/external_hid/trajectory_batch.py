from ..ir import MAX_BATCH_EVENTS, MAX_BATCH_US
from ..mouse_motion.models import MouseTrajectory
from ..mouse_motion.quantizer import RelativeQuantizer, first_report


def timed_pointer_batch(
    trajectory: MouseTrajectory, *, max_payload: int, minimum_interval_us: int,
) -> list[dict]:
    """Encode a single bounded relative-motion job for the existing batch wire format."""
    if minimum_interval_us < 1:
        raise ValueError("timed mouse trajectory requires a positive device interval")
    if trajectory.duration_ns > MAX_BATCH_US * 1000:
        raise ValueError("timed mouse trajectory exceeds device duration")
    capacity = min(MAX_BATCH_EVENTS, (max_payload - 3) // 7)
    quantizer = RelativeQuantizer(trajectory.start)
    edges = []
    for point in trajectory.points[1:]:
        delta = quantizer.remaining(point)
        if delta == (0, 0):
            continue
        offset, nanos = divmod(point.time_offset_ns, 1000)
        if nanos or (edges and offset - edges[-1]["offset_us"] < minimum_interval_us):
            raise ValueError("timed mouse trajectory cannot preserve sample times on this device")
        if first_report(delta) != delta or len(edges) >= capacity:
            raise ValueError("timed mouse trajectory exceeds device report or buffer capacity")
        edges.append({"action": "mouse_move", "offset_us": offset, "dx": delta[0], "dy": delta[1]})
        quantizer.record(delta)
    return edges
