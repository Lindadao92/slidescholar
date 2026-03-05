"""PDF parser for academic papers using PyMuPDF."""

import logging
import os
import re
import tempfile

import fitz  # PyMuPDF

log = logging.getLogger("slidescholar")


# Section headers commonly found in academic papers
SECTION_PATTERNS = [
    r"abstract",
    r"introduction",
    r"related\s+work",
    r"background",
    r"literature\s+review",
    r"method(?:ology)?(?:s)?",
    r"approach",
    r"(?:proposed\s+)?(?:framework|model|system)",
    r"experiment(?:s|al)?(?:\s+(?:setup|results))?",
    r"results?(?:\s+and\s+(?:discussion|analysis))?",
    r"evaluation",
    r"discussion",
    r"analysis",
    r"conclusion(?:s)?(?:\s+and\s+future\s+work)?",
    r"future\s+work",
    r"acknowledgment(?:s)?",
    r"references",
    r"appendix(?:\s+[a-z])?",
    r"supplementary\s+material(?:s)?",
]

SECTION_HEADER_RE = re.compile(
    r"^\s*(?:(\d+|[IVXLC]+)[\.\)]\s*)?"  # optional numbering
    r"(" + "|".join(SECTION_PATTERNS) + r")"
    r"\s*$",
    re.IGNORECASE | re.MULTILINE,
)

FIGURE_CAPTION_RE = re.compile(
    r"((?:Figure|Fig\.?)\s*\d+[^.\n]*(?:\.[^.\n]*)?\.?)",
    re.IGNORECASE,
)


def _extract_metadata(doc: fitz.Document) -> dict:
    """Extract title and authors from document metadata and first-page heuristics."""
    meta = doc.metadata or {}
    title = meta.get("title", "").strip() or None
    authors = meta.get("author", "").strip() or None

    # If metadata is missing, try to pull title/authors from the first page.
    # Heuristic: the largest font text near the top is the title, and the
    # next distinct block of text is the author list.
    if (not title or not authors) and doc.page_count > 0:
        first_page = doc[0]
        blocks = first_page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

        text_spans = []
        for block in blocks:
            if block["type"] != 0:  # text blocks only
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text:
                        text_spans.append({
                            "text": text,
                            "size": span["size"],
                            "y": span["bbox"][1],
                        })

        if text_spans:
            # Sort by vertical position (top of page first)
            text_spans.sort(key=lambda s: s["y"])

            # Consider only the top third of the page for title/author detection
            page_height = first_page.rect.height
            top_spans = [s for s in text_spans if s["y"] < page_height * 0.35]

            if top_spans and not title:
                max_size = max(s["size"] for s in top_spans)
                title_parts = [s["text"] for s in top_spans if s["size"] >= max_size - 0.5]
                title = " ".join(title_parts).strip() or None

            if top_spans and not authors:
                # Authors are usually the second-largest font after the title
                sizes = sorted({s["size"] for s in top_spans}, reverse=True)
                if len(sizes) >= 2:
                    author_size = sizes[1]
                    author_parts = [
                        s["text"] for s in top_spans
                        if abs(s["size"] - author_size) < 0.5
                    ]
                    candidate = " ".join(author_parts).strip()
                    # Basic sanity check — author lines are usually short-ish
                    # and don't look like section headers or body text
                    if candidate and len(candidate) < 500:
                        authors = candidate

    return {"title": title or "Untitled", "authors": authors or "Unknown"}


def _detect_sections(pages_text: list[str]) -> list[dict]:
    """Split the full text into labeled sections using header heuristics."""
    full_text = "\n".join(pages_text)
    matches = list(SECTION_HEADER_RE.finditer(full_text))

    if not matches:
        # No recognizable sections — return the whole body as one chunk
        return [{"name": "Full Text", "text": full_text.strip()}]

    sections = []
    for i, match in enumerate(matches):
        name = match.group(2).strip().title()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        text = full_text[start:end].strip()
        sections.append({"name": name, "text": text})

    # Capture any preamble before the first detected header (often the abstract
    # when it isn't explicitly labeled)
    preamble = full_text[: matches[0].start()].strip()
    if preamble:
        sections.insert(0, {"name": "Preamble", "text": preamble})

    return sections


def _extract_abstract(sections: list[dict], pages_text: list[str]) -> str:
    """Return the abstract, searching sections first then falling back to regex."""
    for sec in sections:
        if sec["name"].lower() == "abstract":
            return sec["text"]

    # Fallback: look for text between "Abstract" and the next section header
    full_text = "\n".join(pages_text)
    abs_match = re.search(
        r"(?:^|\n)\s*abstract\s*\n(.*?)(?=\n\s*(?:\d+[\.\)]\s*)?(?:introduction|1[\.\)])\s*\n|$)",
        full_text,
        re.IGNORECASE | re.DOTALL,
    )
    if abs_match:
        return abs_match.group(1).strip()

    return ""


def _find_nearby_caption(page: fitz.Page, image_bbox: fitz.Rect) -> str:
    """Search for a figure caption near an image bounding box.

    Looks below the image first (most common), then above.
    Handles both single-column and two-column layouts by constraining
    the search region to the horizontal extent of the image.
    """
    page_height = page.rect.height
    img_x0, img_x1 = image_bbox.x0, image_bbox.x1

    # Define search regions: below the image, then above
    search_regions = [
        # Below: from bottom of image to 120pt further down, same column
        fitz.Rect(img_x0 - 20, image_bbox.y1, img_x1 + 20, min(image_bbox.y1 + 120, page_height)),
        # Above: from 80pt above image top to the image top
        fitz.Rect(img_x0 - 20, max(image_bbox.y0 - 80, 0), img_x1 + 20, image_bbox.y0),
    ]

    for region in search_regions:
        text = page.get_text("text", clip=region).strip()
        cap_match = FIGURE_CAPTION_RE.search(text)
        if cap_match:
            return cap_match.group(1).strip()

    return ""


def _clean_caption(raw: str, max_len: int = 150) -> str:
    """Normalise a caption: collapse whitespace, strip, truncate."""
    text = re.sub(r"\s+", " ", raw).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _find_figure_captions_on_page(page_text: str) -> list[dict]:
    """Return all ``Figure N`` / ``Fig. N`` captions found in *page_text*.

    Each entry: ``{"number": int, "caption": str}``.
    """
    results = []
    for m in re.finditer(
        r"(?:Figure|Fig\.?)\s+(\d+)\s*[:\.]?\s*([^\n]{0,200})",
        page_text,
        re.IGNORECASE,
    ):
        num = int(m.group(1))
        caption_body = m.group(2).strip()
        full_caption = f"Figure {num}: {caption_body}" if caption_body else f"Figure {num}"
        results.append({"number": num, "caption": _clean_caption(full_caption)})
    return results


# ------------------------------------------------------------------
# Strategy 1  — raster extraction (PNG / JPEG embedded images)
# ------------------------------------------------------------------

def _extract_raster_figures(
    doc: fitz.Document,
    temp_dir: str,
    pages_text: list[str],
) -> list[dict]:
    """Extract embedded raster images via ``page.get_images()``."""
    figures: list[dict] = []
    seen_xrefs: set[int] = set()

    for page_num in range(doc.page_count):
        page = doc[page_num]
        images = page.get_images(full=True)

        for img_index, img_info in enumerate(images):
            xref = img_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue

            if not base_image or not base_image.get("image"):
                continue

            width = base_image.get("width", 0)
            height = base_image.get("height", 0)
            if width < 100 or height < 100:
                continue

            ext = base_image.get("ext", "png")
            filename = f"fig_p{page_num}_{img_index}.{ext}"
            filepath = os.path.join(temp_dir, filename)

            img_bytes = base_image["image"]

            # Handle CMYK → RGB conversion
            if base_image.get("colorspace", 0) == fitz.csRGB.n:
                pass  # already RGB
            try:
                pix = fitz.Pixmap(img_bytes)
                if pix.n - pix.alpha > 3:  # CMYK or other non-RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                pix.save(filepath)
            except Exception:
                # Fallback: write raw bytes
                with open(filepath, "wb") as f:
                    f.write(img_bytes)

            # Caption from nearby text blocks
            caption = ""
            try:
                img_rects = page.get_image_rects(xref)
                if img_rects:
                    caption = _find_nearby_caption(page, img_rects[0])
            except Exception:
                pass

            # Detect figure number from caption
            fig_number = None
            cap_m = re.search(r"(?:Figure|Fig\.?)\s*(\d+)", caption, re.IGNORECASE)
            if cap_m:
                fig_number = int(cap_m.group(1))

            # If caption search missed, try page text
            if fig_number is None:
                page_caps = _find_figure_captions_on_page(pages_text[page_num])
                if len(page_caps) == 1:
                    fig_number = page_caps[0]["number"]
                    if not caption:
                        caption = page_caps[0]["caption"]

            figures.append({
                "path": filepath,
                "filename": filename,
                "page": page_num + 1,
                "figure_number": fig_number,
                "figure_label": f"Figure {fig_number}" if fig_number else None,
                "caption": _clean_caption(caption),
                "source": "raster",
                "width": width,
                "height": height,
            })

    return figures


# ------------------------------------------------------------------
# Strategy 2  — page-render fallback for vector figures
# ------------------------------------------------------------------

_PAGE_RENDER_DPI = 200
_CROP_RATIO = 0.80  # keep top 80% of the page (figure area)


def _render_page_figure(
    doc: fitz.Document,
    page_num: int,         # 0-indexed
    figure_number: int,
    caption: str,
    temp_dir: str,
) -> dict | None:
    """Render a page to PNG and crop to the figure area."""
    try:
        page = doc[page_num]

        # Crop to top portion using clip (figure area, excluding footer)
        r = page.rect
        crop_rect = fitz.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * _CROP_RATIO)
        pix = page.get_pixmap(dpi=_PAGE_RENDER_DPI, clip=crop_rect)

        # Convert CMYK if needed
        if pix.n - pix.alpha > 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)

        filename = f"fig_p{page_num}_v{figure_number}.png"
        filepath = os.path.join(temp_dir, filename)
        pix.save(filepath)

        return {
            "path": filepath,
            "filename": filename,
            "page": page_num + 1,
            "figure_number": figure_number,
            "figure_label": f"Figure {figure_number}",
            "caption": _clean_caption(caption),
            "source": "page_render",
            "width": pix.width,
            "height": pix.height,
        }
    except Exception as exc:
        log.warning("Page-render failed for Figure %d (page %d): %s",
                     figure_number, page_num + 1, exc)
        return None


def _extract_rendered_figures(
    doc: fitz.Document,
    temp_dir: str,
    pages_text: list[str],
    raster_figures: list[dict],
) -> list[dict]:
    """Find figure captions on pages that have NO raster image and render
    those pages as PNG screenshots (vector-graphic fallback)."""

    # Figure numbers already covered by raster extraction
    covered_nums = {f["figure_number"] for f in raster_figures if f.get("figure_number")}
    # Pages that already have a raster figure
    covered_pages = {f["page"] for f in raster_figures}

    rendered: list[dict] = []

    for page_num, text in enumerate(pages_text):
        page_1idx = page_num + 1
        captions = _find_figure_captions_on_page(text)
        if not captions:
            continue

        for cap in captions:
            fig_num = cap["number"]
            if fig_num in covered_nums:
                continue
            # Also skip if a raster figure is already on this page
            # (it was probably just not matched to this number yet)
            if page_1idx in covered_pages:
                continue

            fig = _render_page_figure(
                doc, page_num, fig_num, cap["caption"], temp_dir,
            )
            if fig:
                rendered.append(fig)
                covered_nums.add(fig_num)
                log.info("Rendered vector Figure %d from page %d", fig_num, page_1idx)

    return rendered


# ------------------------------------------------------------------
# Unified extraction entry point
# ------------------------------------------------------------------

def _extract_figures(
    doc: fitz.Document,
    temp_dir: str,
    pages_text: list[str],
) -> list[dict]:
    """Extract figures using raster extraction + page-render fallback.

    Returns a list of figure dicts sorted by ``figure_number``.
    """
    os.makedirs(temp_dir, exist_ok=True)

    # Strategy 1: raster images
    raster = _extract_raster_figures(doc, temp_dir, pages_text)
    log.info("Raster extraction: %d figure(s)", len(raster))

    # Strategy 2: rendered pages for vector figures
    rendered = _extract_rendered_figures(doc, temp_dir, pages_text, raster)
    log.info("Page-render extraction: %d figure(s)", len(rendered))

    all_figures = raster + rendered

    # Assign figure numbers to any remaining un-numbered figures
    assigned_nums = {f["figure_number"] for f in all_figures if f.get("figure_number")}
    next_num = max(assigned_nums, default=0) + 1
    for fig in all_figures:
        if fig.get("figure_number") is None:
            while next_num in assigned_nums:
                next_num += 1
            fig["figure_number"] = next_num
            fig["figure_label"] = f"Figure {next_num}"
            assigned_nums.add(next_num)
            next_num += 1

    # Sort by figure number for deterministic ordering
    all_figures.sort(key=lambda f: f.get("figure_number", 999))

    # Summary
    for fig in all_figures:
        log.info(
            "  Figure %d (page %d, %s): %dx%d  %s",
            fig["figure_number"], fig["page"], fig["source"],
            fig.get("width", 0), fig.get("height", 0),
            fig["caption"][:60] if fig["caption"] else "(no caption)",
        )

    return all_figures


def parse_pdf(pdf_path: str, image_output_dir: str | None = None) -> dict:
    """Parse an academic PDF and return structured content.

    Args:
        pdf_path: Path to the PDF file.
        image_output_dir: Directory to save extracted images.
            Defaults to a new temporary directory.

    Returns:
        A dict with keys: title, authors, abstract, sections, figures,
        num_pages, num_figures.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        ValueError: If the file cannot be opened as a PDF.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ValueError(f"Failed to open PDF: {exc}") from exc

    if doc.page_count == 0:
        doc.close()
        raise ValueError("PDF has no pages")

    temp_dir = image_output_dir or tempfile.mkdtemp(prefix="slidescholar_")

    try:
        # 1. Extract text page-by-page
        pages_text = []
        for page_num in range(doc.page_count):
            page = doc[page_num]
            pages_text.append(page.get_text("text"))

        # 2. Detect sections
        sections = _detect_sections(pages_text)

        # 3. Extract abstract
        abstract = _extract_abstract(sections, pages_text)

        # 4. Extract metadata
        metadata = _extract_metadata(doc)

        # 5. Extract figures (raster + page-render fallback)
        figures = _extract_figures(doc, temp_dir, pages_text)

        num_pages = doc.page_count
    finally:
        doc.close()

    return {
        "title": metadata["title"],
        "authors": metadata["authors"],
        "abstract": abstract,
        "sections": sections,
        "figures": figures,
        "num_pages": num_pages,
        "num_figures": len(figures),
    }
