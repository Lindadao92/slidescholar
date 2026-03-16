"""Slide planner that uses Claude to turn parsed papers into presentation outlines."""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, Future

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("slidescholar")

MODEL = "claude-sonnet-4-6"
MODEL_FAST = "claude-haiku-4-5-20251001"
MAX_RETRIES = 2
RETRY_DELAY = 2  # seconds

TALK_CONFIGS = {
    "lightning":  {"minutes": 5,  "format": "lightning/spotlight"},
    "short":      {"minutes": 10, "format": "short oral"},
    "conference": {"minutes": 15, "format": "conference oral"},
    "extended":   {"minutes": 20, "format": "extended oral"},
    "invited":    {"minutes": 30, "format": "invited talk"},
    "seminar":    {"minutes": 45, "format": "seminar/colloquium"},
    "defense":    {"minutes": 60, "format": "thesis defense"},
}

# --- Dynamic slide count ---
# Academic talks average ~1.2-1.5 min per content slide (incl. transitions)

SLIDE_RANGES = {
    5:  {"main": (4, 6),    "backup": (2, 3)},
    10: {"main": (7, 9),    "backup": (3, 4)},
    15: {"main": (10, 13),  "backup": (4, 6)},
    20: {"main": (14, 18),  "backup": (5, 7)},
    30: {"main": (18, 22),  "backup": (5, 7)},
    45: {"main": (22, 25),  "backup": (6, 8)},
    60: {"main": (25, 30),  "backup": (8, 10)},
}


def _get_slide_range(minutes: int) -> dict:
    """Return {"main": (min, max), "backup": (min, max)} for given talk length."""
    if minutes in SLIDE_RANGES:
        return SLIDE_RANGES[minutes]
    # Find closest cutoff
    for cutoff in sorted(SLIDE_RANGES.keys()):
        if minutes <= cutoff:
            return SLIDE_RANGES[cutoff]
    return SLIDE_RANGES[60]


# Words too common to signal relevance in keyword matching
_STOPWORDS = frozenset(
    "the a an is are was were be been being have has had do does did will would "
    "could should may might can shall to of in for on with at by from as into "
    "through during before after above below between and but or not no so if "
    "than that this these those it its we our they their each all both such "
    "only also very more most other some new using used based show shows shown "
    "figure fig table tab results method approach proposed paper model models "
    "data dataset performance our which where when how what".split()
)


def _estimate_content_density(paper: dict) -> int:
    """Score paper complexity to determine where within the slide range to land.

    Higher score = more content = more slides needed.
    """
    score = 0

    # Sections that look like experiments or case studies
    sections = paper.get("sections", [])
    results_keywords = {"experiment", "case study", "evaluation", "result",
                        "analysis", "ablation", "study", "finding"}
    n_results_sections = 0
    for sec in sections:
        name_lower = sec.get("name", "").lower()
        if any(kw in name_lower for kw in results_keywords):
            n_results_sections += 1
    score += min(n_results_sections, 12)

    # Figures
    n_figures = paper.get("num_figures", len(paper.get("figures", [])))
    score += min(n_figures, 6)

    # Paper length
    n_pages = paper.get("num_pages", 10)
    if n_pages > 30:
        score += 4
    elif n_pages > 15:
        score += 2
    elif n_pages < 8:
        score -= 2

    # Sections count (more sections = more structure to cover)
    n_sections = len(sections)
    if n_sections > 8:
        score += 2
    elif n_sections > 5:
        score += 1

    return max(0, score)


def _count_experiments(paper: dict) -> dict:
    """Count distinct experiments in a paper using multiple heuristic signals.

    Returns:
        {
            "count": int,          # best-estimate experiment count
            "confidence": float,   # 0.0-1.0, how much to trust the count
            "signals": dict,       # per-signal breakdown for debugging
        }

    Signals (scored independently, then merged):
      1. Section names with experiment/result keywords
      2. Sections with significant quantitative content
      3. Numbered experiment labels (Experiment 1, Study 2, RQ1, etc.)
      4. Ablation study mentions (each distinct ablation = ~1 experiment)
      5. Distinct dataset names appearing in results context
      6. Baseline comparison tables ("Table N" with comparison keywords)
      7. Figure count floor (every ~5 figures ≈ 1 experiment)
    """
    sections = paper.get("sections", [])
    full_text = "\n".join(sec.get("text", "") for sec in sections)
    full_lower = full_text.lower()

    # ------------------------------------------------------------------
    # Signal 1: Section names with experiment keywords
    # ------------------------------------------------------------------
    name_keywords = {"experiment", "case study", "evaluation", "result",
                     "analysis", "ablation", "study", "finding",
                     "benchmark", "comparison", "performance"}
    name_count = 0
    for sec in sections:
        name_lower = sec.get("name", "").lower()
        if any(kw in name_lower for kw in name_keywords):
            name_count += 1

    # ------------------------------------------------------------------
    # Signal 2: Sections with significant quantitative content
    # ------------------------------------------------------------------
    quant_keywords = {"table ", "figure ", "fig.", "tab.", "accuracy", "precision",
                      "recall", "f1", "bleu", "rouge", "auc", "error rate",
                      "outperform", "baseline", "state-of-the-art", "sota",
                      "compared to", "improvement", "versus", "vs."}
    text_count = 0
    for sec in sections:
        text_lower = sec.get("text", "").lower()[:2000]
        matches = sum(1 for kw in quant_keywords if kw in text_lower)
        if matches >= 3:
            text_count += 1

    # ------------------------------------------------------------------
    # Signal 3: Numbered experiment labels
    # Matches: Experiment 1, Study 2, RQ1, RQ 3, Case Study 4, Task 2
    # ------------------------------------------------------------------
    label_patterns = [
        r"experiment\s*(\d+)",
        r"study\s*(\d+)",
        r"case\s+study\s*(\d+)",
        r"rq\s*(\d+)",
        r"task\s+(\d+)(?=\s*[:\.\)])",    # "Task 2:" but not "task 200 epochs"
        r"setting\s*(\d+)",
    ]
    label_nums: set[str] = set()
    for pat in label_patterns:
        for m in re.finditer(pat, full_lower):
            label_nums.add(f"{pat.split('(')[0].strip()}_{m.group(1)}")
    label_count = len(label_nums)

    # ------------------------------------------------------------------
    # Signal 4: Ablation study mentions
    # Each distinctly named ablation ≈ 1 sub-experiment.
    # Look for "w/o X", "without X", "removing X", "-X" (ablation rows)
    # ------------------------------------------------------------------
    ablation_variants: set[str] = set()
    for m in re.finditer(
        r"(?:w/o|without|removing|ablating|no)\s+([a-z][a-z\s]{2,20}?)(?=[,\.\;\)\n])",
        full_lower,
    ):
        # Normalise to first two words to deduplicate "attention" vs "attention mechanism"
        words = m.group(1).strip().split()[:2]
        ablation_variants.add(" ".join(words))
    ablation_count = len(ablation_variants)

    # ------------------------------------------------------------------
    # Signal 5: Distinct dataset names in results context
    # Well-known benchmark datasets and repeated capitalised names
    # near results/evaluation text.
    # ------------------------------------------------------------------
    known_datasets = {
        "imagenet", "cifar", "cifar-10", "cifar-100", "mnist",
        "coco", "voc", "pascal",
        "squad", "glue", "superglue", "mnli", "sst", "sst-2", "qqp",
        "wmt", "iwslt", "newstest",
        "conll", "ontonotes", "penn treebank", "ptb",
        "librispeech", "commonvoice",
        "imdb", "yelp", "amazon",
        "openwebtext", "c4", "pile",
    }
    # Scan the results-relevant sections (sections mentioning results keywords)
    results_text = ""
    for sec in sections:
        n = sec.get("name", "").lower()
        if any(kw in n for kw in {"result", "experiment", "evaluation", "benchmark",
                                   "comparison", "ablation"}):
            results_text += " " + sec.get("text", "")
    if not results_text:
        results_text = full_text  # fallback: whole paper

    results_lower = results_text.lower()
    found_datasets: set[str] = set()
    for ds in known_datasets:
        if ds in results_lower:
            found_datasets.add(ds)

    # Also look for capitalised multi-word names appearing 3+ times
    # near quantitative keywords — likely custom dataset or benchmark names.
    cap_names = re.findall(r"\b([A-Z][A-Za-z]+-?[A-Z0-9][A-Za-z0-9]*)\b", results_text)
    for name in set(cap_names):
        if cap_names.count(name) >= 3 and name.lower() not in _STOPWORDS:
            found_datasets.add(name.lower())

    dataset_count = len(found_datasets)

    # ------------------------------------------------------------------
    # Signal 6: Baseline comparison tables
    # Count "Table N" references co-occurring with comparison language.
    # ------------------------------------------------------------------
    table_refs = set(re.findall(r"(?:table|tab\.?)\s*(\d+)", full_lower))
    comparison_near_table = 0
    compare_words = {"compar", "baseline", "benchmark", "state-of-the-art",
                     "sota", "outperform", "surpass", "exceed", "ablat"}
    for tnum in table_refs:
        # Check a 500-char window around each "Table N" mention
        for m in re.finditer(rf"(?:table|tab\.?)\s*{tnum}", full_lower):
            window = full_lower[max(0, m.start() - 250):m.end() + 250]
            if any(cw in window for cw in compare_words):
                comparison_near_table += 1
                break  # count each table once
    comparison_table_count = comparison_near_table

    # ------------------------------------------------------------------
    # Signal 7: Figure count floor
    # ------------------------------------------------------------------
    n_figures = paper.get("num_figures", len(paper.get("figures", [])))
    figure_floor = max(3, n_figures // 5)

    # ------------------------------------------------------------------
    # Merge signals into a single count
    # ------------------------------------------------------------------
    # Each signal estimates experiment count from a different angle.
    # We take the MAX of "strong" signals (the most sensitive detector),
    # since we'd rather over-count than miss experiments.
    # Weak signals (ablation halved, figure floor) only matter as a floor.

    # Strong signals — each directly estimates experiment count
    strong: list[tuple[int, float]] = []  # (count, reliability_weight)

    if label_count >= 2:        # Numbered labels: most reliable
        strong.append((label_count, 3.0))
    if name_count >= 1:         # Section names
        strong.append((name_count, 2.0))
    if text_count >= 1:         # Quantitative sections
        strong.append((text_count, 1.5))
    if comparison_table_count >= 2:  # Comparison tables (1 is ubiquitous)
        strong.append((comparison_table_count, 1.5))
    if dataset_count >= 3:      # Distinct datasets (2 is common even in short papers)
        strong.append((dataset_count, 1.0))

    # Weak / supplementary signals — set a floor, don't drive the count
    weak_floor = figure_floor   # always ≥ 3
    if ablation_count >= 2:
        weak_floor = max(weak_floor, max(1, ablation_count // 2))

    if strong:
        # Primary count = max of strong signals (most sensitive detector).
        # Check with weighted average: if average is much lower, the max
        # might be an outlier — pull it down slightly.
        max_strong = max(c for c, _ in strong)
        total_w = sum(w for _, w in strong)
        weighted_avg = sum(c * w for c, w in strong) / total_w
        # Blend: 70% max, 30% weighted average — favour the highest signal
        blended = max_strong * 0.7 + weighted_avg * 0.3
        count = max(3, min(20, round(blended)))
        # Never go below the weak floor
        count = max(count, weak_floor)
    else:
        count = max(3, min(20, weak_floor))

    # ------------------------------------------------------------------
    # Confidence: how much do the signals agree?
    # ------------------------------------------------------------------
    # High confidence: multiple strong signals with similar values.
    # Low confidence: signals disagree widely, or only weak signals fire.
    strong_values = [c for c, _ in strong]

    if len(strong_values) >= 3:
        mean_val = sum(strong_values) / len(strong_values)
        if mean_val > 0:
            variance = sum((v - mean_val) ** 2 for v in strong_values) / len(strong_values)
            cv = (variance ** 0.5) / mean_val  # coefficient of variation
            agreement = max(0.0, 1.0 - cv * 0.7)
        else:
            agreement = 0.3
        # Breadth bonus: more independent signals = more trustworthy
        breadth = min(1.0, len(strong_values) / 5)
        # Magnitude factor: signals with small values (1-2) are weaker
        # evidence than signals with larger values (4+).  When no signal
        # exceeds the floor (3), the count is essentially guesswork.
        max_val = max(strong_values)
        magnitude = min(1.0, max_val / 5)  # full credit at 5+
        confidence = round(0.20 * agreement + 0.35 * breadth + 0.45 * magnitude, 2)
    elif len(strong_values) == 2:
        # Two signals: confidence depends on agreement and magnitude
        diff = abs(strong_values[0] - strong_values[1])
        avg = (strong_values[0] + strong_values[1]) / 2
        agreement = max(0.0, 1.0 - (diff / max(avg, 1)) * 0.5)
        magnitude = min(1.0, max(strong_values) / 4)
        confidence = round(0.15 + 0.20 * agreement + 0.15 * magnitude, 2)
    elif len(strong_values) == 1:
        magnitude = min(1.0, strong_values[0] / 4)
        confidence = round(0.20 + 0.15 * magnitude, 2)
    else:
        confidence = 0.15  # only weak signals (figure floor)

    confidence = max(0.1, min(1.0, confidence))

    signals = {
        "name_sections": name_count,
        "quant_sections": text_count,
        "numbered_labels": label_count,
        "ablation_variants": ablation_count,
        "datasets_found": dataset_count,
        "comparison_tables": comparison_table_count,
        "figure_floor": figure_floor,
    }
    log.info(
        "Experiment count: %d (confidence=%.2f) signals=%s",
        count, confidence, signals,
    )
    return {"count": count, "confidence": confidence, "signals": signals}


# =============================================================
# PAPER-TYPE CLASSIFICATION
# =============================================================

# Each paper type gets a different narrative arc and budget strategy.
PAPER_TYPES = {
    "empirical": {
        "label": "empirical research paper",
        "arc": [
            ("title", 1),
            ("motivation", "budget_motivation"),
            ("method", "budget_method"),
            ("results", "budget_results"),
            ("analysis", "budget_analysis"),
            ("conclusion", 1),
            ("thankyou", 1),
        ],
    },
    "survey": {
        "label": "survey / literature review",
        "arc": [
            ("title", 1),
            ("motivation", "budget_motivation"),
            ("taxonomy", "budget_taxonomy"),
            ("comparison", "budget_comparison"),
            ("research_gaps", "budget_gaps"),
            ("conclusion", 1),
            ("thankyou", 1),
        ],
    },
    "theory": {
        "label": "theoretical / mathematical paper",
        "arc": [
            ("title", 1),
            ("motivation", "budget_motivation"),
            ("formulation", "budget_formulation"),
            ("proof", "budget_proof"),
            ("implications", "budget_implications"),
            ("conclusion", 1),
            ("thankyou", 1),
        ],
    },
    "position": {
        "label": "position / opinion paper",
        "arc": [
            ("title", 1),
            ("motivation", "budget_motivation"),
            ("argument", "budget_argument"),
            ("analysis", "budget_analysis"),
            ("conclusion", 1),
            ("thankyou", 1),
        ],
    },
}


def classify_paper_type(paper: dict) -> dict:
    """Classify a paper into one of: empirical, survey, theory, position.

    Uses section names, content signals, and structural features to score
    each type. Returns the best match with a confidence score.

    Returns:
        {
            "type": str,            # "empirical" | "survey" | "theory" | "position"
            "confidence": float,    # 0.0-1.0
            "scores": dict,         # per-type raw scores for debugging
        }
    """
    sections = paper.get("sections", [])
    section_names = [sec.get("name", "").lower() for sec in sections]
    full_text = "\n".join(sec.get("text", "") for sec in sections).lower()
    n_sections = len(sections)
    n_pages = paper.get("num_pages", 10)
    n_figures = paper.get("num_figures", len(paper.get("figures", [])))

    # ------------------------------------------------------------------
    # Score each paper type independently
    # ------------------------------------------------------------------
    scores: dict[str, float] = {"empirical": 0, "survey": 0, "theory": 0, "position": 0}

    # --- Empirical signals ---
    experiment_names = {"experiment", "evaluation", "result", "ablation",
                        "benchmark", "baseline", "performance"}
    method_names = {"method", "approach", "architecture", "model", "system",
                    "framework", "implementation"}
    for name in section_names:
        if any(kw in name for kw in experiment_names):
            scores["empirical"] += 2.0
        if any(kw in name for kw in method_names):
            scores["empirical"] += 1.0
    # Quantitative language in body
    quant_terms = ["accuracy", "precision", "recall", "f1", "bleu", "rouge",
                   "outperform", "baseline", "state-of-the-art", "sota",
                   "table ", "fig.", "figure "]
    quant_hits = sum(1 for t in quant_terms if t in full_text)
    scores["empirical"] += min(quant_hits * 0.5, 5.0)
    if n_figures >= 3:
        scores["empirical"] += 1.0

    # --- Survey signals ---
    survey_names = {"related work", "literature review", "survey", "taxonomy",
                    "categorization", "classification of", "overview of"}
    for name in section_names:
        if any(kw in name for kw in survey_names):
            scores["survey"] += 3.0
    # Many citation patterns: "[N]" or "(Author, Year)" — surveys are citation-dense
    bracket_citations = len(re.findall(r"\[\d+(?:,\s*\d+)*\]", full_text))
    paren_citations = len(re.findall(r"\([A-Z][a-z]+(?:\s+et\s+al\.?)?,\s*\d{4}\)", full_text))
    total_citations = bracket_citations + paren_citations
    if total_citations > 80:
        scores["survey"] += 3.0
    elif total_citations > 40:
        scores["survey"] += 1.5
    # Surveys tend to have many sections and few figures
    if n_sections >= 8 and n_figures <= 3:
        scores["survey"] += 1.0
    # Penalty: surveys rarely have experiment/evaluation sections
    has_experiments = any(any(kw in name for kw in experiment_names) for name in section_names)
    if not has_experiments:
        scores["survey"] += 1.0

    # --- Theory signals ---
    theory_names = {"theorem", "proof", "lemma", "proposition", "corollary",
                    "theoretical", "convergence", "bound", "complexity analysis"}
    for name in section_names:
        if any(kw in name for kw in theory_names):
            scores["theory"] += 3.0
    # Theorem/proof language in body
    theory_body = ["theorem", "\\begin{proof}", "q.e.d.", "∎", "lemma",
                   "proposition", "corollary", "we prove", "it follows that",
                   "by induction", "necessary and sufficient"]
    theory_hits = sum(1 for t in theory_body if t in full_text)
    scores["theory"] += min(theory_hits * 0.8, 5.0)
    # Equation-heavy (LaTeX markers)
    equation_markers = len(re.findall(r"\\begin\{(?:equation|align|gather)\}", full_text))
    if equation_markers >= 3:
        scores["theory"] += 2.0
    # Penalty: theory papers rarely have many figures or experiment sections
    if not has_experiments and n_figures <= 3:
        scores["theory"] += 1.0

    # --- Position paper signals ---
    position_names = {"opinion", "perspective", "vision", "position",
                      "call to action", "manifesto", "commentary", "editorial"}
    for name in section_names:
        if any(kw in name for kw in position_names):
            scores["position"] += 3.0
    # Short papers with no method/experiment sections
    if n_pages <= 6:
        scores["position"] += 1.0
    if not has_experiments:
        scores["position"] += 1.0
    no_method = not any(any(kw in name for kw in method_names) for name in section_names)
    if no_method:
        scores["position"] += 1.0
    # Opinion language
    opinion_terms = ["we argue", "we believe", "we propose that",
                     "we advocate", "we call for", "in our view",
                     "should be", "ought to", "it is time"]
    opinion_hits = sum(1 for t in opinion_terms if t in full_text)
    scores["position"] += min(opinion_hits * 1.0, 4.0)

    # ------------------------------------------------------------------
    # Pick the winner
    # ------------------------------------------------------------------
    best_type = max(scores, key=scores.get)  # type: ignore[arg-type]
    best_score = scores[best_type]

    # Confidence: how much does the winner lead?
    sorted_scores = sorted(scores.values(), reverse=True)
    if best_score == 0:
        confidence = 0.2
    elif len(sorted_scores) >= 2 and sorted_scores[1] > 0:
        margin = (best_score - sorted_scores[1]) / best_score
        confidence = round(0.3 + 0.7 * min(margin, 1.0), 2)
    else:
        confidence = 0.8

    # If confidence is low (< 0.4), default to empirical — safest fallback
    if confidence < 0.4 and best_type != "empirical":
        # Check if empirical is a close second
        if scores["empirical"] >= best_score * 0.6:
            best_type = "empirical"
            confidence = 0.3

    log.info("Paper type: %s (confidence=%.2f) scores=%s", best_type, confidence, scores)
    return {"type": best_type, "confidence": confidence, "scores": scores}


def _allocate_budget_survey(main_slides: int) -> dict:
    """Budget for survey papers: taxonomy-heavy, no experiments."""
    fixed = 3  # title + conclusion + thankyou
    available = max(1, main_slides - fixed)

    # Surveys spend most time on taxonomy and comparison
    motivation = 1
    taxonomy = max(2, available // 3)
    comparison = max(2, available // 3)
    gaps = max(1, available - motivation - taxonomy - comparison)

    # Rebalance if over budget
    total = motivation + taxonomy + comparison + gaps
    while total > available and gaps > 1:
        gaps -= 1
        total -= 1
    while total > available and taxonomy > 2:
        taxonomy -= 1
        total -= 1

    return {
        "motivation": motivation,
        "taxonomy": taxonomy,
        "comparison": comparison,
        "gaps": gaps,
    }


def _allocate_budget_theory(main_slides: int) -> dict:
    """Budget for theory papers: formulation + proof heavy."""
    fixed = 3  # title + conclusion + thankyou
    available = max(1, main_slides - fixed)

    motivation = max(1, min(2, available // 4))
    formulation = max(1, available // 3)
    proof = max(1, available // 3)
    implications = max(1, available - motivation - formulation - proof)

    total = motivation + formulation + proof + implications
    while total > available and implications > 1:
        implications -= 1
        total -= 1
    while total > available and motivation > 1:
        motivation -= 1
        total -= 1

    return {
        "motivation": motivation,
        "formulation": formulation,
        "proof": proof,
        "implications": implications,
    }


def _allocate_budget_position(main_slides: int) -> dict:
    """Budget for position papers: argument-heavy, short."""
    fixed = 3  # title + conclusion + thankyou
    available = max(1, main_slides - fixed)

    motivation = max(1, min(2, available // 3))
    argument = max(2, (available * 2) // 3)
    analysis = max(1, available - motivation - argument)

    total = motivation + argument + analysis
    while total > available and analysis > 1:
        analysis -= 1
        total -= 1

    return {
        "motivation": motivation,
        "argument": argument,
        "analysis": analysis,
    }


# Descriptions for each arc section — used to generate the narrative arc text
_ARC_DESCRIPTIONS: dict[str, str] = {
    "title": "Title slide (paper title — exception to assertion rule)",
    "motivation": "Motivation: State the problem and why it matters as an assertion",
    "method": "Method: Architecture/approach as assertion claims",
    "results": "Results: Each result as an assertion with evidence",
    "analysis": "Analysis: Ablation, visualization, generalization",
    "conclusion": "Conclusion: Executive summary of proven claims",
    "thankyou": "Thank you",
    # Survey arc
    "taxonomy": "Taxonomy: Categorize the landscape into clear groups with visual overview",
    "comparison": "Comparison: Compare approaches/methods with tables and figures",
    "research_gaps": "Research Gaps: Identify open problems and missing coverage",
    # Theory arc
    "formulation": "Formulation: Define the problem mathematically with key equations",
    "proof": "Proof / Derivation: Walk through the main theoretical results step by step",
    "implications": "Implications: What the theoretical results mean in practice",
    # Position arc
    "argument": "Argument: Present the core claims with supporting evidence",
}


def _build_narrative_arc(paper_type: str, budget: dict) -> str:
    """Build the narrative arc text for the system prompt.

    Returns a numbered list like:
        1. Title slide (paper title — exception to assertion rule)
        2. Motivation (1-2 slides): State the problem ...
        ...
    """
    arc_spec = PAPER_TYPES[paper_type]["arc"]
    lines = []
    step = 1
    for section_name, count_key in arc_spec:
        desc = _ARC_DESCRIPTIONS.get(section_name, section_name.title())
        if isinstance(count_key, int):
            # Fixed count (title, conclusion, thankyou)
            lines.append(f"{step}. {desc}")
        else:
            # Variable count from budget
            n = budget.get(count_key.replace("budget_", ""), 1)
            lines.append(f"{step}. {desc} ({n} slide{'s' if n > 1 else ''})")
        step += 1
    return "\n".join(lines)


def _calculate_slide_count(minutes: int, paper: dict) -> dict:
    """Determine exact slide counts for this paper + time slot.

    Ensures at least 60% of experiments get dedicated slides.

    Returns:
        {
            "main_slides": int,
            "backup_slides": int,
            "content_density": int,
            "time_slot_minutes": int,
            "n_experiments": int,
        }
    """
    ranges = _get_slide_range(minutes)
    main_min, main_max = ranges["main"]
    backup_min, backup_max = ranges["backup"]

    density = _estimate_content_density(paper)
    exp_result = _count_experiments(paper)
    n_experiments = exp_result["count"]
    exp_confidence = exp_result["confidence"]

    # Map density to position within the range
    if density <= 5:
        main_target = main_min
    elif density >= 13:
        main_target = main_max
    else:
        frac = (density - 5) / 8.0
        main_target = int(main_min + frac * (main_max - main_min))

    # Ensure at least 60% of experiments can be covered.
    # When confidence is low, be conservative — require fewer dedicated
    # result slides so we don't over-allocate on a shaky estimate.
    coverage_pct = 0.4 + 0.2 * exp_confidence  # 0.4 at low conf → 0.6 at high
    fixed = 3  # title + conclusion + thankyou
    non_results = 3  # motivation(1) + method(1) + analysis(1) minimum
    min_results_slides = max(2, int(n_experiments * coverage_pct))
    min_main_needed = fixed + non_results + min_results_slides
    main_target = max(main_target, min(min_main_needed, main_max))

    # Backup slides: cover uncovered experiments + standard categories
    uncovered = max(0, n_experiments - min_results_slides)
    backup_target = max(backup_min, min(backup_max, uncovered + 3))

    log.info("Slide count: %d-min talk, density=%d, experiments=%d (conf=%.2f) "
             "→ %d main + %d backup",
             minutes, density, n_experiments, exp_confidence,
             main_target, backup_target)

    return {
        "main_slides": main_target,
        "backup_slides": backup_target,
        "content_density": density,
        "time_slot_minutes": minutes,
        "n_experiments": n_experiments,
        "experiment_confidence": exp_confidence,
    }


def _allocate_slide_budget(main_slides: int, paper: dict) -> dict:
    """Decide how many slides each section gets using results-first expansion.

    Returns dict with keys: motivation, method, results, analysis.
    Title, conclusion, and thankyou are always 1 each (fixed=3).

    Core principle: when the time slot increases, extra slides go to
    results/experiments FIRST. Non-results categories only grow after
    experiments are adequately covered.
    """
    fixed = 3  # title + conclusion + thankyou
    available = max(1, main_slides - fixed)
    exp_result = _count_experiments(paper)
    n_experiments = exp_result["count"]
    exp_confidence = exp_result["confidence"]

    # --- STEP 1: Set minimums for non-results ---
    motivation_slides = 1
    method_slides = 1
    analysis_slides = 1
    non_results_min = motivation_slides + method_slides + analysis_slides  # 3

    # --- STEP 2: Give everything else to results ---
    results_slides = max(2, available - non_results_min)

    # Cap results at number of experiments.  When confidence is low the
    # count may be inflated, so use a softer cap to avoid over-allocating.
    cap = n_experiments if exp_confidence >= 0.5 else max(3, int(n_experiments * 0.75))
    results_slides = min(results_slides, cap)

    # --- STEP 3: Distribute leftover to non-results via round-robin ---
    used = results_slides + non_results_min
    leftover = available - used

    # Round-robin order: method, analysis, motivation
    # Method benefits most from extra slides, motivation least
    categories = ["method", "analysis", "motivation"]
    caps = {"method": 4, "analysis": 3, "motivation": 3}
    counts = {"method": method_slides, "analysis": analysis_slides,
              "motivation": motivation_slides}

    while leftover > 0:
        added_any = False
        for cat in categories:
            if leftover <= 0:
                break
            if counts[cat] < caps[cat]:
                counts[cat] += 1
                leftover -= 1
                added_any = True
        if not added_any:
            # All capped — give remaining to results
            results_slides += leftover
            leftover = 0

    budget = {
        "motivation": counts["motivation"],
        "method": counts["method"],
        "results": results_slides,
        "analysis": counts["analysis"],
    }
    log.info("Slide budget: %s (available=%d, n_experiments=%d)",
             budget, available, n_experiments)
    return budget

# =============================================================
# PROMPT 1: ASSERTION-EVIDENCE PLAN (v5)
# =============================================================

SYSTEM_PROMPT_PLAN = """\
You are an expert academic presentation coach who uses the ASSERTION-EVIDENCE \
framework. In this framework, every slide title is a full sentence making a \
specific, testable claim, and the slide body provides visual evidence \
supporting that claim.

## YOUR TASK
Given a research paper, create a detailed slide-by-slide plan for a \
{talk_length}-minute {talk_format} presentation using assertion-evidence design.

## DYNAMIC SLIDE COUNT (calculated from paper content)

Generate EXACTLY {main_slides} content slides (including title and thank-you).

Slide budget allocation:
  - Title: 1 slide
  - Motivation/background: {budget_motivation} slide(s)
  - Method/setup: {budget_method} slide(s)
  - Results/experiments: {budget_results} slide(s)
  - Analysis/discussion: {budget_analysis} slide(s)
  - Conclusion: 1 slide
  - Thank you: 1 slide

Paper content summary:
  - {n_sections} sections, {n_figures} figures, {n_pages} pages
  - Content density score: {content_density}
  - Distinct experiment/result sections: {n_experiments}

### Experiment selection: breadth first, then depth

You have {budget_results} slides for {n_experiments} experiments/case studies.

STEP 1: COVER ALL EXPERIMENTS FIRST (breadth)
  If results_slides >= n_experiments:
    → Every experiment gets its own slide. Done.
  If results_slides < n_experiments:
    → Group the least critical experiments into shared slides (max 2-3 \
per slide). Every experiment must appear on at least one slide.
    → Never drop an experiment from the main presentation entirely.
    → Grouping criteria: combine experiments that demonstrate the same \
type of finding (e.g., two variants of the same vulnerability, \
two ablations of the same component).

STEP 2: ASSIGN REMAINING SLIDES TO DEPTH (only if all experiments covered)
  If results_slides > n_experiments:
    → The surplus slides go to the 2-3 most important experiments, \
giving them additional detail slides.
    → Importance criteria: central to the paper's thesis, most \
surprising finding, or most complex to explain.

STEP 3: NEVER give one experiment multiple slides before all experiments \
have at least one slide.

EXAMPLES:
  7 experiments, 5 result slides:
    Slide 1: Experiment A (most important — own slide)
    Slide 2: Experiment B (own slide)
    Slide 3: Experiments C + D (grouped — same category)
    Slide 4: Experiments E + F (grouped — same category)
    Slide 5: Experiment G (own slide)
    → 7/7 covered

  7 experiments, 10 result slides:
    Slides 1-7: Each experiment gets one slide (7/7 covered)
    Slides 8-10: Deeper dives into the 3 most important experiments
    → 7/7 covered + 3 deep dives

  11 experiments, 9 result slides:
    Slides 1-7: Seven most important experiments (own slides)
    Slide 8: Experiments 8 + 9 (grouped)
    Slide 9: Experiments 10 + 11 (grouped)
    → 11/11 covered

  11 experiments, 12 result slides:
    Slides 1-11: Each experiment gets one slide (11/11 covered)
    Slide 12: Deeper dive into most important experiment
    → 11/11 covered + 1 deep dive

ANTI-PATTERN (what we are fixing — NEVER do this):
    11 experiments, 12 result slides:
    Slides 1-3: Experiment A (three slides!)
    Slides 4-8: Experiments B, C, D, E, F
    Slides 9-10: Experiments G, H
    → Only 8/11 covered (3 dropped, 1 got 3 slides) — WRONG

### Grouping guidelines for shared slides

When combining 2-3 experiments on one slide:
  - Title should name the shared finding, not list experiment numbers
  - Use a comparison table showing each experiment as a row
  - Preferred layout: hero_table with experiments as rows, \
columns for setup/finding/impact

  BAD grouping slide:
    Title: "Case Studies #4 and #5"
    Body: bullets about each separately

  GOOD grouping slide:
    Title: "Resource exhaustion occurs across multiple task types"
    Body: table with columns [Trigger | Duration | Impact | Recovery]
    Each row = one experiment

### No duplicate experiment slides in main presentation

Each experiment should appear on exactly ONE main slide (either as \
a standalone or grouped with 1-2 others). Do not create multiple \
slides covering the same experiment in the main presentation.

If an experiment needs deeper treatment:
  - At 15-20 min: keep it to one slide, put extended analysis in backup
  - At 30+ min: one main slide + one follow-up slide (maximum 2)
  - At 45+ min: up to 3 slides for the most complex experiments

The deep-dive slides come AFTER all experiments have been covered once.

### CRITICAL RULES FOR NON-RESULTS SLIDES
- Motivation: {budget_motivation} slide(s). Problem statement + why it matters.
- Method: {budget_method} slide(s). Architecture/approach with figure_reference.
- Analysis: {budget_analysis} slide(s). Ablation, visualization, or generalization.
- These categories only grow AFTER experiments are adequately covered.
- NEVER use more non-results slides than allocated. The budget is binding.

### LAYOUT TARGETS
- Results slides: 100% must be hero_table or hero_figure (MANDATORY)
- Method slides: prefer hero_figure with architecture diagram
- Overall: ≥60% visual slides (hero_figure, hero_table, key_number, equation)
- Bullet-only slides: maximum 25% of total (ideally ≤2 slides)

{experiment_guidance}

### Backup slide rule
Experiments that don't fit in main slides become backup slides. \
But with breadth-first logic, this should only happen when \
results_slides < n_experiments AND grouping can't fit them all. \
With {budget_results} slides for {n_experiments} experiments, \
{backup_expectation}.

## THE ASSERTION-EVIDENCE FRAMEWORK

### Rule 1: Assertion Titles (MOST IMPORTANT)
Every slide title MUST be a COMPLETE SENTENCE that makes a specific, testable \
claim about the paper's contribution.
- Length: 8-15 words
- Must be a grammatically complete sentence with subject and verb
- Must make a claim the audience can evaluate
- NOT a topic label, NOT a question, NOT a fragment

Examples:
  BAD:  "Transformer Architecture"  (topic label — NO)
  BAD:  "Results and Analysis"  (topic label — NO)
  BAD:  "How does attention work?"  (question — NO)
  GOOD: "Self-attention replaces recurrence with parallel pairwise comparisons"
  GOOD: "The Transformer achieves 28.4 BLEU, exceeding all prior single models"
  GOOD: "Multi-head attention captures diverse syntactic and semantic patterns"

The ONLY exceptions: slide 1 (paper title) and the last slide (thank-you).

### Rule 2: Layout Selection (MANDATORY DECISION HIERARCHY)
Every slide (except title and thankyou) MUST specify a "layout" field.
Choose the layout by following this decision tree IN ORDER — use the \
FIRST rule that matches. Do NOT skip ahead to "bullets".

STEP 1 → Does the slide reference an available figure from the paper?
  YES → layout: "hero_figure". Full stop. Do NOT convert it to bullets.

STEP 2 → Does the slide present numerical comparisons across 3+ \
conditions, models, or experiments?
  YES → layout: "hero_table" with table_data (≥3 data rows). \
Never summarize a table as bullet points.

STEP 3 → Does the slide highlight ONE dramatic headline metric \
(the paper's single most memorable number)?
  YES → layout: "key_number". Exactly one per presentation.

STEP 4 → Does the slide explain a mathematical formulation?
  YES → layout: "equation" with equation_latex and annotations.

STEP 5 → ONLY if none of the above apply → layout: "bullets".

NEGATIVE RULES (NON-NEGOTIABLE):
- NEVER use "bullets" when a relevant figure exists in AVAILABLE FIGURES. \
Use "hero_figure" instead.
- NEVER use "bullets" to describe numerical results that could be a table. \
Use "hero_table" instead — even a 3-row comparison table is better than \
bullets listing numbers.
- NEVER use "bullets" for a slide that has a figure_reference field. \
If figure_reference is set, layout MUST be "hero_figure".
- NEVER use "bullets" for a slide whose content includes ≥3 numerical \
comparisons (accuracy, BLEU, F1, speedup, etc.). Use "hero_table".
- Maximum 2 "bullets" slides in the ENTIRE presentation. If you already \
have 2, every remaining slide MUST use a visual layout.

Layout definitions:
- "hero_figure": Large centered figure (60%+ of slide) with 2-4 annotations
- "hero_table": Full-width table with table_headline and context_line
- "key_number": One large number with context (for the headline metric)
- "equation": Centered equation with annotations explaining each term
- "bullets": Text-only fallback (max 2 per talk, only when no visual applies)

A good conference talk has: 4-5 hero_figure/hero_table, \
1-2 key_number, 1-2 equation, and only 1-2 bullets slides.

### Rule 3: Annotations Replace Bullets on Visual Slides
For hero_figure, hero_table, equation, and key_number layouts, use \
"annotations" instead of "bullet_points". Annotations are:
- Short technical labels (5-10 words each)
- 2-4 per slide maximum
- No bullet symbols — clean text phrases only
- They describe what the audience should notice in the visual

Examples for a figure slide:
  ["Encoder stack processes input in parallel",
   "Decoder attends to all encoder positions",
   "Residual connections around each sub-layer"]
Examples for an equation:
  ["Q, K, V = queries, keys, values",
   "Scaling factor prevents gradient vanishing",
   "Softmax produces attention weights"]

Use "bullet_points" ONLY when layout is "bullets".

### Rule 4: References on Evidence Slides
Slides showing specific results or claims should include "references":
- Format: ["Author et al., Year", "Table 3 in paper"]
- Rendered at 10pt in the bottom-left corner
- 1-2 references per evidence slide
- Cite the paper's own table/figure numbers for traceability

### Rule 5: Context Lines for Tables and Figures
Tables and figures should include a "context_line":
- A single sentence (15-25 words) describing the experimental setup
- Appears below the visual in small italic text
- Example: "Trained on WMT 2014 EN-DE (4.5M pairs) with 8x P100 GPUs for 3.5 days"
- This answers the audience's immediate "what dataset/setup?" question

### Rule 6: Key Number Slides
Use layout "key_number" for the paper's most impressive single metric:
- "key_number": The number itself, e.g. "28.4 BLEU"
- "key_number_context": What it means, e.g. "+2.0 over previous state-of-the-art"
- Use this for the headline result the audience should remember
- Pair with 2-3 annotations giving context

### Rule 7: Table Headlines
For hero_table slides, include "table_headline":
- One sentence summarizing the table's key finding
- Rendered prominently above the table
- Example: "Single-model Transformer outperforms all prior ensembles"

### Rule 8: NEVER pad the opening
Maximum 2 slides before showing the architecture/method. Audiences lose \
interest after 2 minutes of text-only motivation. Merge "problem + insight" \
into fewer slides.

### Rule 9: NEVER repeat content across slides
Each slide must add NEW information. If a table shows training cost, the \
next slide must NOT restate it. Merge overlapping slides.

### Rule 10: Tables are MANDATORY for numerical comparisons
NEVER convert comparison tables into bullets or annotations. Numbers go in \
table_data, always.

### Rule 11: Include the paper's most MEMORABLE visual
Every paper has one "wow" figure — find it and include it. It often comes \
from the appendix, not the main body.

### Rule 12: Theoretical justification comes BEFORE results
Show complexity comparison or theoretical advantage BEFORE experimental results. \
Structure: Problem → Method → Why It Should Work → Proof It Does Work.

### Rule 13: Figure references must include page location
Format: "Figure 3 (page 13)". The PDF parser uses page position to find images.

### Rule 14: Results table must include the strongest baseline
Include the previous SOTA and 3-4 strongest baselines. The table should tell \
a story: "here's what existed → here's how much better we are."

### Rule 15: Visualization slides need context annotations
Any hero_figure showing qualitative results (attention heatmaps, t-SNE, etc.) \
MUST have 2-3 annotations explaining what the audience is looking at.

### Rule 16: Method slides must mention component compensation
If the paper removes a standard component (recurrence, convolution), the \
method slide MUST mention how the model compensates (positional encoding, etc.).

### Rule 17: Key Takeaways must be PROVEN claims only
The conclusion slide must only contain claims supported by experiments. \
Never include speculation like "Future: apply to X."

### Rule 18: Training efficiency needs comparative context
Always compare training time/cost against a specific baseline. Never present \
"12 hours" without context.

### Rule 19: Backup visualization slides MUST include figures
Use content_type "backup_visualization" with figure_reference and page number. \
The whole point is to SHOW the figure during Q&A, not describe it.

### Rule 20: Title slide rules
- Paper title appears ONCE (never repeated)
- Below title: one-line hook summarizing the core contribution
- Below hook: author names, then venue and year
- Four lines maximum

### Rule 21: Evidence-conclusion consistency
Before writing the conclusion slide, review every slide in the plan. \
For each conclusion bullet, verify that at least one earlier slide provides \
the EVIDENCE (a table, figure, or specific data point) that supports that claim. \
If a conclusion bullet references a result with no corresponding evidence slide, \
either add an evidence slide for it or remove the claim. NEVER claim something \
in the conclusion that wasn't shown with data in the presentation.

When the paper has secondary experiments (additional tasks, datasets, transfer \
learning), include at least the MOST IMPORTANT one as a main slide. Place it \
after ablation/analysis and before conclusion.

### Rule 22: Evidence slides beat explanation slides
When you have more content than slide slots, prioritize:
MUST INCLUDE (always gets its own slide):
  - Architecture/method overview (with figure)
  - Main results comparison table
  - Most important secondary/generalization result

INCLUDE IF ROOM:
  - Key mechanism detail (equation + figure)
  - Ablation/analysis (table)
  - Qualitative visualization

MOVE TO BACKUP OR FOLD INTO ANNOTATIONS:
  - Mathematical derivations beyond the key equation
  - Detailed mechanism explanations (positional encoding details, etc.)
  - Training schedule, optimizer, normalization details

General principle: if it has DATA (numbers, tables, figures), it deserves a \
slide. If it's an EXPLANATION (how something works), compress it to an \
annotation on a nearby slide or move to backup.

### Rule 23: Key number completeness
If the paper achieves state-of-the-art on multiple benchmarks or tasks, \
the key_number_context must mention ALL of them. The key_number itself is \
the single most impressive number. The context provides: what benchmark, \
how much improvement over previous best (specific comparison), any additional \
SOTA results on other benchmarks, and training cost for efficiency context.

### Rule 24: Conclusion specificity
Every conclusion bullet must contain at least one specific, verifiable claim — \
a number, a comparison, a benchmark name, or a concrete capability.
BAD: "Achieves state-of-the-art results"
BAD: "Trains faster than previous models"
GOOD: "Achieves 28.4 BLEU on WMT EN-DE, +2.0 over previous best ensemble"
GOOD: "Trains 10-20x faster than comparable models on similar hardware"
The conclusion should be an executive summary of EVIDENCE shown, not a pitch.

### Rule 25: Equation slides should pair with mechanism figures
When the paper has a key equation AND a related mechanism diagram (architecture, \
workflow, or process figure), include BOTH on the equation slide via \
figure_reference. The equation goes left; the diagram goes right. This helps \
the audience see WHERE the equation fits in the system. If no related figure \
exists, equation-only is fine.

### Rule 26: Tables MUST include highlight_terms (NON-NEGOTIABLE)
EVERY table_data object MUST have a non-empty "highlight_terms" array \
with 1-3 strings. These are the paper's own method/model names that should \
be visually emphasized (bold + highlighted row). A table with an empty \
highlight_terms array is INVALID. \
Example: "highlight_terms": ["Transformer (base)", "Transformer (big)"] \
If unsure, use the paper's primary model name.

### Rule 27: Target 80% of talk time (NON-NEGOTIABLE)
BEFORE outputting the final JSON, sum all speaking_time_seconds. The total \
MUST be UNDER 80% of the talk length ({talk_length} minutes = {timing_budget} \
seconds max). If your total exceeds this, reduce speaking_time_seconds on \
motivation and text-heavy slides until it fits. This is CRITICAL for Q&A buffer.

### Rule 28: Minimum table size (NON-NEGOTIABLE)
Every table_data object must have at least 3 data rows (plus header). \
If you cannot fill 3 rows, DO NOT use a table — use annotations or \
bullet points instead.

Table sizing guidelines based on slide type:

  HERO_TABLE (main result/comparison):
    - Minimum: 3 data rows + header = 4 rows total
    - Target: 4-6 data rows + header
    - Maximum: 8 data rows + header
    - Show multiple examples, conditions, or comparisons

  HERO_TABLE (case study/incident):
    - Show the PROGRESSION of events, not a single snapshot
    - Each row = one step in the sequence (e.g., trigger → response → \
escalation → outcome)
    - Or each row = one instance of the pattern across different agents
    - Minimum 3 rows showing distinct data points

  Backup tables:
    - Can be larger: 8-15 rows showing full data

If a case study has a sequence of events (attack → response → \
escalation → compromise), show ALL steps as separate rows, not just \
the final outcome. The progression IS the finding.

If a vulnerability was tested across multiple agents, show each \
agent as a row with its specific behavior, not a single summary row.

BAD (1 data row — this is not a table):
  | Attack | Response | Outcome |
  | Spoofed name | Full access granted | Complete compromise |

GOOD (4 data rows — shows the full attack sequence):
  | Step | Attacker Action | Agent Response | Access Level |
  | 1. Reconnaissance | Checked owner display name | Public info | None |
  | 2. Name change | Set display name to owner | Not detected | None |
  | 3. First command | Requested file listing | Executed without verification | Read |
  | 4. Escalation | Requested API keys | Provided credentials | Full |

GOOD (3 data rows — shows pattern across agents):
  | Agent | Attack Vector | Verification Check | Outcome |
  | Agent A | Username spoof | Display name only | Full access |
  | Agent B | Username spoof | Display name only | Full access |
  | Agent C | Email header spoof | Checked sender address | Refused |

### Rule 29: Key number slide is MANDATORY
Every presentation MUST include exactly ONE key_number slide. This \
slide shows one dramatic number that captures the paper's core finding.

How to choose the key number:
  1. Look for the paper's most surprising QUANTITATIVE finding
  2. It should be a number that makes the audience react
  3. If the paper has multiple headline numbers, pick the most impactful

Examples of good key numbers:
  - "11 case studies" (for a paper documenting vulnerabilities)
  - "28.4 BLEU" (for a paper setting a new benchmark record)
  - "92.7% F1" (for a strong result on a standard task)
  - "3.5 days" (for a paper about training efficiency)
  - "100% success rate" (for an attack paper)
  - "47× faster" (for a speedup paper)

The key_number slide should appear AFTER the results section and \
BEFORE the conclusion. It serves as the dramatic climax — the single \
number the audience should remember.

Format:
  - layout: "key_number"
  - key_number: the number itself (displayed at 60-72pt)
  - key_number_context: 2-3 lines explaining what the number means, \
what it's compared to, and why it matters
  - Include the benchmark/dataset name and improvement over baseline

If the paper is qualitative (no single headline number), use the \
COUNT of findings as the key number (e.g., "11 documented failures", \
"7 design principles", "5 threat categories").

Every paper has a key number. Find it.

### Rule 30: Every hero_table slide needs supporting annotations below the table
A table alone doesn't tell the story. After the table, include 2-3 \
takeaway annotations that explain what the audience should notice.

Every hero_table slide must have these fields:
  1. table_headline: one sentence above the table (what pattern to see)
  2. table_data: the table itself (3+ data rows)
  3. context_line: what the table shows (dataset, conditions)
  4. annotations: list of 2-3 key takeaways from the table data
     - Each annotation is one sentence (max 15 words)
     - They explain the IMPLICATION, not just restate the data
     - Example: "No agent performed any authorization check before executing"
     - Example: "Attack succeeded in under 5 minutes with zero technical skill"
     - Example: "All three agents exhibited identical vulnerability pattern"

The annotations fill the space below the table and context lines. \
Without them, the bottom third of the slide is blank — this looks \
unfinished and wastes the audience's visual attention.

### Rule 31: Every hero_figure slide needs 3-4 annotations (NON-NEGOTIABLE)
A figure without annotations is like a chart without a legend — the \
audience doesn't know what to look at.

Every hero_figure slide must have:
  1. figure_reference: which figure from the paper (with page number)
  2. annotations: list of 3-4 key callouts explaining the figure
     - Each annotation is one concise observation (max 15 words)
     - Point out specific parts of the figure
     - Example: "Each node has dedicated compute, storage, and network access"
     - Example: "Persistent state enables long-term behavior across sessions"
     - Example: "Scheduler triggers autonomous recurring actions periodically"
     - Example: "Communication channels connect all components bidirectionally"
  3. context_line: experimental setup or what the figure depicts

A hero_figure slide with an empty annotations array is INVALID. \
The builder places annotations next to or below the figure. Without \
them, the slide is just a picture with no explanation.

## NARRATIVE ARC

This paper has been classified as: **{paper_type_label}**.

Structure your {main_slides} slides following this pattern:

{narrative_arc}

Average ~{seconds_per_slide} seconds per slide. Visual target: 60%+ slides \
with figures, tables, equations, or key numbers.

## OUTPUT FORMAT

Return ONLY valid JSON. No markdown fences. No explanation.
IMPORTANT: OMIT fields that are null — do NOT include them. This saves tokens.
Keep table rows concise. Keep annotations SHORT (5-10 words).

{{
  "talk_title": "string",
  "talk_subtitle": "string (one-line hook — MUST differ from title)",
  "authors": "string",
  "venue": "string",
  "talk_length_minutes": number,
  "slides": [
    {{
      "slide_number": 1,
      "title": "full sentence assertion 8-15 words",
      "content_type": "title|motivation|method|...|thankyou",
      "layout": "hero_figure|hero_table|key_number|equation|bullets",
      "bullet_points": ["only if layout=bullets"],
      "annotations": ["5-10 word labels, only for visual layouts"],
      "figure_reference": "Figure N (page P)",
      "table_data": {{"headers": [...], "rows": [{{"cells": [...], "bold": false}}], "caption": "...", "highlight_terms": ["method name"]}},
      "table_headline": "key finding sentence",
      "context_line": "experimental setup",
      "references": ["Author et al., Year"],
      "equation_latex": "LaTeX string",
      "key_number": "28.4 BLEU",
      "key_number_context": "+2.0 over previous SOTA",
      "speaking_time_seconds": number,
      "source_section": "Section name"
    }}
  ],
  "visual_checklist": {{
    "figures_used": ["Figure 1"],
    "tables_used": ["Table 1"],
    "figures_available_but_skipped": [],
    "tables_available_but_skipped": [],
    "total_visual_slides": 8,
    "total_slides": 12,
    "visual_ratio": "67%"
  }},
  "coverage_checklist": {{
    "total_experiments": number,
    "experiments_in_main": [list of experiment/case-study identifiers covered in main slides],
    "experiments_in_backup": [list covered only in backup slides],
    "missing": []
  }}
}}

The visual_checklist forces you to account for every figure and table. \
If visual_ratio is below 50%, go back and add more visual evidence.

### Rule 32a: Figure-slide RELEVANCE is mandatory (NON-NEGOTIABLE)
Every figure_reference MUST be semantically related to the slide's content:
- A slide about "Method X outperforms baselines" → use a results/comparison figure
- A slide about "Architecture overview" → use the architecture diagram figure
- A slide about "Training procedure" → use a training curve or pipeline figure
- NEVER place a random or unrelated figure just to have a visual

Before assigning a figure, CHECK its caption from the AVAILABLE FIGURES list. \
The caption tells you what the figure shows. Only assign it to a slide \
whose title/content matches what the figure depicts.

If no available figure is relevant to a slide's topic, use a different \
layout (hero_table, key_number, equation, or bullets) instead of forcing \
an unrelated figure.

### Rule 32: ABSOLUTE REQUIREMENT — zero missing experiments (NON-NEGOTIABLE)
After generating your slide plan, perform this self-check:

1. List EVERY experiment, case study, or major finding in the paper
2. For each one, verify it appears on at least one main slide OR \
is explicitly assigned to a backup slide
3. If ANY experiment is missing from both main AND backup, you MUST \
add it — either by:
   a) Grouping it with a related experiment on an existing slide \
(preferred — max 2-3 per slide)
   b) Adding an extra backup slide for overflow experiments
   c) Mentioning overflow experiments in the conclusion bullets

NEVER submit a plan where missing > 0. If your first draft has \
missing experiments, revise the plan before outputting JSON.

Specifically: count the total experiments in the paper. Then count \
how many appear in your experiments_in_main + experiments_in_backup \
lists. If the numbers don't match, you have a bug in your plan — \
fix it before outputting.

The "missing" list in coverage_checklist MUST be empty. A plan with \
missing experiments is INVALID and will be rejected.

### Rule 33: Alternate layouts to avoid visual monotony (NON-NEGOTIABLE)
NEVER place more than 3 consecutive slides with the same layout type. \
If you have 4+ case studies/results, alternate between:
  - HERO_TABLE: for findings best shown as data comparisons, \
multi-step sequences, or cross-condition patterns
  - HERO_FIGURE: for findings best shown as conversation screenshots, \
propagation diagrams, or escalation visualizations

How to decide TABLE vs FIGURE for a case study:

  Use HERO_TABLE when:
    - The finding is a comparison across agents or conditions
    - The data has clear columns (trigger → response → outcome)
    - Numbers, success rates, or durations are the key evidence

  Use HERO_FIGURE when:
    - The paper has a figure illustrating THIS specific finding
    - The finding is a process/sequence (escalation, propagation)
    - The case study involves spatial/network relationships
    - A conversation excerpt or visual pattern IS the evidence

When you have a sequence of 5+ result slides, enforce this pattern:
  Slide N:   HERO_TABLE  (data comparison)
  Slide N+1: HERO_FIGURE (visual evidence) ← alternate
  Slide N+2: HERO_TABLE  (data comparison)
  Slide N+3: HERO_FIGURE (visual evidence) ← alternate
  Slide N+4: HERO_TABLE  (data comparison)

If no paper figure exists for a case study, HERO_TABLE is fine. \
But if a figure IS available and relevant, prefer it over a table.

### Rule 34: Prioritize the paper's most dramatic figures for main slides
When selecting which case studies get HERO_FIGURE layout, prefer:
  1. Figures showing ESCALATION patterns (social pressure, \
concession sequences) — these are the "wow moments"
  2. Figures showing PROPAGATION (how failures spread between \
entities) — these tell a story visually
  3. Figures showing CONVERSATION EXCERPTS or real interactions — \
more compelling than summaries in tables
  4. Architecture/system diagrams — already used for method slides

A figure that shows a real interaction or attack sequence should \
ALWAYS be in a main slide, not buried in backup. These are what the \
audience photographs and remembers.

### Rule 35: Figure slides in backup should be rare
If you assign a figure to a backup slide, ask yourself: is there a \
table slide in the main deck that would be LESS impactful than this \
figure? If yes, swap them. Dramatic figures belong in main slides; \
supplementary tables belong in backup.

Before outputting, check the AVAILABLE FIGURES list in the paper data. \
If more than half the available figures are unused in main slides, \
revisit your layout choices — you are likely over-using tables.
"""

# =============================================================
# PROMPT 2: SPEAKER NOTES (v5 — assertion-evidence)
# =============================================================

SYSTEM_PROMPT_NOTES = """\
You are coaching a researcher for an assertion-evidence conference talk. \
Each slide title is a full-sentence claim. Write speaker notes that support \
the claim with the evidence on the slide.

## ABSOLUTE RULES

1. **60-80 words per slide.** Cue notes, not a script.

2. **Start with the claim.** Restate the title assertion naturally.
   GOOD: "The key insight here is that self-attention replaces recurrence entirely..."
   BAD:  "This slide presents the method..."

3. **End with [Transition] sentence** connecting to the next slide's assertion.

4. **For TABLE slides:** Name specific cells. "Point to the bottom row — \
28.4 versus 26.3 above it."

5. **For FIGURE slides:** Tell the presenter what to physically point at. \
"Start with the left half — that's the encoder."

6. **For KEY NUMBER slides:** Build up to the number. "And the result? \
[pause] 28.4 BLEU."

7. **NEVER reference a visual that isn't on the slide.** Check has_figure, \
has_table, has_equation, and has_key_number flags.

8. **[pause] markers are MANDATORY on EVERY slide (NON-NEGOTIABLE).** \
Include exactly 1-2 [pause] markers in each slide's speaker_notes. \
Place [pause] after key numbers, surprising claims, comparisons, or \
before transitions. Examples:
- "And the result? [pause] 28.4 BLEU."
- "That's a 2-point improvement over the previous best. [pause]"
- "Notice the encoder stack on the left. [pause] Each layer has two sub-layers."
A slide with ZERO [pause] markers is INVALID. Check every slide before returning.

9. **Include timing:** "[~90 seconds]" at the start.

Return ONLY valid JSON array. No markdown.
[{{"slide_number": 1, "speaker_notes": "...", "transition": "...", "timing_cue": "~30 seconds"}}]
"""

# =============================================================
# PROMPT 3: BACKUP / Q&A SLIDES (v5 — assertion-evidence)
# =============================================================

SYSTEM_PROMPT_BACKUP = """\
Generate {backup_slides} backup slides for Q&A after an assertion-evidence talk.

## RULES

1. Each backup slide title MUST be a full-sentence assertion (8-15 words).
2. Include "layout" field: "hero_table", "hero_figure", or "bullets".
3. Use "annotations" (not bullet_points) on visual layouts.
4. OMIT null fields to save tokens.

## REQUIRED CATEGORIES (generate one slide per category that applies):

a) FULL RESULTS TABLE: The complete version of any table simplified in the \
main slides — all rows, all columns, all baselines. \
layout: "hero_table", content_type: "backup_full_table". \
table_data REQUIRED.

b) SECONDARY EXPERIMENTS: Results on additional tasks, datasets, or modalities \
not shown in the main slides. If the paper has them, include the most important. \
layout: "hero_table" (if data) or "hero_figure" (if qualitative). \
content_type: "backup_extra_experiment".

c) ADDITIONAL FIGURES: Figures from the paper NOT used in main slides. \
Only include figures NOT already in the main deck. \
layout: "hero_figure" with figure_reference including page number. \
content_type: "backup_visualization".

d) LIMITATIONS & FUTURE WORK: 5-6 specific limitations from the paper. \
Title should state the paper's primary limitation as an assertion. \
layout: "bullets", content_type: "backup_limitations". \
bullet_points REQUIRED.

e) IMPLEMENTATION & REPRODUCIBILITY: Hardware, training time, optimizer, \
data sizes, code availability — 6-8 specific details a researcher needs. \
layout: "bullets", content_type: "backup_reproducibility". \
bullet_points REQUIRED.

## OUTPUT FORMAT
Same JSON schema as main slides.
Return JSON as {{"slides": [...]}}. NO markdown. NO explanation. ONLY valid JSON.
"""


# =============================================================
# FIGURE–SLIDE RELEVANCE SCORING
# =============================================================

def _extract_keywords(text: str) -> dict[str, int]:
    """Extract keyword counts from text, filtering stopwords and short tokens."""
    words = re.findall(r"[a-z][a-z0-9]{2,}", text.lower())
    counts: dict[str, int] = {}
    for w in words:
        if w not in _STOPWORDS:
            counts[w] = counts.get(w, 0) + 1
    return counts


def _map_figures_to_sections(paper: dict) -> dict[int, str]:
    """Map figure numbers to the section that discusses them.

    Scans each section's text for "Figure N" / "Fig. N" references.
    Returns {figure_number: section_name} for figures with a clear home.
    """
    sections = paper.get("sections", [])
    figures = paper.get("figures", [])
    mapping: dict[int, str] = {}

    for fig in figures:
        fig_num = fig.get("figure_number")
        if not fig_num:
            continue

        pattern = rf"(?:Figure|Fig\.?)\s*{fig_num}\b"
        best_section = None
        best_count = 0
        for sec in sections:
            mentions = len(re.findall(pattern, sec.get("text", ""), re.IGNORECASE))
            if mentions > best_count:
                best_count = mentions
                best_section = sec["name"]

        if best_section:
            mapping[fig_num] = best_section

    return mapping


def _score_figure_slide_relevance(
    fig_caption: str,
    fig_section: str,
    slide_title: str,
    slide_annotations: list[str],
    slide_source_section: str,
) -> float:
    """Score how relevant a figure is to a slide (0.0–1.0).

    Uses keyword overlap between figure context (caption + section name)
    and slide context (title + annotations + source section).
    """
    fig_text = f"{fig_caption} {fig_section}"
    fig_kw = _extract_keywords(fig_text)

    slide_text = f"{slide_title} {' '.join(slide_annotations)} {slide_source_section}"
    slide_kw = _extract_keywords(slide_text)

    if not fig_kw or not slide_kw:
        return 0.0

    shared = set(fig_kw) & set(slide_kw)
    if not shared:
        return 0.0

    overlap = sum(min(fig_kw[k], slide_kw[k]) for k in shared)
    max_possible = min(sum(fig_kw.values()), sum(slide_kw.values()))
    return overlap / max_possible if max_possible > 0 else 0.0


def _validate_figure_assignments(slides: list[dict], paper: dict) -> None:
    """Validate and fix figure-to-slide assignments based on content relevance.

    Phase 1 — Verify existing assignments: if a slide's assigned figure scores
    below REASSIGN_THRESHOLD and a much better match exists, swap it.

    Phase 2 — Fill gaps: find slides without figures that strongly match an
    unused figure, and assign it (switching layout to hero_figure).

    Mutates slides in place.
    """
    figures = paper.get("figures", [])
    if not figures:
        return

    fig_to_section = _map_figures_to_sections(paper)

    # Build figure info lookup by number
    fig_info: dict[int, dict] = {}
    for fig in figures:
        num = fig.get("figure_number")
        if num:
            fig_info[num] = {
                "caption": fig.get("caption", ""),
                "section": fig_to_section.get(num, ""),
                "page": fig.get("page", "?"),
            }

    if not fig_info:
        return

    # Score every (figure, slide) pair
    scores: dict[tuple[int, int], float] = {}
    eligible_slide_idxs: list[int] = []
    for i, slide in enumerate(slides):
        if not isinstance(slide, dict):
            continue
        ct = slide.get("content_type", "")
        if ct in ("title", "thankyou"):
            continue
        eligible_slide_idxs.append(i)
        for fig_num, info in fig_info.items():
            scores[(fig_num, i)] = _score_figure_slide_relevance(
                fig_caption=info["caption"],
                fig_section=info["section"],
                slide_title=slide.get("title", ""),
                slide_annotations=slide.get("annotations", []),
                slide_source_section=slide.get("source_section", ""),
            )

    # --- Phase 1: validate existing assignments ---
    REASSIGN_THRESHOLD = 0.05
    used_figures: set[int] = set()

    for i, slide in enumerate(slides):
        if not isinstance(slide, dict):
            continue
        ref = slide.get("figure_reference")
        if not ref:
            continue

        m = re.search(r"Figure\s*(\d+)", str(ref), re.IGNORECASE)
        if not m:
            continue
        fig_num = int(m.group(1))
        if fig_num not in fig_info:
            continue

        current_score = scores.get((fig_num, i), 0.0)

        # Find the best-scoring unused figure for this slide
        best_fig = fig_num
        best_score = current_score
        for fn in fig_info:
            if fn in used_figures:
                continue
            s = scores.get((fn, i), 0.0)
            if s > best_score:
                best_fig = fn
                best_score = s

        # Reassign only when current score is weak and the alternative is
        # meaningfully stronger (at least 2× or +0.10 absolute).
        if (
            best_fig != fig_num
            and current_score < REASSIGN_THRESHOLD
            and (best_score > current_score * 2 or best_score - current_score > 0.10)
        ):
            page = fig_info[best_fig]["page"]
            old_ref = slide["figure_reference"]
            slide["figure_reference"] = f"Figure {best_fig} (page {page})"
            log.info(
                "Reassigned slide %s: %s → %s (score %.3f → %.3f)",
                slide.get("slide_number", i), old_ref,
                slide["figure_reference"], current_score, best_score,
            )
            used_figures.add(best_fig)
        else:
            used_figures.add(fig_num)

    # --- Phase 2: assign strong-match figures to slides that lack one ---
    AUTO_ASSIGN_THRESHOLD = 0.15
    for i in eligible_slide_idxs:
        slide = slides[i]
        if slide.get("figure_reference"):
            continue  # already has a figure
        layout = slide.get("layout", "")
        if layout in ("hero_table", "key_number"):
            continue  # table/number layouts don't take a hero figure

        best_fig = None
        best_score = AUTO_ASSIGN_THRESHOLD
        for fn in fig_info:
            if fn in used_figures:
                continue
            s = scores.get((fn, i), 0.0)
            if s > best_score:
                best_fig = fn
                best_score = s

        if best_fig is not None:
            page = fig_info[best_fig]["page"]
            slide["figure_reference"] = f"Figure {best_fig} (page {page})"
            if layout == "bullets":
                slide["layout"] = "hero_figure"
            log.info(
                "Auto-assigned Figure %d to slide %s (score %.3f)",
                best_fig, slide.get("slide_number", i), best_score,
            )
            used_figures.add(best_fig)


MAX_SUMMARY_CHARS = 15_000  # ~3.7K tokens — keeps API calls fast and reliable


def _build_paper_summary(paper: dict) -> str:
    """Format the parsed paper dict into a text block for the prompt.

    Truncates each section to stay within MAX_SUMMARY_CHARS total, ensuring
    the full paper fits comfortably in the context window without causing
    API connection timeouts on large papers.
    """
    parts = [
        f"TITLE: {paper['title']}",
        f"AUTHORS: {paper['authors']}",
    ]

    if paper.get("abstract"):
        parts.append(f"\nABSTRACT:\n{paper['abstract']}")

    for section in paper.get("sections", []):
        text = section["text"]
        if len(text) > 3000:
            text = text[:3000] + "\n[...truncated...]"
        parts.append(f"\n## {section['name']}\n{text}")

    figures = paper.get("figures", [])
    if figures:
        fig_section_map = _map_figures_to_sections(paper)
        parts.append(f"\nAVAILABLE FIGURES ({len(figures)} total):")
        parts.append(
            "IMPORTANT: Use these figures in main slides. Do NOT default to "
            "tables when a relevant figure exists. Alternate table/figure "
            "layouts for visual variety.\n"
            "Each figure lists the section that discusses it — assign the "
            "figure to a slide covering THAT section's topic."
        )
        for i, fig in enumerate(figures):
            caption = fig.get("caption") or "(no caption detected)"
            page = fig.get("page", "?")
            fig_num = fig.get("figure_number") or fig.get("figure_label") or (i + 1)
            section_hint = fig_section_map.get(
                fig.get("figure_number"), ""
            )
            section_line = f"  Discussed in: {section_hint}" if section_hint else ""
            parts.append(
                f"  - Figure {fig_num} (page {page}): {caption}\n"
                f"    → ONLY use on slides whose content matches this caption"
                f"{section_line}"
            )

    summary = "\n".join(parts)
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS] + "\n[...paper text truncated...]"
    return summary


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that Claude sometimes wraps around JSON."""
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.index("\n") if "\n" in text else len(text)
        text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[:-3]
    if text.startswith("json"):
        text = text[4:]
    return text.strip()


def _repair_truncated_json(text: str) -> str:
    """Attempt to repair JSON truncated by max_tokens.

    Strategy: find the last complete JSON object in a slides array,
    then close all open brackets/braces.
    """
    cleaned = _strip_code_fences(text)

    # Find the last complete "}" that could end a slide object
    # by looking for "}," or "}" followed by whitespace/newline
    last_complete = -1
    depth = 0
    in_string = False
    escape_next = False

    for i, c in enumerate(cleaned):
        if escape_next:
            escape_next = False
            continue
        if c == '\\':
            escape_next = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth >= 1:  # closed a nested object (like a slide)
                last_complete = i

    if last_complete > 0:
        # Truncate at last complete object, then close remaining brackets
        truncated = cleaned[:last_complete + 1]
        # Count remaining open brackets
        open_braces = 0
        open_brackets = 0
        in_str = False
        esc = False
        for c in truncated:
            if esc:
                esc = False
                continue
            if c == '\\':
                esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == '{':
                open_braces += 1
            elif c == '}':
                open_braces -= 1
            elif c == '[':
                open_brackets += 1
            elif c == ']':
                open_brackets -= 1

        closing = ']' * open_brackets + '}' * open_braces
        repaired = truncated + closing
        log.info("Repaired truncated JSON: kept %d/%d chars, added '%s'",
                 last_complete + 1, len(cleaned), closing)
        return repaired

    return text  # can't repair, return as-is


def _parse_json_response(text: str) -> dict | list:
    """Parse a JSON response from Claude, handling common quirks."""
    cleaned = _strip_code_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = cleaned.find(start_char)
            if start == -1:
                continue
            depth = 0
            for i, c in enumerate(cleaned[start:], start):
                if c == start_char:
                    depth += 1
                elif c == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start:i + 1])
                        except json.JSONDecodeError:
                            break
        raise ValueError(f"Could not parse JSON from response: {cleaned[:200]}...")


def _validate_layout_variety(slides: list[dict]) -> None:
    """Check for layout monotony and log warnings."""
    # Classify each slide's visual type
    max_run = 1
    current_run = 1
    prev_layout = None
    run_layout = None

    for s in slides:
        if not isinstance(s, dict):
            continue
        ct = s.get("content_type", "")
        if ct in ("title", "thankyou", "conclusion"):
            prev_layout = None  # reset run on structural slides
            current_run = 1
            continue

        has_table = bool(s.get("table_data"))
        has_figure = bool(s.get("figure_reference"))
        layout = "table" if has_table else ("figure" if has_figure else "text")

        if layout == prev_layout and layout in ("table", "figure"):
            current_run += 1
            if current_run > max_run:
                max_run = current_run
                run_layout = layout
        else:
            current_run = 1
        prev_layout = layout

    if max_run > 3:
        log.warning(
            "Layout monotony: %d consecutive %s slides. "
            "Consider alternating table/figure layouts.",
            max_run, run_layout,
        )

    # Check figure vs table ratio
    table_slides = sum(1 for s in slides if isinstance(s, dict) and s.get("table_data"))
    figure_slides = sum(
        1 for s in slides
        if isinstance(s, dict) and s.get("figure_reference") and not s.get("table_data")
    )
    total_visual = table_slides + figure_slides
    if total_visual > 4 and figure_slides / total_visual < 0.2:
        log.warning(
            "Low figure ratio: %d/%d visual slides use figures (%.0f%%). "
            "Papers with available figures should have ≥30%% figure slides.",
            figure_slides, total_visual, figure_slides / total_visual * 100,
        )


def _enforce_layout_hierarchy(slides: list[dict]) -> None:
    """Fix slides whose layout contradicts their own data.

    Applies the same decision hierarchy from the prompt as a hard
    post-processing pass so violations are corrected even when Claude
    ignores the rules.

    Mutates slides in place.
    """
    for s in slides:
        if not isinstance(s, dict):
            continue
        ct = s.get("content_type", "")
        if ct in ("title", "thankyou"):
            continue

        layout = s.get("layout", "bullets")
        has_figure = bool(s.get("figure_reference"))
        has_table = bool(s.get("table_data"))
        has_key_num = bool(s.get("key_number"))
        has_equation = bool(s.get("equation_latex"))

        # Count data rows to verify a real table
        table_rows = 0
        if has_table:
            td = s["table_data"]
            if isinstance(td, dict):
                table_rows = len(td.get("rows", []))

        new_layout = layout

        # Rule: figure_reference present → must be hero_figure
        if has_figure and layout != "hero_figure":
            # Don't override equation slides that use a side figure
            if layout != "equation":
                new_layout = "hero_figure"

        # Rule: table_data with 3+ rows → must be hero_table
        elif has_table and table_rows >= 3 and layout not in ("hero_table",):
            new_layout = "hero_table"

        # Rule: key_number present → must be key_number
        elif has_key_num and layout != "key_number":
            new_layout = "key_number"

        # Rule: equation_latex present → must be equation
        elif has_equation and layout != "equation":
            # Don't override hero_figure that also has an equation sidebar
            if not has_figure:
                new_layout = "equation"

        if new_layout != layout:
            log.info(
                "Layout fix slide %s: %s → %s (has figure=%s, table=%d rows, "
                "key_num=%s, equation=%s)",
                s.get("slide_number", "?"), layout, new_layout,
                has_figure, table_rows, has_key_num, has_equation,
            )
            s["layout"] = new_layout

            # Migrate bullet_points → annotations for visual layouts
            if new_layout != "bullets" and s.get("bullet_points") and not s.get("annotations"):
                s["annotations"] = s.pop("bullet_points")


def _call_claude(
    client: anthropic.Anthropic, system: str, user: str, max_tokens: int = 4096,
    model: str | None = None,
) -> dict | list:
    """Call Claude with retry logic and return parsed JSON."""
    last_error = None
    use_model = model or MODEL

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=use_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = response.content[0].text
            if response.stop_reason == "max_tokens":
                log.warning("Response truncated (max_tokens=%d, model=%s). "
                            "Attempting to repair JSON.", max_tokens, use_model)
                text = _repair_truncated_json(text)
            return _parse_json_response(text)

        except anthropic.APIStatusError as exc:
            last_error = exc
            if exc.status_code in (401, 403, 404):
                raise
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))

        except anthropic.APIConnectionError as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))

        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    raise RuntimeError(
        f"Claude API call failed after {MAX_RETRIES + 1} attempts: {last_error}"
    ) from last_error


def _enforce_timing_budget(slides: list[dict], talk_minutes: int) -> None:
    """Scale speaking_time_seconds so total ≤ 80% of talk length."""
    target = int(talk_minutes * 60 * 0.80)
    total = sum(s.get("speaking_time_seconds", 0) for s in slides if isinstance(s, dict))
    if total <= target or total == 0:
        return
    scale = target / total
    for s in slides:
        if isinstance(s, dict) and s.get("speaking_time_seconds"):
            s["speaking_time_seconds"] = int(s["speaking_time_seconds"] * scale)


def _enforce_highlight_terms(slides: list[dict], paper: dict) -> None:
    """Ensure every table_data has non-empty highlight_terms."""
    # Extract likely model name from paper title (first capitalized noun-phrase)
    title = paper.get("title", "")
    # Common pattern: the paper's method is a capitalized proper noun in the title
    import re
    candidates = re.findall(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b", title)
    fallback_term = candidates[0] if candidates else ""

    for s in slides:
        if not isinstance(s, dict):
            continue
        td = s.get("table_data")
        if td and isinstance(td, dict):
            ht = td.get("highlight_terms", [])
            if not ht and fallback_term:
                td["highlight_terms"] = [fallback_term]


def plan_slides(
    paper: dict,
    talk_length: str = "conference",
    include_speaker_notes: bool = True,
    include_backup_slides: bool = False,
) -> dict:
    """Generate a slide plan from a parsed academic paper.

    Args:
        paper: Parsed paper dict from pdf_parser.parse_pdf().
        talk_length: One of "lightning", "conference", "seminar".
        include_speaker_notes: Whether to make the second API call for notes.
        include_backup_slides: Whether to request backup/appendix slides.

    Returns:
        A dict containing the full slide plan.

    Raises:
        ValueError: If talk_length is invalid or API key is missing.
        RuntimeError: If API calls fail after retries.
    """
    if talk_length not in TALK_CONFIGS:
        raise ValueError(
            f"Invalid talk_length '{talk_length}'. "
            f"Choose from: {', '.join(TALK_CONFIGS)}"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY not found. "
            "Set it in .env or as an environment variable."
        )

    config = TALK_CONFIGS[talk_length]
    client = anthropic.Anthropic(api_key=api_key, timeout=300.0, max_retries=1)
    paper_summary = _build_paper_summary(paper)

    # --- Classify paper type and compute slide budget ---
    slide_counts = _calculate_slide_count(config["minutes"], paper)
    paper_class = classify_paper_type(paper)
    paper_type = paper_class["type"]

    # Select the right budget allocator for this paper type
    main_n = slide_counts["main_slides"]
    if paper_type == "survey":
        budget = _allocate_budget_survey(main_n)
    elif paper_type == "theory":
        budget = _allocate_budget_theory(main_n)
    elif paper_type == "position":
        budget = _allocate_budget_position(main_n)
    else:  # empirical (default)
        budget = _allocate_slide_budget(main_n, paper)

    # Build the narrative arc text for the prompt
    narrative_arc = _build_narrative_arc(paper_type, budget)

    # Build experiment guidance — relevant for empirical papers,
    # simplified for other types
    sections = paper.get("sections", [])
    results_keywords = {"experiment", "case study", "evaluation", "result",
                        "analysis", "ablation", "study", "finding",
                        "benchmark", "comparison", "performance"}
    result_section_names = [sec["name"] for sec in sections
                           if any(kw in sec.get("name", "").lower()
                                  for kw in results_keywords)]

    n_experiments = slide_counts["n_experiments"]
    n_results = budget.get("results", 0)

    if paper_type != "empirical":
        experiment_guidance = (
            f"This is a {PAPER_TYPES[paper_type]['label']}. "
            f"Follow the narrative arc above rather than the experiment-centric "
            f"rules. Adapt content_type values to match the arc sections."
        )
        backup_expectation = "use backup slides for supplementary material"
    else:
        if result_section_names:
            experiment_guidance = (
                f"Identified experiment/result sections in this paper: "
                f"{result_section_names}.\n"
            )
        else:
            experiment_guidance = (
                f"This paper has ~{n_experiments} distinct experiments/findings "
                f"embedded across its sections. Identify them from the text.\n"
            )

        if n_results >= n_experiments:
            experiment_guidance += (
                f"You have {n_results} result slides for {n_experiments} experiments. "
                f"Every experiment gets its own slide. Use surplus slides "
                f"({n_results - n_experiments}) for deeper dives into the most "
                f"important experiments."
            )
        else:
            experiment_guidance += (
                f"You have {n_results} result slides for {n_experiments} experiments. "
                f"Group the least critical into shared slides (max 2-3 per slide). "
                f"Every experiment must appear on at least one main slide — "
                f"never drop one entirely."
            )

        # Backup expectation for the prompt
        if n_results >= n_experiments:
            backup_expectation = "zero experiments should need backup slides"
        else:
            n_ungroupable = max(0, n_experiments - n_results * 2)
            if n_ungroupable > 0:
                backup_expectation = (
                    f"try to fit all via grouping, but up to {n_ungroupable} "
                    f"may need backup slides"
                )
            else:
                backup_expectation = (
                    "all experiments should fit via grouping — zero need backup"
                )

    timing_budget = int(config["minutes"] * 60 * 0.80)
    seconds_per_slide = int(timing_budget / max(slide_counts["main_slides"], 1))

    # Provide all budget_* keys for the prompt format call.
    # Keys not relevant to this paper type default to 0.
    budget_for_prompt = {
        "motivation": budget.get("motivation", 0),
        "method": budget.get("method", 0),
        "results": budget.get("results", 0),
        "analysis": budget.get("analysis", 0),
        "taxonomy": budget.get("taxonomy", 0),
        "comparison": budget.get("comparison", 0),
        "gaps": budget.get("gaps", 0),
        "formulation": budget.get("formulation", 0),
        "proof": budget.get("proof", 0),
        "implications": budget.get("implications", 0),
        "argument": budget.get("argument", 0),
    }

    # --- CALL 1: Plan outline + tables ---
    plan_system = SYSTEM_PROMPT_PLAN.format(
        talk_length=config["minutes"],
        talk_format=config["format"],
        main_slides=slide_counts["main_slides"],
        budget_motivation=budget_for_prompt["motivation"],
        budget_method=budget_for_prompt["method"],
        budget_results=budget_for_prompt["results"],
        budget_analysis=budget_for_prompt["analysis"],
        n_sections=len(sections),
        n_figures=paper.get("num_figures", len(paper.get("figures", []))),
        n_pages=paper.get("num_pages", 0),
        content_density=slide_counts["content_density"],
        n_experiments=slide_counts["n_experiments"],
        experiment_guidance=experiment_guidance,
        backup_expectation=backup_expectation,
        timing_budget=timing_budget,
        seconds_per_slide=seconds_per_slide,
        paper_type_label=PAPER_TYPES[paper_type]["label"],
        narrative_arc=narrative_arc,
    )

    # Scale max_tokens for larger slide counts (~400 tokens per slide)
    n_main = slide_counts["main_slides"]
    plan_tokens = max(5120, n_main * 400)
    if plan_tokens > 16384:
        plan_tokens = 16384

    plan_user = f"Here is the paper:\n\n{paper_summary}"

    log.info("Starting Claude API call 1/3: slide plan (%s, %d tokens)", MODEL, plan_tokens)
    t0 = time.time()
    plan_data = _call_claude(client, plan_system, plan_user, max_tokens=plan_tokens)
    log.info("Slide plan call completed in %.1fs", time.time() - t0)

    # Normalize: extract slides list
    if isinstance(plan_data, list):
        slides = plan_data
        plan_data = {}
    else:
        slides = plan_data.get("slides", [])

    if isinstance(slides, dict):
        slides = [slides]

    # --- CALLS 2 & 3: Speaker notes + Backup slides (parallel, fast model) ---
    # Both depend on the plan but are independent of each other.
    log.info("Starting parallel API calls (notes + backup) using %s", MODEL_FAST)
    t1 = time.time()
    notes_future: Future | None = None
    backup_future: Future | None = None

    with ThreadPoolExecutor(max_workers=2) as executor:
        # Submit speaker notes call
        if include_speaker_notes:
            slides_for_notes = []
            for s in slides:
                note_info = {
                    "slide_number": s.get("slide_number"),
                    "title": s.get("title"),
                    "content_type": s.get("content_type"),
                    "layout": s.get("layout"),
                    "bullet_points": s.get("bullet_points"),
                    "annotations": s.get("annotations"),
                    "has_figure": s.get("figure_reference") is not None,
                    "has_table": s.get("table_data") is not None,
                    "has_equation": s.get("equation_latex") is not None,
                    "has_key_number": s.get("key_number") is not None,
                }
                slides_for_notes.append(note_info)

            slides_summary = json.dumps(slides_for_notes, indent=2)
            notes_user = (
                f"Paper title: {plan_data.get('talk_title', paper['title'])}\n\n"
                f"Slides (with visual element flags):\n{slides_summary}\n\n"
                f"Generate speaker notes for each slide. "
                f"ONLY reference figures/tables/equations that are marked as present."
            )
            notes_future = executor.submit(
                _call_claude, client, SYSTEM_PROMPT_NOTES, notes_user,
                4096, MODEL_FAST,
            )

        # Submit backup slides call
        if include_backup_slides:
            main_tables = [s.get("table_data", {}).get("caption", "")
                           for s in slides if s.get("table_data")]
            main_figures = [s["figure_reference"]
                            for s in slides if s.get("figure_reference")]
            backup_system = SYSTEM_PROMPT_BACKUP.format(
                backup_slides=slide_counts["backup_slides"],
            )
            backup_user = (
                f"Paper:\n\n{paper_summary}\n\n"
                f"Main slides already include these tables: {main_tables}\n"
                f"Main slides already include these figures: {main_figures}\n\n"
                f"Generate backup slides for tables/figures NOT already covered, "
                f"plus limitations and reproducibility."
            )
            backup_future = executor.submit(
                _call_claude, client, backup_system, backup_user, 3072,
                MODEL_FAST,
            )

    # Collect speaker notes result
    log.info("Parallel calls completed in %.1fs", time.time() - t1)
    if notes_future is not None:
        notes_data = notes_future.result()

        if isinstance(notes_data, dict):
            notes_list = notes_data.get("slides", notes_data.get("notes", []))
        else:
            notes_list = notes_data
        if not isinstance(notes_list, list):
            notes_list = []

        notes_by_num = {}
        for note in notes_list:
            if isinstance(note, dict):
                num = note.get("slide_number")
                if num is not None:
                    notes_by_num[num] = note

        for slide in slides:
            num = slide.get("slide_number")
            if num in notes_by_num:
                slide["speaker_notes"] = notes_by_num[num].get("speaker_notes", "")
                slide["transition"] = notes_by_num[num].get("transition", "")
                slide["timing_cue"] = notes_by_num[num].get("timing_cue", "")
            else:
                slide.setdefault("speaker_notes", "")
                slide.setdefault("transition", "")
                slide.setdefault("timing_cue", "")
    else:
        for slide in slides:
            slide.setdefault("speaker_notes", "")
            slide.setdefault("transition", "")
            slide.setdefault("timing_cue", "")

    # Collect backup slides result
    backup_slides = []
    if backup_future is not None:
        try:
            backup_data = backup_future.result()
            if isinstance(backup_data, list):
                backup_slides = backup_data
            elif isinstance(backup_data, dict):
                backup_slides = backup_data.get("slides", [])
        except (RuntimeError, ValueError):
            backup_slides = []

    # --- Post-processing: enforce constraints Claude may have missed ---
    all_slides = slides + backup_slides
    _enforce_timing_budget(all_slides, config["minutes"])
    _enforce_highlight_terms(all_slides, paper)
    _enforce_layout_hierarchy(all_slides)
    _validate_figure_assignments(slides, paper)
    _validate_layout_variety(slides)

    # --- Validate coverage checklist ---
    coverage = plan_data.get("coverage_checklist", {})
    missing = coverage.get("missing", [])
    if missing:
        log.warning("Coverage gap: experiments %s not in any slide!", missing)
    in_main = coverage.get("experiments_in_main", [])
    in_backup = coverage.get("experiments_in_backup", [])
    total_exp = coverage.get("total_experiments", n_experiments)
    covered = len(in_main) + len(in_backup)
    if total_exp > 0 and covered < total_exp:
        log.warning(
            "Coverage incomplete: %d/%d experiments covered (main=%d, backup=%d)",
            covered, total_exp, len(in_main), len(in_backup),
        )

    return {
        "talk_title": plan_data.get("talk_title", paper.get("title", "Untitled")),
        "talk_subtitle": plan_data.get("talk_subtitle", ""),
        "authors": plan_data.get("authors", paper.get("authors", "")),
        "venue": plan_data.get("venue", ""),
        "talk_length_minutes": config["minutes"],
        "total_slides": len(slides),
        "slides": slides,
        "backup_slides": backup_slides,
        "visual_checklist": plan_data.get("visual_checklist", {}),
        "coverage_checklist": coverage,
        "paper_type": paper_class,
        "slide_budget": {
            "target_main": slide_counts["main_slides"],
            "target_backup": slide_counts["backup_slides"],
            "content_density": slide_counts["content_density"],
            "allocation": budget,
        },
    }
