"""
Lesson-level evaluation from the record the pipeline writes next to each
video (<video>.json).

    python -m evaluation.eval_lesson data/video/<lesson>.json [--judge]

Measures
  coverage        share of the source's sections the narration teaches
  visual grounding  share of on-screen labels whose words appear in the
                  source or narration (a cheap hallucination check)
  visual reliability  first-try / after-retry / fallback visual plans
  sync            how soon each element appears after its sentence starts,
                  and video vs audio length of the MP4
  pacing          speaking rate, scene length, total length, generation time
  faithfulness    (--judge) an LLM extracts each factual claim from the
                  narration and labels it supported / unsupported /
                  contradicted by the source
"""

import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

from app.content.narration import content_words
from app.video.choreography import build_timing
from app.video.schemas import VisualPlan

JUDGE_PROMPT = """
You are checking an educational video narration against its source
material. Extract every factual claim the narration makes (ignore
greetings, rhetorical questions and transitions). For each claim decide:

- "supported": stated in, or directly implied by, the source
- "unsupported": not in the source (may be true general knowledge)
- "contradicted": conflicts with the source

Return JSON: {"claims": [{"claim": "...", "label": "supported|unsupported|contradicted"}]}

SOURCE:
"""


def _media_durations(video: Path) -> tuple[float, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
         "-of", "json", str(video)], capture_output=True, text=True,
    )
    streams = json.loads(out.stdout or "{}").get("streams", [])
    get = lambda kind: next((float(s["duration"]) for s in streams if s["codec_type"] == kind), 0.0)
    return get("video"), get("audio")


def _visual_labels(visual: dict) -> list[str]:
    labels = [n["label"] for n in visual.get("nodes", [])]
    labels += [s["label"] for s in visual.get("steps", [])]
    labels += visual.get("columns", [])
    for r in visual.get("rows", []):
        labels += [r["criterion"]] + r["cells"]
    labels += [p["text"] for p in visual.get("points", [])]
    return [l for l in labels if l and l.strip() not in ("-",)]


def main(record_path: str, judge: bool = False) -> str:
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    scenes = record["scenes"]
    narration = " ".join(s for scene in scenes for s in scene["sentences"])
    narration_words = content_words(narration)
    source_words = content_words(record["source_text"])

    # Coverage of the source's sections.
    section_scores = []
    for section in record["structure"]["sections"]:
        words = content_words(section["title"] + " " + " ".join(section["content"]))
        if words:
            section_scores.append(len(words & narration_words) / len(words))
    covered = sum(s >= 0.5 for s in section_scores)

    # On-screen text grounded in source or narration.
    labels = [l for scene in scenes for l in _visual_labels(scene["visual"])]
    grounded = [
        l for l in labels
        if (w := content_words(l)) and len(w & (source_words | narration_words)) / len(w) >= 0.5
    ]

    # Visual planner outcomes.
    outcomes = {"first try": 0, "after retry": 0, "fallback": 0}
    for scene in scenes:
        reason = scene["visual"].get("reason", "")
        if reason.startswith("fallback"):
            outcomes["fallback"] += 1
        elif reason.startswith("[attempt 1]"):
            outcomes["first try"] += 1
        else:
            outcomes["after retry"] += 1

    # Sync: element reveal relative to the start of its sentence.
    latencies, before_end = [], 0
    for scene in scenes:
        plan = VisualPlan.model_validate(scene["visual"])
        timing = build_timing(plan, scene["sentences"], [tuple(s) for s in scene["spans"]],
                              scene["audio_seconds"])
        elements = plan.element_sentences()
        for eid, idx in elements.items():
            start, end = scene["spans"][min(max(idx, 0), len(scene["spans"]) - 1)]
            latencies.append(timing.cues[eid] - start)
            before_end += timing.cues[eid] <= end
    video = Path(record_path).with_suffix(".mp4")
    v_dur, a_dur = _media_durations(video) if video.exists() else (0.0, 0.0)

    words = len(narration.split())
    audio = sum(s["audio_seconds"] for s in scenes)
    speech = sum(e - b for s in scenes for b, e in s["spans"])

    rows = [
        "| Metric | Value |", "|---|---|",
        f"| Scenes | {len(scenes)} |",
        f"| Video length | {v_dur:.1f} s |",
        f"| Source sections covered (>=50% of their key words taught) | {covered}/{len(section_scores)} ({100 * covered / max(len(section_scores), 1):.0f}%) |",
        f"| Mean section coverage | {100 * statistics.mean(section_scores or [0]):.0f}% |",
        f"| On-screen labels grounded in source/narration | {len(grounded)}/{len(labels)} ({100 * len(grounded) / max(len(labels), 1):.0f}%) |",
        f"| Visual plans: first try / after retry / fallback | {outcomes['first try']} / {outcomes['after retry']} / {outcomes['fallback']} |",
        f"| Elements shown while their sentence is spoken | {before_end}/{len(latencies)} ({100 * before_end / max(len(latencies), 1):.0f}%) |",
        f"| Median reveal delay after sentence start | {statistics.median(latencies or [0]):.2f} s |",
        f"| Video vs audio length difference | {abs(v_dur - a_dur) * 1000:.0f} ms |",
        f"| Speaking rate | {words / max(speech, 1):.2f} words/s |",
        f"| Speech share of runtime (rest: intros, pauses) | {100 * speech / max(audio, 1):.0f}% |",
        f"| Generation time / video length | {record['generation_seconds'] / max(v_dur, 1):.1f}x |",
    ]

    if judge:
        from app.common import llm_client
        from app.common.llm_client import generate_json

        llm_client.ALLOW_LOCAL_FALLBACK = False
        counts = {"supported": 0, "unsupported": 0, "contradicted": 0}
        contradicted = []
        for scene in scenes:
            data = generate_json(
                JUDGE_PROMPT + record["source_text"][:6000] + "\n\nNARRATION:\n"
                + " ".join(scene["sentences"]), temperature=0.0, label="judge",
            )
            for claim in data.get("claims", []):
                label = claim.get("label", "")
                if label in counts:
                    counts[label] += 1
                if label == "contradicted":
                    contradicted.append(claim.get("claim", ""))
        total = sum(counts.values()) or 1
        rows += [
            f"| Narration claims judged | {total} |",
            f"| Supported by source | {100 * counts['supported'] / total:.0f}% |",
            f"| Not in source (extra explanation) | {100 * counts['unsupported'] / total:.0f}% |",
            f"| Contradicting the source | {100 * counts['contradicted'] / total:.0f}% |",
        ]
        for c in contradicted:
            print(f"[judge] contradicted: {c}")

    table = "\n".join(rows)
    print(table)
    return table


if __name__ == "__main__":
    main(sys.argv[1], "--judge" in sys.argv)
