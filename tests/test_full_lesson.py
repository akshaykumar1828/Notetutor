"""
Full lesson video from real notes (needs Ollama running; uses Groq if
GROQ_API_KEY is set). Run directly:

    python -m tests.test_full_lesson [source]

source defaults to the sample handwritten page; it may also be a PDF,
a .txt/.md file, or a topic string.
"""

import sys

from app.pipeline.lesson_video import generate_lesson_video

DEFAULT_SOURCE = "samples/agentic_ai_notes.jpeg"


def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE
    generate_lesson_video(source, "data/video/full_lesson.mp4")


if __name__ == "__main__":
    main()
