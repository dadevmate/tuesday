"""The body Tuesday controls. Every robot implements this interface; the mock stands in until the Vector arrives."""
from abc import ABC, abstractmethod


class Body(ABC):
    name = "body"

    @abstractmethod
    def status(self) -> dict: ...
    @abstractmethod
    def say(self, text: str) -> str: ...
    @abstractmethod
    def drive(self, distance_mm: float, speed_mmps: float = 100) -> str: ...
    @abstractmethod
    def turn(self, degrees: float) -> str: ...
    @abstractmethod
    def head(self, degrees: float) -> str: ...
    @abstractmethod
    def lift(self, height: float) -> str: ...
    @abstractmethod
    def look(self) -> bytes | None:
        """Return a JPEG of what the camera sees."""
    @abstractmethod
    def faces(self) -> list[dict]:
        """Faces currently visible: [{name, face_id, expression}]."""
    @abstractmethod
    def enroll_face(self, name: str) -> str: ...
    @abstractmethod
    def known_faces(self) -> list[str]: ...
    @abstractmethod
    def animate(self, trigger: str) -> str: ...
    @abstractmethod
    def off_charger(self) -> str: ...
    @abstractmethod
    def to_charger(self) -> str: ...
    @abstractmethod
    def scan_room(self) -> dict:
        """Explore the surroundings and return Vector's navigation map as an image path + summary."""
    @abstractmethod
    def show_on_face(self, png: bytes, seconds: float = 5) -> str: ...

    def on_voice(self, handler) -> None:
        """Register handler(text) for voice commands, if the robot supports it."""
        return None

    def close(self) -> None:
        return None
