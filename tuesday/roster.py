"""The seven minds of Tuesday: open-weight models (Apache 2.0 / MIT) that run on hardware the owner controls.
No company sees a single query. Tuesday is the main mind: it talks to the owner, judges the others' drafts and decides.

Five minds share one base (Qwen3-8B) and differ by a small LoRA adapter, all served at once by Tuesday Core
(tuesday/core.py): no loading or swapping between them. Two minds from another family (Mistral's Ministral) stay
separate on Ollama, as the outside voices that don't share the base's blind spots. Each mind is fine-tuned on the
owner's data plus its specialty (train/build_core.py); before its first fine-tune, its base model stands in."""
from dataclasses import dataclass, field

from . import config


@dataclass
class Mind:
    name: str            # day name: the Ollama model name once fine-tuned
    base_ollama: str     # stand-in until fine-tuned
    base_hf: str         # MLX 4-bit weights fine-tuned on this Mac
    role: str            # one line, shown to the others in the council
    focus: str           # system-prompt addition: how this mind thinks
    tools: list[str] = field(default_factory=list)   # tools this mind may use when drafting
    license: str = "Apache-2.0"
    shared: bool = False  # on Tuesday Core's shared Qwen3-8B as a LoRA adapter (else its own model on Ollama)

    @property
    def model(self) -> str:
        """TUESDAY_MODEL_<DAY> if set (e.g. a candidate under test); else a shared mind on Tuesday Core; else the
        fine-tuned mind on Ollama, else its base stand-in."""
        from . import core
        from .minds import local
        override = config.env(f"TUESDAY_MODEL_{self.name.upper()}")
        if override:
            return override
        installed = local.installed_names()
        # A shared mind runs on the core once it has an adapter (or has nothing better on Ollama than the base).
        if self.shared and core.enabled() and (self.name in core.adapters() or self.name not in installed):
            return f"core:{self.name}"
        return self.name if self.name in installed else self.base_ollama


READ = ["weather", "current_time", "calculate", "list_reminders", "task_list", "list_routines", "robot_recent_events", "memory_recall"]
WEB = ["search_web", "fetch_page", "browse_open", "browse_read", "browse_click", "browse_type"]

MINDS = {m.name: m for m in [
    Mind("monday", "qwen3:8b", "mlx-community/Qwen3-8B-4bit",
         "Researcher: searches and reads the web, checks facts, cites sources",
         "You are Monday, the researcher. Search and read before you answer; prefer primary sources; give facts with their URLs; say what you could not verify.",
         READ + WEB, shared=True),
    Mind("tuesday", "qwen3:8b", "mlx-community/Qwen3-8B-4bit",
         "The main mind: knows the owner best, speaks for the system, judges and decides",
         "You are Tuesday, the main mind. You know your owner better than anyone and speak to him directly.",
         READ, shared=True),
    Mind("wednesday", "qwen3:8b", "mlx-community/Qwen3-8B-4bit",
         "Strategist: business, the owner's company and projects, career, plans and decisions",
         "You are Wednesday, the strategist. Think in goals, trade-offs and next actions; be concrete about numbers, timelines and the single best move.",
         READ, shared=True),
    Mind("thursday", "qwen3:8b", "mlx-community/Qwen3-8B-4bit",
         "Engineer: code, AI engineering, tech and how things work",
         "You are Thursday, the engineer. Be technically exact; explain with the important keywords, what it does and how to extend it; prefer working code.",
         READ + ["search_web", "fetch_page"], shared=True),
    Mind("friday", "ministral-3:8b", "mlx-community/Ministral-3-8B-Instruct-2512-4bit",
         "Companion: feelings, wellbeing, friendships, confidence and fun",
         "You are Friday, the companion. Listen first, be warm and honest, never syrupy; notice how he feels and what he needs (comfort, a laugh, or a push).",
         READ),
    Mind("saturday", "qwen3:8b", "mlx-community/Qwen3-8B-4bit",
         "Analyst: money, markets, maths and anything with numbers",
         "You are Saturday, the analyst. Use the exact computed numbers you are given, state assumptions, size risks plainly, and respect his investing rules.",
         READ, shared=True),
    Mind("sunday", "ministral-3:3b", "mlx-community/Ministral-3-3B-Instruct-2512-4bit",
         "Operator and guardian: robot, reminders, tasks, actions online, privacy and safety",
         "You are Sunday, the operator and guardian. Turn requests into concrete actions with tools; check every plan for privacy leaks, safety and anything needing the owner's confirmation.",
         ["*"]),  # every tool
]}

MAIN = MINDS["tuesday"]
SPECIALISTS = [m for n, m in MINDS.items() if n != "tuesday"]


def enabled() -> list[Mind]:
    """TUESDAY_MINDS=monday,tuesday,... limits which minds run (e.g. on a small server); default all seven."""
    names = [n.strip() for n in config.env("TUESDAY_MINDS", ",".join(MINDS)).split(",") if n.strip() in MINDS]
    return [MINDS[n] for n in names] or [MAIN]
