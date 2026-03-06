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


def _count_experiments(paper: dict) -> int:
    """Count distinct experiment/result sections in the paper.

    Uses multiple signals:
    1. Section names with experiment keywords
    2. Sections containing significant quantitative content
    3. Figure count as a floor (papers with many figures have many findings)
    """
    sections = paper.get("sections", [])

    # Signal 1: Section names
    name_keywords = {"experiment", "case study", "evaluation", "result",
                     "analysis", "ablation", "study", "finding",
                     "benchmark", "comparison", "performance"}
    name_count = 0
    for sec in sections:
        name_lower = sec.get("name", "").lower()
        if any(kw in name_lower for kw in name_keywords):
            name_count += 1

    # Signal 2: Sections with quantitative content (tables, figures, numbers)
    quant_keywords = {"table ", "figure ", "fig.", "tab.", "accuracy", "precision",
                      "recall", "f1", "bleu", "rouge", "auc", "error rate",
                      "outperform", "baseline", "state-of-the-art", "sota",
                      "compared to", "improvement", "versus", "vs."}
    text_count = 0
    for sec in sections:
        text_lower = sec.get("text", "").lower()[:2000]  # first 2000 chars
        matches = sum(1 for kw in quant_keywords if kw in text_lower)
        if matches >= 3:  # significant quantitative content
            text_count += 1

    # Signal 3: Figure-based floor (every ~5 figures suggests ~1 experiment)
    n_figures = paper.get("num_figures", len(paper.get("figures", [])))
    figure_floor = max(3, n_figures // 5)

    count = max(name_count, text_count, figure_floor)
    log.info("Experiment count: name=%d, text=%d, figure_floor=%d → %d",
             name_count, text_count, figure_floor, count)
    return count


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
    n_experiments = _count_experiments(paper)

    # Map density to position within the range
    if density <= 5:
        main_target = main_min
    elif density >= 13:
        main_target = main_max
    else:
        frac = (density - 5) / 8.0
        main_target = int(main_min + frac * (main_max - main_min))

    # Ensure at least 60% of experiments can be covered
    fixed = 3  # title + conclusion + thankyou
    non_results = 3  # motivation(1) + method(1) + analysis(1) minimum
    min_results_slides = max(2, int(n_experiments * 0.6))
    min_main_needed = fixed + non_results + min_results_slides
    main_target = max(main_target, min(min_main_needed, main_max))

    # Backup slides: cover uncovered experiments + standard categories
    uncovered = max(0, n_experiments - min_results_slides)
    backup_target = max(backup_min, min(backup_max, uncovered + 3))

    log.info("Slide count: %d-min talk, density=%d, experiments=%d → %d main + %d backup",
             minutes, density, n_experiments, main_target, backup_target)

    return {
        "main_slides": main_target,
        "backup_slides": backup_target,
        "content_density": density,
        "time_slot_minutes": minutes,
        "n_experiments": n_experiments,
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
    n_experiments = _count_experiments(paper)

    # --- STEP 1: Set minimums for non-results ---
    motivation_slides = 1
    method_slides = 1
    analysis_slides = 1
    non_results_min = motivation_slides + method_slides + analysis_slides  # 3

    # --- STEP 2: Give everything else to results ---
    results_slides = max(2, available - non_results_min)

    # Cap results at number of experiments (no empty result slides)
    results_slides = min(results_slides, n_experiments)

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

### Rule 2: Layout Selection
Every slide (except title and thankyou) MUST specify a "layout" field:
- "hero_figure": Large centered figure (60%+ of slide) with 2-4 annotations
- "hero_table": Full-width table with optional table_headline and context_line
- "key_number": One large number with context (for the headline metric)
- "equation": Centered equation with annotations explaining each term
- "bullets": Traditional bullet points (use SPARINGLY — max 2-3 per talk)

Prefer visual layouts. A good conference talk has: 4-5 hero_figure/hero_table, \
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

Structure your {main_slides} slides following this pattern:

1. Title slide (paper title — exception to assertion rule)
2. Motivation (1-{budget_motivation} slides): State the problem as an assertion
3. Method (1-{budget_method} slides): Architecture/approach as assertion claims
4. Results ({budget_results} slides): Each result as an assertion with evidence
5. Analysis (1-{budget_analysis} slides): Ablation, visualization, generalization
6. Conclusion (1 slide): Executive summary of proven claims
7. Thank you (1 slide)

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
        parts.append(f"\nAVAILABLE FIGURES ({len(figures)} total):")
        parts.append(
            "IMPORTANT: Use these figures in main slides. Do NOT default to "
            "tables when a relevant figure exists. Alternate table/figure "
            "layouts for visual variety."
        )
        for i, fig in enumerate(figures):
            caption = fig.get("caption") or "(no caption detected)"
            page = fig.get("page", "?")
            fig_num = fig.get("figure_number") or fig.get("figure_label") or (i + 1)
            parts.append(
                f"  - Figure {fig_num} (page {page}): {caption}\n"
                f"    → ONLY use on slides whose content matches this caption"
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

    # --- Dynamic slide count ---
    slide_counts = _calculate_slide_count(config["minutes"], paper)
    budget = _allocate_slide_budget(slide_counts["main_slides"], paper)

    # Build experiment guidance — always provide experiment names
    sections = paper.get("sections", [])
    results_keywords = {"experiment", "case study", "evaluation", "result",
                        "analysis", "ablation", "study", "finding",
                        "benchmark", "comparison", "performance"}
    result_section_names = [sec["name"] for sec in sections
                           if any(kw in sec.get("name", "").lower()
                                  for kw in results_keywords)]

    n_experiments = slide_counts["n_experiments"]
    n_results = budget["results"]

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

    # --- CALL 1: Plan outline + tables ---
    plan_system = SYSTEM_PROMPT_PLAN.format(
        talk_length=config["minutes"],
        talk_format=config["format"],
        main_slides=slide_counts["main_slides"],
        budget_motivation=budget["motivation"],
        budget_method=budget["method"],
        budget_results=budget["results"],
        budget_analysis=budget["analysis"],
        n_sections=len(sections),
        n_figures=paper.get("num_figures", len(paper.get("figures", []))),
        n_pages=paper.get("num_pages", 0),
        content_density=slide_counts["content_density"],
        n_experiments=slide_counts["n_experiments"],
        experiment_guidance=experiment_guidance,
        backup_expectation=backup_expectation,
        timing_budget=timing_budget,
        seconds_per_slide=seconds_per_slide,
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
        "slide_budget": {
            "target_main": slide_counts["main_slides"],
            "target_backup": slide_counts["backup_slides"],
            "content_density": slide_counts["content_density"],
            "allocation": budget,
        },
    }
