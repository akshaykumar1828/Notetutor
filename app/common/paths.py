"""
Where the pipeline writes per-lesson working files (voice clips, rendered
PDF pages, music, downloaded pictures).

The command line uses data/ in the project. The web app's worker sets
NOTETUTOR_WORK_DIR to a temporary folder that is deleted after each
lesson, so nothing accumulates on disk; results are stored in MongoDB.
"""

import os
from pathlib import Path


def work_dir(*parts: str) -> Path:
    return Path(os.environ.get("NOTETUTOR_WORK_DIR", "data"), *parts)
