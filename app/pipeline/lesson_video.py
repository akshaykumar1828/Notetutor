"""
End-to-end: learning material -> narrated, animated lesson MP4.

    python -m app.pipeline.lesson_video <image|pdf|txt|md path, or a topic>

Stages (each uses the previous stage's output):
    source text -> structure -> knowledge check -> lesson plan
    -> per scene: content -> content check -> narration sentences
       -> visual plan -> per-sentence TTS -> synced animation
    -> final MP4 containing every planned scene.
"""

import argparse
import re
import time
from dataclasses import dataclass
from pathlib import Path

from app.audio.tts import synthesize_narration
from app.common.llm_client import generate_json
from app.content.generator import generate_scene_content
from app.content.narration import content_words, split_sentences
from app.knowledge.verifier import verify_generated_content, verify_knowledge
from app.teaching.planner import create_lesson_plan
from app.video.renderer import build_scene_clip, render_lesson
from app.video.visual_planner import attach_image, create_visual_plan, fallback_plan
from app.vision.structure_client import structure_note

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
TEXT_SUFFIXES = {".txt", ".md"}
MIN_PDF_PAGE_CHARS = 40
# Each request must stay well under the free tier's per-minute token
# limit, so long documents are processed in chunks of about this size.
CHUNK_CHARS = 5000
# Spoken narration speed of the voices used, in words per second.
WORDS_PER_SECOND = 2.4


class LessonPipelineError(RuntimeError):
    """Raised when the lesson video cannot be produced."""


@dataclass
class SourceMaterial:
    text: str
    origin: str
    uncertain: list[str]
    chunks: list[str] = None  # pages (PDF) or paragraph groups

    def __post_init__(self):
        if not self.chunks:
            self.chunks = _split_chunks(self.text)


def _split_chunks(text: str) -> list[str]:
    """Paragraph-preserving chunks of at most about CHUNK_CHARS."""
    chunks, current = [], ""
    for block in re.split(r"\n\s*\n", text):
        if current and len(current) + len(block) > CHUNK_CHARS:
            chunks.append(current.strip())
            current = ""
        current += block + "\n\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]


CACHE_DIR = Path("data/cache")


def _cache_key(data: bytes) -> str:
    """Hash of the input plus the prompts that process it, so improving a
    prompt automatically invalidates results made with the old one."""
    import hashlib

    from app.vision.structure_extractor import STRUCTURE_PROMPT
    from app.vision.transcription import TRANSCRIPTION_PROMPT

    digest = hashlib.sha1(data)
    digest.update((TRANSCRIPTION_PROMPT + STRUCTURE_PROMPT).encode("utf-8"))
    return digest.hexdigest()


def _cached_online_only() -> bool:
    """Only results from the online models are worth reusing."""
    from app.common import llm_client

    return llm_client.groq_available() and not llm_client.ALLOW_LOCAL_FALLBACK


def _transcribe_image(path: str) -> str:
    """Reads a page image once; later runs on the same file reuse the text."""
    from app.vision.transcription_client import transcribe_note

    cache = CACHE_DIR / "transcripts" / f"{_cache_key(Path(path).read_bytes())}.txt"
    if cache.exists():
        print(f"[pipeline] Reusing saved transcription of {Path(path).name}.")
        return cache.read_text(encoding="utf-8")
    text = transcribe_note(path).transcription
    if text.strip() and _cached_online_only():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    return text


def _structure_all(material: SourceMaterial):
    """Structures a long document part by part and merges the outline."""
    from app.vision.structure_schema import NoteStructure

    if len(material.chunks) == 1:
        return _structure(material.text)
    parts = []
    for index, chunk in enumerate(material.chunks, start=1):
        print(f"    structuring part {index}/{len(material.chunks)}")
        parts.append(_structure(chunk))
    sections = [s for part in parts for s in part.sections]
    return NoteStructure(topic=parts[0].topic, sections=sections)


def _structure(text: str):
    """Structure extraction, cached by the exact source text."""
    from app.vision.structure_schema import NoteStructure

    cache = CACHE_DIR / "structure" / f"{_cache_key(text.encode('utf-8'))}.json"
    if cache.exists():
        print("[pipeline] Reusing saved structure of this material.")
        return NoteStructure.model_validate_json(cache.read_text(encoding="utf-8"))
    structured = structure_note(text)
    if _cached_online_only():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(structured.model_dump_json(indent=2), encoding="utf-8")
    return structured


def _read_pdf(path: Path) -> str:
    import pymupdf

    from app.ingestion.document_ingestion import ingest_document

    texts = []
    with pymupdf.open(path) as pdf:
        page_texts = [page.get_text("text").strip() for page in pdf]

    scanned = [i for i, t in enumerate(page_texts) if len(t) < MIN_PDF_PAGE_CHARS]
    images = {}
    if scanned:
        print(f"[pipeline] {len(scanned)} PDF page(s) have no text layer; transcribing images.")
        document = ingest_document(path)
        images = {p.page_number - 1: p.image_path for p in document.pages}

    for i, text in enumerate(page_texts):
        texts.append(_transcribe_image(images[i]) if i in scanned else text)
    return "\n\n".join(t for t in texts if t.strip())


def _notes_for_topic(topic: str) -> str:
    data = generate_json(
        "Write accurate, concise study notes (about 250-450 words) that a "
        "good teacher would use to explain the topic below to a learner. "
        "Cover what it is, why it matters, how it works, its main "
        "components or stages, and any important contrast with related "
        "ideas. Use short headings and bullet points. Only include facts "
        "you are confident are correct.\n\n"
        f"TOPIC: {topic}\n\n"
        'Return JSON: {"notes": "..."}',
        temperature=0.2, label="topic_notes",
    )
    notes = str(data.get("notes", "")).strip()
    if not notes:
        raise LessonPipelineError("Could not generate notes for the topic.")
    return notes


TERMINOLOGY_PROMPT = """
You are checking terminology in a learner's notes before a lesson is made
from them.

List every term in the notes that is (or may be) the name of a specific
technology: a framework, library, product, model, language or tool.
Handwriting often splits one product name into two words or changes its
capitalisation.

For each, use the whole document as context (the topic, and the other
technologies the notes mention) to decide its exact official name. Only
give a name you are confident refers to a real, well-known technology that
fits this context; if unsure, keep the original wording and set
"confident" to false.

Return JSON: {"terms": [{"written": "...", "official_name": "...", "confident": true}]}

NOTES:
"""


def _resolve_terminology(text: str) -> str:
    """
    Writes technology names in their official form (e.g. a product name
    split into two words by handwriting), so later stages do not
    reinterpret them as ordinary words. Decided by the model from the
    document's own context; nothing is hardcoded.
    """
    try:
        data = generate_json(TERMINOLOGY_PROMPT + text, temperature=0.0,
                             label="terminology", light=True)
    except Exception as exc:
        print(f"[pipeline] Terminology check skipped: {exc}")
        return text

    for term in data.get("terms", []):
        if not isinstance(term, dict) or term.get("confident") is not True:
            continue
        written = str(term.get("written", "")).strip()
        official = str(term.get("official_name", "")).strip()
        if not written or not official or written == official:
            continue
        # Same letters apart from spacing/case only: never swap in a
        # different word.
        if re.sub(r"[\s\-_]", "", written).lower() != re.sub(r"[\s\-_]", "", official).lower():
            continue
        pattern = r"(?<!\w)" + r"[\s\-_]*".join(map(re.escape, re.sub(r"[\s\-_]", "", written))) + r"(?!\w)"
        text, count = re.subn(pattern, official, text, flags=re.IGNORECASE)
        if count:
            print(f"[pipeline] Terminology: '{written}' -> '{official}' ({count}x)")
    return text


def load_source(source: str) -> SourceMaterial:
    path = Path(source)
    if path.exists() and path.is_file():
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            text = _transcribe_image(str(path))
        elif suffix == ".pdf":
            text = _read_pdf(path)
        elif suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
        else:
            raise LessonPipelineError(f"Unsupported file type: {suffix}")
        origin = path.name
    else:
        print(f"[pipeline] '{source}' is not a file; treating it as a topic.")
        text = _notes_for_topic(source)
        origin = f"topic: {source}"

    text = text.strip()
    if not text:
        raise LessonPipelineError("The source material contains no text.")
    chunks = _split_chunks(text)
    if len(chunks) > 1:
        print(f"[pipeline] Long source ({len(text)} characters): processing in {len(chunks)} parts.")
    chunks = [_resolve_terminology(c) for c in chunks]
    return SourceMaterial(text="\n\n".join(chunks), origin=origin, uncertain=[], chunks=chunks)


def _format_verified_knowledge(verification) -> str:
    lines = [f"Verified topic: {verification.verified_topic}"]
    for claim in verification.claims:
        lines.append(f"- [{claim.status}] {claim.claim}")
        if claim.correction:
            lines.append(f"  Correction: {claim.correction}")
    for note in verification.overall_notes:
        lines.append(f"- {note}")
    return "\n".join(lines)


def _length_budget(text: str) -> tuple[int, int]:
    """
    (number of scenes, narration words per scene). The video grows much
    more slowly than the document: about 2 minutes for one page of notes,
    about 6 for five pages, and never more than about 10 minutes.
    """
    words = len(text.split())
    seconds = min(max(90 + words * 0.13, 105), 600)
    scenes = max(3, min(14, round(seconds / 32)))
    per_scene = int((seconds / scenes - 3) * WORDS_PER_SECOND)
    return scenes, max(per_scene, 45)


def _outline(structured, max_chars: int = 9000):
    """
    The structure, trimmed step by step (fewer and shorter points per
    section, finally titles only) so a long document's outline still fits
    in one request.
    """
    from app.vision.structure_schema import NoteSection, NoteStructure

    for per_section, length in ((8, 220), (4, 160), (2, 120), (1, 90), (0, 0)):
        sections = [
            NoteSection(title=s.title, content=[c[:length] for c in s.content[:per_section]])
            for s in structured.sections
        ]
        outline = NoteStructure(topic=structured.topic, sections=sections)
        if len(outline.model_dump_json(exclude_defaults=False)) <= max_chars:
            break
    return outline


def _relevant_source(material: SourceMaterial, scene, max_chars: int = 4500) -> str:
    """The parts of the source this scene is about (all of it if short)."""
    if len(material.text) <= max_chars:
        return material.text
    wanted = content_words(" ".join(
        [scene.title, scene.purpose] + list(scene.key_concepts) + list(scene.key_points)
    ))
    ranked = sorted(
        material.chunks,
        key=lambda c: len(wanted & content_words(c)) / (len(wanted) or 1),
        reverse=True,
    )
    picked, size = [], 0
    for chunk in ranked:
        if size + len(chunk) > max_chars and picked:
            break
        picked.append(chunk[: max_chars - size])
        size += len(picked[-1])
    return "\n\n".join(picked)


def _relevant_sections(structured, scene, max_chars: int = 5000):
    """Sub-structure used to fact-check one scene of a long document."""
    from app.vision.structure_schema import NoteStructure

    if len(structured.model_dump_json()) <= max_chars:
        return structured
    wanted = content_words(" ".join(
        [scene.title, scene.purpose] + list(scene.key_concepts) + list(scene.key_points)
    ))
    ranked = sorted(
        structured.sections,
        key=lambda s: len(wanted & content_words(s.title + " " + " ".join(s.content))),
        reverse=True,
    )
    picked = []
    for section in ranked:
        picked.append(section)
        if len(NoteStructure(topic=structured.topic, sections=picked).model_dump_json()) > max_chars:
            picked.pop()
            break
    return NoteStructure(topic=structured.topic, sections=picked or ranked[:1])


def _check_online_models(allow_local: bool) -> None:
    """
    Fails fast when the online models are unreachable, instead of silently
    spending hours on the much weaker local model.
    """
    from app.common.llm_client import LLMCallError, call_groq, groq_available

    if not groq_available():
        print("[pipeline] GROQ_API_KEY not set: using local models only (lower quality).")
        return
    try:
        call_groq('Reply with JSON {"ok": true}', temperature=0, max_tokens=200)
    except LLMCallError as exc:
        message = (
            "Cannot reach the online models (Groq). Check your internet connection; "
            "a 'certificate verify failed' error usually means the network or an "
            "antivirus is intercepting HTTPS.\n"
            f"Details: {str(exc)[:300]}\n"
            "Re-run with --allow-local to continue with the local model anyway "
            "(much slower and less accurate)."
        )
        if not allow_local:
            raise LessonPipelineError(message) from exc
        print(f"[pipeline] WARNING: {message}")


class _StageTimer:
    """Wall-clock time per stage plus AI usage, for the end-of-run report."""

    def __init__(self):
        from app.common import llm_client

        self.stats = llm_client.STATS
        for key in self.stats:
            self.stats[key] = 0
        self.last = time.time()
        self.stages: dict[str, float] = {}

    def mark(self, stage: str) -> None:
        now = time.time()
        self.stages[stage] = round(self.stages.get(stage, 0) + now - self.last, 1)
        self.last = now

    def summary(self) -> dict:
        return {"stages": self.stages,
                "ai": {k: round(v, 1) if isinstance(v, float) else v
                       for k, v in self.stats.items()}}

    def report(self) -> str:
        parts = ", ".join(f"{k} {v:.0f}s" for k, v in self.stages.items())
        s = self.stats
        return (f"[timing] {parts}\n[timing] AI: {s['requests']} requests, "
                f"{s['request_seconds']:.0f}s answering, {s['wait_seconds']:.0f}s waiting on "
                f"rate limits, {s['tokens']} tokens, {s['cache_hits']} reused from cache")


def _write_record(output, material, structured, lesson, scenes, seconds,
                  timing: dict | None = None) -> None:
    """Saves what the lesson was made from, next to the video, for review
    and for the evaluation scripts in evaluation/."""
    import json

    record = {
        "source": material.origin,
        "source_text": material.text,
        "structure": structured.model_dump(),
        "lesson": {"title": lesson.title, "subject": lesson.subject,
                   "audience": lesson.audience},
        "scenes": scenes,
        "generation_seconds": round(seconds, 1),
        "timing": timing or {},
    }
    Path(output).with_suffix(".json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _write_review(output, material, scenes) -> None:
    """
    After rendering, an LLM judge checks every narration claim against the
    source and writes <video>.review.md listing anything doubtful with its
    timestamp, so a person can check those spots before sharing the video.
    Never blocks the video: if the check cannot run, it is just skipped.
    """
    from evaluation.eval_lesson import JUDGE_PROMPT

    lines = [f"# Review: {Path(output).name}", ""]
    flagged = 0
    offset = 0.0
    try:
        for scene in scenes:
            data = generate_json(
                JUDGE_PROMPT + _relevant_source(material, _SceneView(scene), 6000)
                + "\n\nNARRATION:\n" + " ".join(scene["sentences"]),
                temperature=0.0, label="review", light=True,
            )
            for claim in data.get("claims", []):
                label = claim.get("label")
                if label not in ("contradicted", "unsupported"):
                    continue
                text = claim.get("claim", "")
                index = max(range(len(scene["sentences"])),
                            key=lambda i: len(content_words(text) & content_words(scene["sentences"][i])))
                at = offset + scene["spans"][index][0]
                mark = "CHECK" if label == "contradicted" else "not in source"
                lines.append(f"- **{int(at // 60)}:{int(at % 60):02d}** ({scene['title']}) "
                             f"[{mark}] {text}")
                flagged += label == "contradicted"
            offset += scene["audio_seconds"]
    except Exception as exc:
        print(f"[pipeline] Review skipped: {exc}")
        return
    lines.insert(2, f"{flagged} claim(s) contradict the source. "
                    "'not in source' lines are added explanation; usually fine, but worth a look.\n")
    Path(output).with_suffix(".review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[pipeline] Review: {flagged} contradicted claim(s); see {Path(output).with_suffix('.review.md')}")


class _SceneView:
    """Lets a saved scene record be used where a plan scene is expected."""

    def __init__(self, record: dict):
        self.title = record["title"]
        self.purpose = ""
        self.key_concepts = []
        self.key_points = record["sentences"]


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60] or "lesson"


def generate_lesson_video(
    source: str,
    output_path: str | None = None,
    show_captions: bool = True,
    music: bool = True,
    allow_local: bool = False,
    verify: bool = True,
    review: bool = True,
) -> str:
    started = time.time()
    from app.common import llm_client

    llm_client.ALLOW_LOCAL_FALLBACK = allow_local
    _check_online_models(allow_local)

    timer = _StageTimer()
    print("[1] Reading source material...")
    material = load_source(source)
    timer.mark("reading source")

    print("[2] Extracting structure...")
    structured = _structure_all(material)
    long_source = len(material.text) > CHUNK_CHARS
    timer.mark("structuring")

    print("[3] Checking facts in the notes...")
    try:
        verified = _format_verified_knowledge(
            verify_knowledge(_outline(structured, 6000), material.uncertain)
        )
    except Exception as exc:
        print(f"[pipeline] Knowledge check failed, continuing without it: {exc}")
        verified = ""

    timer.mark("fact check (notes)")
    print("[4] Planning the lesson...")
    budget, words_per_scene = _length_budget(material.text)
    print(f"    Length budget: {budget} scenes, about {words_per_scene} words each")
    plan_notes = _outline(structured, 5000 if long_source else 9000)
    plan_text = "" if long_source else material.text
    lesson = create_lesson_plan(
        structured_note=plan_notes,
        transcription=plan_text,
        uncertain_content=material.uncertain,
        max_scenes=budget,
    )
    if len(lesson.scenes) < budget - 1:
        print(f"    Plan has {len(lesson.scenes)} scenes, budget {budget}; re-planning once.")
        lesson = create_lesson_plan(
            structured_note=plan_notes,
            transcription=plan_text,
            uncertain_content=material.uncertain,
            max_scenes=budget,
            extra_instruction=(
                f"YOUR PREVIOUS PLAN HAD ONLY {len(lesson.scenes)} SCENES, which "
                f"skips parts of the document. Use {budget - 1}-{budget} scenes "
                "so every section of the document is covered."
            ),
        )
    if not lesson.scenes:
        raise LessonPipelineError("The lesson plan contains no scenes.")
    print(f"    {lesson.title}: {len(lesson.scenes)} scenes")
    timer.mark("planning")

    from concurrent.futures import ThreadPoolExecutor

    clips = []
    records = []
    pending = []
    voice_pool = ThreadPoolExecutor(max_workers=1)
    used_visuals: list[str] = []
    total = len(lesson.scenes)
    for position, scene in enumerate(lesson.scenes, start=1):
        print(f"\n=== Scene {position}/{total}: {scene.title} ({scene.teaching_method})")

        content = generate_scene_content(
            scene=scene,
            dominant_topic=lesson.subject,
            audience=lesson.audience,
            verified_knowledge=(
                f"{verified}\n\nSOURCE MATERIAL (teach only what this supports; "
                f"spell terms exactly as written here):\n{_relevant_source(material, scene)}"
            ),
            target_words=words_per_scene,
        )

        if verify:
            try:
                check = verify_generated_content(content, _relevant_sections(structured, scene))
                if not check.is_accurate and check.corrected_explanation:
                    print(f"    Correcting content: {check.misplaced_claims}")
                    content.explanation = check.corrected_explanation
            except Exception as exc:
                print(f"    Content check failed, keeping generated content: {exc}")

        sentences = split_sentences(content.explanation)
        if not sentences:
            raise LessonPipelineError(f"Scene {scene.scene_id} has no narration.")

        plan = attach_image(
            create_visual_plan(scene, content, sentences, previous_types=used_visuals),
            context=f"{lesson.subject}: {scene.title}",
        )
        print(f"    visual: {plan.visual_type} ({plan.reason})")
        used_visuals.append(plan.visual_type)

        # The voice is synthesised in the background while the next scene
        # is being written (CPU work overlapping network waits).
        scene_key = f"{position:02d}_{_safe_name(scene.scene_id)}"
        pending.append((position, scene, content, sentences, plan,
                        voice_pool.submit(synthesize_narration, sentences, scene_key)))

    timer.mark("writing scenes")
    for position, scene, content, sentences, plan, future in pending:
        narration = future.result()
        records.append({
            "scene_id": scene.scene_id,
            "title": scene.title,
            "teaching_method": scene.teaching_method,
            "sentences": sentences,
            "spans": narration.spans,
            "audio_seconds": narration.duration,
            "visual": plan.model_dump(),
        })
        clips.append(
            build_scene_clip(
                title=scene.title,
                plan=plan,
                sentences=sentences,
                narration=narration,
                progress_label=f"{position} / {total}",
                show_captions=show_captions,
                fallback_plan=fallback_plan(content, sentences),
            )
        )
    voice_pool.shutdown()
    timer.mark("voice (remaining)")

    output = output_path or f"data/video/{_safe_name(lesson.title)}.mp4"
    print(f"\n[5] Rendering {len(clips)} scenes to {output}...")
    speech, offset = [], 0.0
    for record in records:  # sentence times across the whole lesson, for ducking
        speech += [(offset + a, offset + b) for a, b in record["spans"]]
        offset += record["audio_seconds"]
    render_lesson(clips, output, music=music, title=lesson.title, speech=speech)
    timer.mark("rendering")
    if review:
        _write_review(output, material, records)
        timer.mark("fact review")
    _write_record(output, material, structured, lesson, records, time.time() - started,
                  timer.summary())
    print(timer.report())
    print(f"Done in {time.time() - started:.0f}s: {output}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an animated lesson video.")
    parser.add_argument("source", help="image, PDF, .txt/.md file, or a topic")
    parser.add_argument("-o", "--output", help="output MP4 path")
    parser.add_argument("--no-captions", action="store_true")
    parser.add_argument("--no-music", action="store_true")
    parser.add_argument(
        "--no-verify", action="store_true",
        help="skip the per-scene fact-check (saves daily quota, less safe)",
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="ignore saved AI answers and generate everything anew",
    )
    parser.add_argument(
        "--no-review", action="store_true",
        help="skip the after-render fact review file",
    )
    parser.add_argument(
        "--allow-local", action="store_true",
        help="continue with local models if the online models are unreachable",
    )
    args = parser.parse_args()
    if args.fresh:
        from app.common import llm_client

        llm_client.CACHE_ENABLED = False
    generate_lesson_video(
        args.source, args.output,
        show_captions=not args.no_captions, music=not args.no_music,
        allow_local=args.allow_local, verify=not args.no_verify,
        review=not args.no_review,
    )


if __name__ == "__main__":
    main()
