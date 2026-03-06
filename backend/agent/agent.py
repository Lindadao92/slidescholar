#!/usr/bin/env python3
"""
SlideScholar arXiv Agent
------------------------
Daily agent that:
1. Fetches new papers from arXiv
2. Extracts author emails from PDFs
3. Generates a .pptx via SlideScholar API
4. Emails authors with the deck attached
"""

import os
import re
import time
import smtplib
import logging
import requests
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timedelta

import feedparser
import fitz  # PyMuPDF

# ── Config from environment variables ────────────────────────────────────────
SLIDESCHOLAR_API   = os.environ["SLIDESCHOLAR_API_URL"]   # e.g. https://slidescholar.up.railway.app
GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]      # Gmail App Password (not your real password)
PAPERS_PER_RUN     = int(os.getenv("PAPERS_PER_RUN", "5"))
TALK_LENGTH        = os.getenv("TALK_LENGTH", "15-min")    # or "5-min", "30-min"
LOG_LEVEL          = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── arXiv categories to monitor ───────────────────────────────────────────────
ARXIV_FEEDS = [
    "https://rss.arxiv.org/rss/cs.AI",
    "https://rss.arxiv.org/rss/cs.LG",
    "https://rss.arxiv.org/rss/q-bio",
    "https://rss.arxiv.org/rss/physics.med-ph",
]

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def fetch_arxiv_papers(max_papers: int) -> list[dict]:
    """Fetch recent papers from arXiv RSS feeds."""
    papers = []
    seen_ids = set()

    for feed_url in ARXIV_FEEDS:
        if len(papers) >= max_papers:
            break
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                if len(papers) >= max_papers:
                    break
                raw_id = entry.get("id", "")
# Handle both formats: /abs/2603.04448 and oai:arXiv.org:2603.04448v1
if "/abs/" in raw_id:
    arxiv_id = raw_id.split("/abs/")[-1].strip()
elif "arXiv.org:" in raw_id:
    arxiv_id = raw_id.split("arXiv.org:")[-1].strip()
else:
    arxiv_id = raw_id.strip()
# Remove version suffix (v1, v2 etc)
arxiv_id = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id
                if not arxiv_id or arxiv_id in seen_ids:
                    continue
                seen_ids.add(arxiv_id)
                papers.append({
                    "id":       arxiv_id,
                    "title":    entry.get("title", "").replace("\n", " ").strip(),
                    "pdf_url":  f"https://arxiv.org/pdf/{arxiv_id}",
                    "abs_url":  f"https://arxiv.org/abs/{arxiv_id}",
                })
        except Exception as e:
            log.warning(f"Failed to fetch {feed_url}: {e}")

    log.info(f"Fetched {len(papers)} papers from arXiv")
    return papers


def extract_emails_from_pdf(pdf_bytes: bytes) -> list[str]:
    """Extract author emails from the first 2 pages of a PDF."""
    emails = []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            # Only look at first 2 pages — that's where author info lives
            for page_num in range(min(2, len(doc))):
                text = doc[page_num].get_text()
                found = EMAIL_REGEX.findall(text)
                emails.extend(found)
    except Exception as e:
        log.warning(f"PDF email extraction failed: {e}")

    # Deduplicate and filter out obvious non-author emails
    seen = set()
    clean = []
    for email in emails:
        email = email.lower().strip(".")
        if email in seen:
            continue
        seen.add(email)
        # Filter out common false positives
        if any(skip in email for skip in ["example.", "arxiv.", "latex", "sty@", ".sty"]):
            continue
        clean.append(email)

    return clean


def generate_slides(pdf_bytes: bytes, title: str) -> bytes | None:
    """Call SlideScholar API to parse PDF and generate .pptx."""
    try:
        # Step 1: Parse
        log.info(f"Parsing PDF for: {title[:60]}")
        parse_resp = requests.post(
            f"{SLIDESCHOLAR_API}/api/parse",
            files={"file": ("paper.pdf", pdf_bytes, "application/pdf")},
            timeout=60,
        )
        parse_resp.raise_for_status()
        parse_data = parse_resp.json()
        doc_id = parse_data.get("doc_id")
        if not doc_id:
            log.error("No doc_id returned from /api/parse")
            return None

        # Step 2: Generate
        log.info(f"Generating slides for doc_id={doc_id}")
        gen_resp = requests.post(
            f"{SLIDESCHOLAR_API}/api/generate",
            json={
                "doc_id":      doc_id,
                "talk_length": TALK_LENGTH,
                "density":     13,
            },
            timeout=180,  # generation takes ~2 min
        )
        gen_resp.raise_for_status()

        # The API returns the .pptx as a file download
        content_type = gen_resp.headers.get("content-type", "")
        if "application/vnd" in content_type or "octet-stream" in content_type:
            return gen_resp.content

        # If it returns JSON with a download URL instead
        gen_data = gen_resp.json()
        if "download_url" in gen_data:
            dl = requests.get(gen_data["download_url"], timeout=30)
            dl.raise_for_status()
            return dl.content

        log.error(f"Unexpected response from /api/generate: {gen_data}")
        return None

    except requests.exceptions.Timeout:
        log.error(f"Timeout generating slides for: {title[:60]}")
        return None
    except Exception as e:
        log.error(f"Slide generation failed for {title[:60]}: {e}")
        return None


def send_email(to_email: str, author_first_name: str, paper_title: str,
               paper_url: str, pptx_bytes: bytes) -> bool:
    """Send cold email with .pptx attached."""
    subject = f"I turned your paper into slides — free to use"

    # Clean up title for filename
    safe_title = re.sub(r'[^\w\s-]', '', paper_title[:50]).strip().replace(' ', '_')
    filename = f"SlideScholar_{safe_title}.pptx"

    body = f"""Hi {author_first_name},

I came across your paper "{paper_title}" on arXiv and used SlideScholar to automatically generate a presentation deck from it.

I've attached the .pptx — it's fully editable, so feel free to use or adapt it however you like.

If you find it useful, you can generate slides from any paper at:
https://slidescholar.vercel.app

Best,
The SlideScholar Team

---
Paper: {paper_url}
"""

    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        # Attach .pptx
        attachment = MIMEApplication(pptx_bytes, Name=filename)
        attachment["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(attachment)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, to_email, msg.as_string())

        log.info(f"✅ Email sent to {to_email}")
        return True

    except Exception as e:
        log.error(f"Failed to send email to {to_email}: {e}")
        return False


def guess_first_name(email: str) -> str:
    """Best-effort first name from email address."""
    local = email.split("@")[0]
    # Handle formats like jsmith, john.smith, john_smith
    parts = re.split(r'[._\-]', local)
    if len(parts) >= 2:
        # john.smith → John
        name = parts[0]
    else:
        name = local
    return name.capitalize() if len(name) > 1 else "there"


def run():
    log.info("=== SlideScholar arXiv Agent starting ===")
    papers = fetch_arxiv_papers(PAPERS_PER_RUN)
    
    sent_count = 0
    skip_count = 0

    for paper in papers:
        log.info(f"Processing: {paper['title'][:70]}")

        # Download PDF
        try:
            pdf_resp = requests.get(paper["pdf_url"], timeout=30)
            pdf_resp.raise_for_status()
            pdf_bytes = pdf_resp.content
        except Exception as e:
            log.warning(f"Could not download PDF: {e}")
            skip_count += 1
            continue

        # Extract emails
        emails = extract_emails_from_pdf(pdf_bytes)
        if not emails:
            log.info(f"No emails found in PDF, skipping: {paper['title'][:60]}")
            skip_count += 1
            continue
        log.info(f"Found {len(emails)} email(s): {emails}")

        # Generate slides
        pptx_bytes = generate_slides(pdf_bytes, paper["title"])
        if not pptx_bytes:
            log.warning(f"Slide generation failed, skipping email")
            skip_count += 1
            continue

        # Send to first author email only (avoid spamming all co-authors)
        primary_email = emails[0]
        first_name = guess_first_name(primary_email)

        success = send_email(
            to_email=primary_email,
            author_first_name=first_name,
            paper_title=paper["title"],
            paper_url=paper["abs_url"],
            pptx_bytes=pptx_bytes,
        )
        if success:
            sent_count += 1

        # Be polite — don't hammer APIs
        time.sleep(5)

    log.info(f"=== Done. Sent: {sent_count}, Skipped: {skip_count} ===")


if __name__ == "__main__":
    run()
