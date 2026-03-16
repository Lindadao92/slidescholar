"""PDF parser for academic papers using PyMuPDF."""

import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor

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
# Caption-anchored figure bounding-box detection
# ------------------------------------------------------------------


def _find_caption_rect(page: fitz.Page, figure_num: int) -> fitz.Rect | None:
    """Find the bounding box of a 'Figure N' caption on *page*.

    Searches text blocks for the pattern ``Figure N`` / ``Fig. N`` and
    returns the bounding rectangle of the containing block.
    """
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    pattern = re.compile(rf"(?:Figure|Fig\.?)\s*{figure_num}\b", re.IGNORECASE)

    for block in blocks:
        if block["type"] != 0:
            continue
        for line in block.get("lines", []):
            line_text = "".join(span["text"] for span in line["spans"])
            if pattern.search(line_text):
                return fitz.Rect(block["bbox"])
    return None


def _estimate_figure_bbox(
    page: fitz.Page, caption_rect: fitz.Rect,
) -> fitz.Rect:
    """Estimate the figure bounding box given its caption location.

    In academic papers, figures sit *above* their captions.  This function:
      1. Determines the column (left / right / full-width) from the caption.
      2. Looks for image blocks (type=1) directly above the caption.
      3. Falls back to the gap between the preceding text block and the
         caption — that's where the figure lives.
    """
    page_rect = page.rect
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

    # Column detection
    page_mid = page_rect.width / 2
    cap_width = caption_rect.x1 - caption_rect.x0
    cap_mid = (caption_rect.x0 + caption_rect.x1) / 2

    if cap_width > page_rect.width * 0.6:
        col_x0, col_x1 = page_rect.x0, page_rect.x1
    elif cap_mid < page_mid:
        col_x0, col_x1 = page_rect.x0, page_mid + 10
    else:
        col_x0, col_x1 = page_mid - 10, page_rect.x1

    # Strategy A: find image blocks (type=1) above the caption
    best_img = None
    for block in blocks:
        if block["type"] != 1:
            continue
        bbox = fitz.Rect(block["bbox"])
        if bbox.y1 > caption_rect.y0 + 10:
            continue
        if bbox.x1 < col_x0 or bbox.x0 > col_x1:
            continue
        if best_img is None or bbox.y1 > best_img.y1:
            best_img = bbox

    if best_img is not None:
        return fitz.Rect(
            max(best_img.x0 - 5, page_rect.x0),
            max(best_img.y0 - 5, page_rect.y0),
            min(best_img.x1 + 5, page_rect.x1),
            min(best_img.y1 + 5, caption_rect.y0),
        )

    # Strategy B: no image block found — the figure is likely vector
    # content (drawings, positioned text like attention heatmaps, etc.).
    # Find the nearest section header or large vertical gap above the
    # caption to determine where the figure area starts.
    above_blocks: list[tuple[float, float]] = []  # (y0, y1) of text blocks above caption
    for block in blocks:
        if block["type"] != 0:
            continue
        bbox = fitz.Rect(block["bbox"])
        if bbox.y1 < caption_rect.y0 - 5 and bbox.x1 >= col_x0 and bbox.x0 <= col_x1:
            above_blocks.append((bbox.y0, bbox.y1))

    if above_blocks:
        above_blocks.sort(key=lambda b: b[0])

        # Look for the largest vertical gap between consecutive blocks.
        # The figure content starts just below the gap (after the header/
        # preceding paragraph).
        best_gap_bottom = above_blocks[0][0]  # default: top of first block
        best_gap_size = 0
        for i in range(1, len(above_blocks)):
            gap = above_blocks[i][0] - above_blocks[i - 1][1]
            if gap > best_gap_size:
                best_gap_size = gap
                best_gap_bottom = above_blocks[i - 1][1]

        # If the largest gap is small (<20pt), all these blocks are likely
        # the figure content itself (e.g., attention heatmap words).
        # In that case, start from the top of the first block.
        if best_gap_size >= 20:
            fig_top = best_gap_bottom + 3
        else:
            fig_top = above_blocks[0][0] - 5

        # Safety: ensure at least 150pt tall (≈2 inches) to avoid tiny crops
        min_height = 150
        if caption_rect.y0 - fig_top < min_height:
            fig_top = max(page_rect.y0, caption_rect.y0 - min_height)
    else:
        fig_top = max(page_rect.y0, caption_rect.y0 - 300)

    return fitz.Rect(col_x0, max(fig_top, page_rect.y0), col_x1, caption_rect.y0 - 2)


def _is_fullpage_image(page: fitz.Page, xref: int) -> bool:
    """Return True if the raster image at *xref* covers >80 % of *page*."""
    try:
        rects = page.get_image_rects(xref)
        if not rects:
            return False
        img_rect = rects[0]
        page_area = page.rect.width * page.rect.height
        img_area = img_rect.width * img_rect.height
        return page_area > 0 and img_area > page_area * 0.80
    except Exception:
        return False


# ------------------------------------------------------------------
# Strategy 1  — raster extraction (PNG / JPEG embedded images)
# ------------------------------------------------------------------

def _save_image(img_bytes: bytes, filepath: str) -> None:
    """Save image bytes to disk, converting CMYK → RGB if needed."""
    try:
        pix = fitz.Pixmap(img_bytes)
        if pix.n - pix.alpha > 3:  # CMYK or other non-RGB
            pix = fitz.Pixmap(fitz.csRGB, pix)
        pix.save(filepath)
    except Exception:
        with open(filepath, "wb") as f:
            f.write(img_bytes)


def _extract_raster_figures(
    doc: fitz.Document,
    temp_dir: str,
    pages_text: list[str],
) -> list[dict]:
    """Extract embedded raster images via ``page.get_images()``."""
    figures: list[dict] = []
    seen_xrefs: set[int] = set()

    # Cache caption parsing per page (avoid redundant regex)
    _page_captions_cache: dict[int, list[dict]] = {}

    def _get_page_captions(page_num: int) -> list[dict]:
        if page_num not in _page_captions_cache:
            _page_captions_cache[page_num] = _find_figure_captions_on_page(pages_text[page_num])
        return _page_captions_cache[page_num]

    # Phase 1: Extract image data from doc (sequential — doc isn't thread-safe)
    pending: list[tuple[dict, str, str]] = []  # (fig_info, filepath, img_bytes)

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

            # Skip full-page images (scanned pages, background images).
            # These will be re-extracted with tight cropping in Strategy 2.
            if _is_fullpage_image(page, xref):
                log.info(
                    "Skipping full-page raster on page %d (xref=%d, %dx%d) "
                    "— will re-extract with tight crop",
                    page_num + 1, xref, width, height,
                )
                continue

            ext = base_image.get("ext", "png")
            filename = f"fig_p{page_num}_{img_index}.{ext}"
            filepath = os.path.join(temp_dir, filename)

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
                page_caps = _get_page_captions(page_num)
                if len(page_caps) == 1:
                    fig_number = page_caps[0]["number"]
                    if not caption:
                        caption = page_caps[0]["caption"]

            fig_info = {
                "path": filepath,
                "filename": filename,
                "page": page_num + 1,
                "figure_number": fig_number,
                "figure_label": f"Figure {fig_number}" if fig_number else None,
                "caption": _clean_caption(caption),
                "source": "raster",
                "width": width,
                "height": height,
            }
            pending.append((fig_info, filepath, base_image["image"]))

    # Phase 2: Save images to disk in parallel (Pixmap conversion + I/O)
    if pending:
        with ThreadPoolExecutor(max_workers=4) as executor:
            save_futures = [
                executor.submit(_save_image, img_bytes, filepath)
                for _, filepath, img_bytes in pending
            ]
            for future in save_futures:
                future.result()  # propagate exceptions
        figures = [info for info, _, _ in pending]

    return figures


# ------------------------------------------------------------------
# Strategy 2  — page-render fallback for vector figures
# ------------------------------------------------------------------

_PAGE_RENDER_DPI = 200


def _render_figure_crop(
    doc: fitz.Document,
    page_num: int,
    figure_num: int,
    caption: str,
    temp_dir: str,
) -> dict | None:
    """Render a tightly-cropped figure region from *page_num*.

    1. Locate the ``Figure N`` caption on the page.
    2. Estimate the figure bounding box above the caption.
    3. Render only that region at high DPI.
    4. If the crop still covers >80 % of the page, shrink to the
       caption-anchored estimate and retry.
    """
    try:
        page = doc[page_num]
        page_rect = page.rect
        page_area = page_rect.width * page_rect.height

        # Step 1: find caption location
        cap_rect = _find_caption_rect(page, figure_num)

        if cap_rect:
            # Step 2: estimate figure bbox from caption anchor
            fig_bbox = _estimate_figure_bbox(page, cap_rect)
        else:
            # No caption found — fall back to the top 60 % of the page
            # (tighter than the old 80 % default)
            fig_bbox = fitz.Rect(
                page_rect.x0, page_rect.y0,
                page_rect.x1, page_rect.y0 + page_rect.height * 0.60,
            )

        # Sanity: ensure the crop region has a reasonable size
        fig_area = fig_bbox.width * fig_bbox.height
        min_area = page_area * 0.02   # at least 2 % of page
        if fig_area < min_area:
            # Too tiny — widen to column width, add vertical padding
            fig_bbox = fitz.Rect(
                fig_bbox.x0,
                max(page_rect.y0, fig_bbox.y0 - 50),
                fig_bbox.x1,
                min(fig_bbox.y1 + 50, page_rect.y1),
            )

        # Step 3: render the crop
        pix = page.get_pixmap(dpi=_PAGE_RENDER_DPI, clip=fig_bbox)
        if pix.n - pix.alpha > 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)

        # Step 4: fullpage safety check — if still >80 % of page, tighten
        crop_area = fig_bbox.width * fig_bbox.height
        if page_area > 0 and crop_area > page_area * 0.80 and cap_rect:
            # Retry with a smaller region: 250 pt above caption, full column width
            tight = fitz.Rect(
                fig_bbox.x0,
                max(page_rect.y0, cap_rect.y0 - 250),
                fig_bbox.x1,
                cap_rect.y0 - 2,
            )
            if tight.width > 50 and tight.height > 50:
                pix = page.get_pixmap(dpi=_PAGE_RENDER_DPI, clip=tight)
                if pix.n - pix.alpha > 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                log.info(
                    "Figure %d (page %d): initial crop was %.0f%% of page, "
                    "retried with tight crop (%.0f×%.0f pt)",
                    figure_num, page_num + 1,
                    crop_area / page_area * 100, tight.width, tight.height,
                )

        filename = f"fig_p{page_num}_v{figure_num}.png"
        filepath = os.path.join(temp_dir, filename)
        pix.save(filepath)

        return {
            "path": filepath,
            "filename": filename,
            "page": page_num + 1,
            "figure_number": figure_num,
            "figure_label": f"Figure {figure_num}",
            "caption": _clean_caption(caption),
            "source": "page_render",
            "width": pix.width,
            "height": pix.height,
        }
    except Exception as exc:
        log.warning(
            "Tight-crop render failed for Figure %d (page %d): %s",
            figure_num, page_num + 1, exc,
        )
        return None


def _extract_rendered_figures(
    doc: fitz.Document,
    temp_dir: str,
    pages_text: list[str],
    raster_figures: list[dict],
) -> list[dict]:
    """Extract figures not covered by raster extraction using caption-anchored
    tight crops (vector-graphic fallback and fullpage-raster re-extraction)."""

    covered_nums = {f["figure_number"] for f in raster_figures if f.get("figure_number")}

    rendered: list[dict] = []

    for page_num, text in enumerate(pages_text):
        captions = _find_figure_captions_on_page(text)
        if not captions:
            continue

        for cap in captions:
            fig_num = cap["number"]
            if fig_num in covered_nums:
                continue

            result = _render_figure_crop(
                doc, page_num, fig_num, cap["caption"], temp_dir,
            )
            if result:
                rendered.append(result)
                covered_nums.add(fig_num)
                log.info(
                    "Tight-cropped Figure %d from page %d (%dx%d px)",
                    fig_num, page_num + 1, result["width"], result["height"],
                )

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
