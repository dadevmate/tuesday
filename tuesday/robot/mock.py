"""A stand-in body so Tuesday works before the Vector arrives. It logs what the robot would do."""
import time

from .base import Body


class MockBody(Body):
    name = "mock"

    def __init__(self):
        self.log: list[str] = []
        self.pose = {"x": 0.0, "y": 0.0, "angle": 0.0}
        self.head_deg = 0.0
        self.on_charger = True
        self.enrolled: list[str] = []

    def _do(self, what: str) -> str:
        line = f"[mock robot {time.strftime('%H:%M:%S')}] {what}"
        self.log.append(line)
        self.log = self.log[-200:]
        print(line, flush=True)
        return what

    def status(self):
        return {"robot": "mock (Vector not connected)", "battery": "n/a", "on_charger": self.on_charger, "pose": self.pose, "head_deg": self.head_deg, "recent": self.log[-5:]}

    def say(self, text):
        # Before the robot arrives: TUESDAY_MOCK_SPEAK=1 plays Tuesday's voice on the Mac's speakers.
        from .. import config, voice
        if voice.enabled() and config.env("TUESDAY_MOCK_SPEAK") == "1":
            import subprocess
            wav = voice.to_file(text)
            subprocess.run(["afplay", str(wav)], check=False)
            wav.unlink(missing_ok=True)
        return self._do(f'says "{text}"')

    def drive(self, distance_mm, speed_mmps=100):
        import math
        self.on_charger = False
        self.pose["x"] += distance_mm * math.cos(math.radians(self.pose["angle"]))
        self.pose["y"] += distance_mm * math.sin(math.radians(self.pose["angle"]))
        return self._do(f"drives {distance_mm:.0f} mm at {speed_mmps:.0f} mm/s")

    def turn(self, degrees):
        self.pose["angle"] = (self.pose["angle"] + degrees) % 360
        return self._do(f"turns {degrees:.0f}°")

    def head(self, degrees):
        self.head_deg = max(-22, min(45, degrees))
        return self._do(f"tilts head to {self.head_deg:.0f}°")

    def lift(self, height):
        return self._do(f"moves lift to {height:.2f}")

    def look(self):
        self._do("takes a photo (mock: no camera)")
        return None

    def faces(self):
        return []

    def enroll_face(self, name):
        self.enrolled.append(name)
        return self._do(f"would start face enrollment for {name}")

    def known_faces(self):
        return list(self.enrolled)

    def animate(self, trigger):
        return self._do(f"plays animation {trigger}")

    def off_charger(self):
        self.on_charger = False
        return self._do("drives off charger")

    def to_charger(self):
        self.on_charger = True
        return self._do("returns to charger")

    def scan_room(self):
        self._do("would explore and map the room")
        return {"ok": False, "summary": "Mock robot: no map. Connect the Vector to map rooms."}

    def show_on_face(self, png, seconds=5):
        return self._do(f"shows an image on its face for {seconds}s")
