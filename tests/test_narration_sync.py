"""Sentence splitting and narration-to-visual timing (no LLM needed)."""

from app.content.narration import split_sentences
from app.video.choreography import build_timing
from app.video.schemas import ProcessStep, VisualPlan
from app.video.visual_planner import normalize_plan, validate_plan
from app.video.schemas import ConceptEdge, ConceptNode


def test_split_keeps_abbreviations_and_decimals():
    text = "Resistance is about 2.5 ohms, e.g. in a small resistor. Current is I. Voltage then rises across R. Done now here."
    parts = split_sentences(text)
    assert parts[0] == "Resistance is about 2.5 ohms, e.g. in a small resistor. Current is I."
    assert parts[-1].endswith("Done now here.")


def test_elements_appear_at_their_sentence_and_refocus_on_mention():
    sentences = ["First the data is cleaned.", "Then the model is trained.", "The cleaned data is reused."]
    spans = [(0.7, 2.0), (2.3, 4.0), (4.3, 6.0)]
    plan = VisualPlan(
        visual_type="process_flow",
        steps=[ProcessStep(id="a", label="Clean data", sentence=0),
               ProcessStep(id="b", label="Train model", sentence=1)],
    )
    timing = build_timing(plan, sentences, spans, 7.0)
    assert timing.cues["a"] < timing.cues["b"]
    assert 0.7 <= timing.cues["a"] < 2.0
    assert (4.3, 6.0) in timing.focus["a"]  # "cleaned data" mentioned again
    assert len(timing.captions) == 3


def test_missing_cues_are_inferred_from_sentence_text():
    sentences = ["Photosynthesis happens in chloroplasts.", "Chlorophyll absorbs light energy."]
    plan = VisualPlan(
        visual_type="concept_map",
        nodes=[ConceptNode(id="c", label="Chloroplast"), ConceptNode(id="l", label="Light energy"),
               ConceptNode(id="h", label="Chlorophyll")],
        edges=[ConceptEdge(source="h", target="l", label="absorbs"),
               ConceptEdge(source="c", target="h", label="contains")],
    )
    plan = normalize_plan(plan, sentences)
    assert {n.id: n.sentence for n in plan.nodes}["l"] == 1


def test_description_nodes_are_rejected():
    plan = VisualPlan(
        visual_type="concept_map",
        nodes=[ConceptNode(id="a", label="Connect the central node to all other nodes."),
               ConceptNode(id="b", label="Cache"), ConceptNode(id="c", label="CPU")],
        edges=[ConceptEdge(source="a", target="b"), ConceptEdge(source="b", target="c")],
    )
    assert any("short concept names" in p for p in validate_plan(plan))


def test_unconnected_nodes_are_dropped():
    plan = VisualPlan(
        visual_type="concept_map",
        nodes=[ConceptNode(id="a", label="Router"), ConceptNode(id="b", label="Switch"),
               ConceptNode(id="c", label="Host"), ConceptNode(id="z", label="Stray")],
        edges=[ConceptEdge(source="a", target="b"), ConceptEdge(source="b", target="c"),
               ConceptEdge(source="c", target="missing")],
    )
    plan = normalize_plan(plan, ["Routers connect to switches, which connect hosts."])
    assert [n.id for n in plan.nodes] == ["a", "b", "c"]
    assert len(plan.edges) == 2


def test_mostly_empty_comparison_is_rejected():
    from app.video.schemas import ComparisonRow
    plan = VisualPlan(
        visual_type="comparison", columns=["A", "B"],
        rows=[ComparisonRow(id=f"r{i}", criterion=f"c{i}", cells=[f"x{i}", "-"]) for i in range(4)],
    )
    assert any("empty" in p for p in validate_plan(plan))


def test_process_steps_reveal_in_order_and_start_immediately():
    sentences = ["Intro.", "Then step one.", "Then step two.", "Then step three."]
    plan = VisualPlan(
        visual_type="process_flow",
        steps=[ProcessStep(id="a", label="One", sentence=3),
               ProcessStep(id="b", label="Two", sentence=2),
               ProcessStep(id="c", label="Three", sentence=1)],
    )
    plan = normalize_plan(plan, sentences)
    assert [s.sentence for s in plan.steps] == [1, 2, 3]


def test_concept_map_builds_top_down_but_highlights_with_narration():
    from app.video.schemas import ConceptEdge, ConceptNode
    plan = VisualPlan(
        visual_type="concept_map",
        nodes=[ConceptNode(id="child", label="Child", sentence=0),
               ConceptNode(id="parent", label="Parent", sentence=2),
               ConceptNode(id="other", label="Other", sentence=1)],
        edges=[ConceptEdge(source="parent", target="child"),
               ConceptEdge(source="parent", target="other")],
    )
    timing = build_timing(plan, ["a", "b", "c"], [(1, 2), (3, 4), (5, 6)], 7)
    assert timing.cues["parent"] < timing.cues["child"]
    assert timing.focus["parent"][0] == (5, 6)


def test_terminology_only_fixes_spacing_and_case():
    from unittest import mock

    from app.pipeline import lesson_video as L

    terms = {"terms": [
        {"written": "lang graph", "official_name": "LangGraph", "confident": True},
        {"written": "agents", "official_name": "Agentic", "confident": True},
        {"written": "py torch", "official_name": "PyTorch", "confident": False},
    ]}
    with mock.patch.object(L, "generate_json", return_value=terms):
        out = L._resolve_terminology("lang graph basics; AI agents; py torch")
    assert out == "LangGraph basics; AI agents; py torch"
