# Evaluation results

All numbers come from the scripts in this folder (run on 2026-10-13).

## 1. Layout engine (`python -m evaluation.eval_layout 300`)

1,200 randomly generated visual plans (seed 42), 300 per type, with
3-7 nodes / 2-8 steps / 2-4 columns x 1-6 rows / 1-6 points and labels of
1-14 words.

| Visual type | Cases | Success | No overlap | In bounds | Connectors OK | Text fits | Edge labels shown | Font mean / min |
|---|---|---|---|---|---|---|---|---|
| concept_map | 300 | 99.7% | 100.0% | 100.0% | 100.0% | 100.0% | 91.4% | 24.4 / 14 |
| process_flow | 300 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | n/a | 25.9 / 20 |
| comparison | 300 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | n/a | 22.3 / 14 |
| key_points | 300 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | n/a | 31.5 / 24 |

"Connectors OK": every arrow starts and ends on its own boxes and crosses no
other box. Edge labels that cannot be placed without overlap are omitted
(8.6% on crowded maps) instead of drawn over other elements. The one failed
concept map falls back to a key-points visual in the pipeline.

## 2. Handwriting transcription (`python -m evaluation.eval_transcription`)

One handwritten page (`samples/agentic_ai_notes.jpeg`) against a manual
reference transcript (`evaluation/data/agentic_ai_page1.txt`).

| Transcriber | CER | WER |
|---|---|---|
| Local gemma3:4b (original baseline) | 19.3% | 23.9% |
| qwen3.8-27b via Groq (current), as produced | 3.5% | 3.7% |
| qwen3.8-27b via Groq (current), link prefixes removed | 0.0% | 0.0% |

"Link prefixes" are the `<term> ->` markers the transcriber adds on
purpose to record which term a hand-drawn arrow points from.

## 3. Generated lesson (`python -m evaluation.eval_lesson <record>.json --judge`)

Lesson generated from the same page.

| Metric | Value |
|---|---|
| Scenes | 3 |
| Video length | 91.8 s |
| Source sections covered (>=50% of their key words taught) | 6/8 (75%) |
| Mean section coverage | 74% |
| On-screen labels grounded in source/narration | 31/33 (94%) |
| Visual plans: first try / after retry / fallback | 2 / 1 / 0 |
| Elements shown while their sentence is spoken | 15/15 (100%) |
| Median reveal delay after sentence start | 0.15 s |
| Video vs audio length difference | 45 ms |
| Speaking rate | 2.46 words/s |
| Speech share of runtime (rest: intros, pauses) | 88% |
| Generation time / video length | 1.6x |
| Narration claims judged (LLM-as-judge, gpt-oss-120b) | 16 |
| Supported by source | 56% |
| Not in source (extra explanation) | 44% |
| Contradicting the source | 0% |

"Not in source" claims are explanations added to teach the notes (for
example what an AI agent does); none contradicted the source.

## Long documents

A 14-page PDF (5,465 words) was read completely (12 parts, 56 sections) and
planned into 11 scenes covering the whole document; two scenes were
rendered end to end as a sample.

## Limitations

- Transcription and lesson metrics come from a single page; the reference
  transcript was made by hand from the image.
- Coverage and grounding use word overlap, a proxy rather than a semantic
  judgement.
- The faithfulness judge is itself an LLM; contradicted / supported labels
  were not double-checked by a person.
- A larger multi-subject test set is the next step.
