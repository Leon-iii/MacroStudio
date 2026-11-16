from dataclasses import dataclass

from .models import TimedPoint

HID_REPORT_LIMIT = 127


@dataclass(slots=True)
class RelativeQuantizer:
    origin: tuple[float, float]
    sent_x: int = 0
    sent_y: int = 0

    def remaining(self, point: TimedPoint) -> tuple[int, int]:
        # Subtract the total already sent from the rounded logical displacement.
        # The subpixel remainder survives skipped samples and split reports.
        return (round(point.x - self.origin[0]) - self.sent_x,
                round(point.y - self.origin[1]) - self.sent_y)

    def record(self, report: tuple[int, int]) -> None:
        self.sent_x += report[0]
        self.sent_y += report[1]


def first_report(delta: tuple[int, int]) -> tuple[int, int]:
    reports = max(1, (max(map(abs, delta)) + HID_REPORT_LIMIT - 1) // HID_REPORT_LIMIT)
    return round(delta[0] / reports), round(delta[1] / reports)
