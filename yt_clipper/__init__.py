"""yt-clipper: turn a YouTube link into short, captioned clips."""

from .config import ClipConfig
from .pipeline import run_pipeline

__all__ = ["ClipConfig", "run_pipeline"]
__version__ = "0.1.0"
