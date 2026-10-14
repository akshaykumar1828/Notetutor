"""
Scene and lesson video rendering.

A scene clip is a deterministic animation (app.video.animator) driven by
the measured narration timing, with the narration WAV as its audio.
Video length always equals audio length, and the audio is never sped
up or truncated.
"""

import os
from pathlib import Path

from moviepy import AudioFileClip, CompositeAudioClip, VideoClip, concatenate_videoclips

from app.audio.tts import NarrationAudio
from app.video import theme
from app.video.animator import SceneAnimator
from app.video.choreography import build_timing
from app.video.layout import LayoutError, layout_visual
from app.video.schemas import VisualPlan
from app.common.paths import work_dir

OUTPUT_DIR = Path("data/video")

# Area the visual may occupy: below the title, above the caption band.
VISUAL_REGION = (60, 112, theme.VIDEO_WIDTH - 60, 600)

ENCODE_OPTIONS = dict(
    fps=theme.FPS,
    codec="libx264",
    audio_codec="libmp3lame",  # AAC is silent in VS Code's preview
    audio_bitrate="128k",
    preset="veryfast",
    threads=os.cpu_count() or 4,
    # Quality-based encoding tuned for flat graphics: same look, files ~5x
    # smaller than a fixed bitrate (a 5-minute lesson ~8 MB instead of ~40).
    ffmpeg_params=["-crf", "24", "-tune", "animation", "-pix_fmt", "yuv420p"],
)


class VideoRenderError(RuntimeError):
    """Raised when scene video rendering fails."""


def build_scene_clip(
    title: str,
    plan: VisualPlan,
    sentences: list[str],
    narration: NarrationAudio,
    progress_label: str = "",
    show_captions: bool = True,
    fallback_plan: VisualPlan | None = None,
) -> VideoClip:
    try:
        layout = layout_visual(plan, VISUAL_REGION)
    except LayoutError as exc:
        if fallback_plan is None:
            raise VideoRenderError(f"Could not lay out visual: {exc}") from exc
        print(f"[renderer] {exc}; using fallback key points.")
        plan = fallback_plan
        layout = layout_visual(plan, VISUAL_REGION)

    for warning in layout.warnings:
        print(f"[renderer] layout warning: {warning}")

    audio = AudioFileClip(narration.path)
    duration = audio.duration
    timing = build_timing(plan, sentences, narration.spans, duration)
    animator = SceneAnimator(title, layout, timing, progress_label, show_captions)

    clip = VideoClip(frame_function=animator.frame, duration=duration)
    return clip.with_audio(audio)


def write_clip(clip: VideoClip, output_path: str | Path) -> str:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        clip.write_videofile(str(output_path), logger="bar", **ENCODE_OPTIONS)
    except Exception as exc:
        raise VideoRenderError(f"Failed to write {output_path}: {exc}") from exc
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise VideoRenderError(f"Renderer produced no output at {output_path}")
    return str(output_path)


# Background music level relative to full scale; narration stays clear.
MUSIC_VOLUME = 0.09  # level in pauses; ducked to about a third under speech


def render_lesson(
    clips: list[VideoClip],
    output_path: str | Path,
    music: bool = True,
    title: str = "",
    speech: list[tuple[float, float]] | None = None,
) -> str:
    """Joins scene clips back to back and adds soft background music."""
    if not clips:
        raise VideoRenderError("No scene clips to render.")
    lesson = concatenate_videoclips(clips, method="chain")
    if music:
        from app.audio.music import generate_music

        music_path = generate_music(
            lesson.duration, work_dir("audio", "music_bed.wav"), seed_text=title, speech=speech
        )
        bed = AudioFileClip(music_path).with_volume_scaled(MUSIC_VOLUME)
        lesson = lesson.with_audio(CompositeAudioClip([lesson.audio, bed]))
    try:
        return write_clip(lesson, output_path)
    finally:
        lesson.close()
        for clip in clips:
            clip.close()
