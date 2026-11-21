from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class ClipConfig:
    """All knobs for one clipping run."""

    num_clips: int = 5
    min_duration: float = 20.0
    max_duration: float = 60.0

    # Layout: "vertical" (9:16), "square" (1:1) or "original"
    aspect: str = "vertical"
    # How to fit a landscape source into a vertical frame: "blur" | "crop"
    reframe: str = "blur"
    # Horizontal crop center for reframe="crop", 0.0 (left) .. 1.0 (right)
    crop_x: float = 0.5
    output_height: int = 1920

    captions: bool = True
    caption_words_per_line: int = 3
    caption_font: str = "DejaVu Sans"
    caption_font_size: int = 0  # 0 = auto from output height
    caption_highlight: str = "#FFD400"
    hook_overlay: bool = True  # show the clip's hook text at the top for the first seconds
    loudnorm: bool = True  # normalize audio to -14 LUFS (platform standard)

    # Transcript source: "auto" (YouTube subs, then whisper) | "youtube" | "whisper" | "none"
    transcript: str = "auto"
    languages: list[str] = field(default_factory=lambda: ["id", "en"])
    whisper_model: str = "small"

    # Highlight selection: "auto" (Claude if available, else heuristic) | "ai" | "heuristic"
    selector: str = "auto"
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "high"
    ai_instructions: str = ""

    # Download options
    max_height: int = 1080
    cookies: str | None = None
    cookies_from_browser: str | None = None

    # Encoding
    crf: int = 20
    preset: str = "veryfast"

    work_dir: Path = Path("work")
    output_dir: Path = Path("output")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["work_dir"] = str(self.work_dir)
        d["output_dir"] = str(self.output_dir)
        return d

    def validate(self) -> None:
        if self.num_clips < 1:
            raise ValueError("num_clips must be >= 1")
        if self.min_duration <= 0 or self.max_duration < self.min_duration:
            raise ValueError("need 0 < min_duration <= max_duration")
        if self.aspect not in {"vertical", "square", "original"}:
            raise ValueError("aspect must be vertical, square or original")
        if self.reframe not in {"blur", "crop"}:
            raise ValueError("reframe must be blur or crop")
        if self.transcript not in {"auto", "youtube", "whisper", "none"}:
            raise ValueError("transcript must be auto, youtube, whisper or none")
        if self.selector not in {"auto", "ai", "heuristic"}:
            raise ValueError("selector must be auto, ai or heuristic")
        if not 0.0 <= self.crop_x <= 1.0:
            raise ValueError("crop_x must be between 0 and 1")
