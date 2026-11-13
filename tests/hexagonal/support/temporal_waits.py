"""Independent elapsed and wall controls for bounded observation scenarios."""


class ControlledClock:
    def __init__(self):
        self.elapsed = 0.0
        self.wall = 1_000.0
        self.sleeps = []

    def monotonic(self):
        return self.elapsed

    def time(self):
        return self.wall

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds

    def advance(self, *, elapsed=0, wall=0):
        self.elapsed += elapsed
        self.wall += wall
