import math
import random
from datetime import datetime

class DiurnalPattern:
    def __init__(self, pattern_type: str = "sine"):
        self.pattern_type = pattern_type

    def multiplier(self, hour_of_day: float = None) -> float:
        if hour_of_day is None:
            now = datetime.now()
            hour_of_day = now.hour + now.minute / 60.0

        if self.pattern_type == "flat":
            return 1.0

        if self.pattern_type == "sine":
            peak_hour = 10.0
            lunch_dip_hour = 13.0
            evening_drop_hour = 18.0

            business_peak = math.sin(math.pi * (hour_of_day - 6) / 12)
            business_peak = max(0, business_peak)

            lunch_dip = 1.0 - 0.3 * math.exp(
                -((hour_of_day - lunch_dip_hour) ** 2) / 0.5
            )

            multiplier = 0.2 + 0.8 * business_peak * lunch_dip
            return max(0.15, min(1.0, multiplier))

        if self.pattern_type == "step":
            if 8 <= hour_of_day <= 18:
                return 1.0
            elif 22 <= hour_of_day or hour_of_day <= 5:
                return 0.3
            else:
                return 0.6

        if self.pattern_type == "backup_window":
            if 1 <= hour_of_day <= 3:
                return 2.0
            else:
                return 1.0

        return 1.0


class RampPattern:
    def __init__(self, ramp_type: str = "none",
                 start_rate: float = 0.1,
                 end_rate: float = 1.0,
                 ramp_duration_sec: int = 120):
        self.ramp_type = ramp_type
        self.start_rate = start_rate
        self.end_rate = end_rate
        self.ramp_duration_sec = ramp_duration_sec

    def multiplier(self, elapsed_sec: float) -> float:
        if self.ramp_type == "none":
            return 1.0

        if self.ramp_type == "linear":
            if elapsed_sec >= self.ramp_duration_sec:
                return self.end_rate
            progress = elapsed_sec / self.ramp_duration_sec
            return self.start_rate + (self.end_rate - self.start_rate) * progress

        if self.ramp_type == "step":
            step_time = self.ramp_duration_sec / 5
            step = int(elapsed_sec / step_time)
            values = [0.1, 0.25, 0.5, 0.75, 1.0]
            if step >= len(values):
                return values[-1]
            return values[step]

        if self.ramp_type == "sine_ramp":
            if elapsed_sec >= self.ramp_duration_sec:
                return self.end_rate
            progress = elapsed_sec / self.ramp_duration_sec
            smooth = (1 - math.cos(progress * math.pi)) / 2
            return self.start_rate + (self.end_rate - self.start_rate) * smooth

        return 1.0
