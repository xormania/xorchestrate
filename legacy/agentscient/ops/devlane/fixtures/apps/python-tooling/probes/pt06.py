from tool.model import Interval

interval = Interval(1, 3)
raise SystemExit(0 if not interval.contains(3) else 1)
