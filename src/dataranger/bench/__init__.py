"""DQ-Repair: a benchmark for safe, verified data-quality repair."""
from .tables import generate
from .inject import inject
from .detect import detect, score

__all__ = ["generate", "inject", "detect", "score"]
