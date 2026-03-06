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
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import feedparser
import fitz  # PyMuPDF

# ── Config from environment variables ────────────────────────────────────────
SLIDESCHOLAR_API   = os.environ["SLIDESCHOLAR_API_URL"]
GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
PAPERS_PER_RUN     = int(os.getenv("PAPERS_PER_RUN", "5"))
TALK_LENGTH        = os.getenv("TALK_LENGTH", "conference")
LOG_LEVEL          = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── arXiv categories to monitor ──────────────────────────────────────────────
ARXIV_FEEDS = [
    "https://rss.arxiv.org/rss/cs.AI",
    "https://rss.arxiv.org/rss/cs.LG",
    "https://rss.arxiv.org/rss/q-bio",
    "https://rss.arxiv.org/rss/physics.med-ph",
]

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def parse_arxiv_id(raw_id: str) -> str:
    """Parse arxiv ID from various formats."""
    if "/abs/" in raw_id:
        arxiv_id = raw_id.split("/abs/")[-1].strip()
    elif "arXiv.org:" in raw_id:
        arxiv_id = raw_id.split("arXiv.org:")[-1].strip()
    else:
        arxiv_id = raw_id.strip()
    # Remove version suffix like v1, v2
    arxiv_id = re.sub(r'v\d+$', '', arxiv_id)
    return arxiv_id


def fetch_arxiv_papers(max_papers: int) -> list:
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
                arxiv_id = parse_arxiv_id(raw_id)
                if not arxiv_id or arxiv_id in seen_ids:
                    continue
                seen_ids.add(arxiv_id)
                papers.append({
                    "id":      arxiv_id,
                    "title":   entry.get("title", "").replace("\n", " ").strip(),
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                    "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
                })
        except Exception as e:
            log.warning(f"Failed to fetch {feed_url}: {e}")

    log.info(f"Fetched {len(papers)} papers from arXiv")
    return papers


def extract_emails_from_pdf(pdf_bytes: bytes) -> list:
    """Extract author emails from the first 2 pages of a PDF."""
    emails = []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_num in range(min(2, len(doc))):
                text = doc[page_num].get_text()
                found = EMAIL_REGEX.findall(text)
                emails.extend(found)
    except Exception as e:
        log.warning(f"PDF email extraction failed: {e}")

    seen = set()
    clean = []
    for email in emails:
        email = email.lower().strip(".")
        if email in seen:
            continue
        seen.add(email)
        if any(skip in email for skip in ["example.", "arxiv.", "latex", "sty@", ".sty"]):
            continue
        clean.append(email)

    return clean


def generate_slides(pdf_bytes: bytes, title: str):
    """Call SlideScholar API to parse PDF and generate .pptx."""
    try:
        # Step 1: Parse — returns paper_id
        log.info(f"Parsing PDF for: {title[:60]}")
        parse_resp = requests.post(
            f"{SLIDESCHOLAR_API}/api/parse",
            files={"file": ("paper.pdf", pdf_bytes, "application/pdf")},
            timeout=60,
        )
        parse_resp.raise_for_status()
        parse_data = parse_resp.json()
        paper_id = parse_data.get("paper_id")
        if not paper_id:
            log.error(f"No paper_id returned from /api/parse. Response: {parse_data}")
            return None

        # Step 2: Generate — returns job_id (async)
        log.info(f"Generating slides for paper_id={paper_id}")
        gen_resp = requests.post(
            f"{SLIDESCHOLAR_API}/api/generate",
            json={
                "paper_id": paper_id,
                "talk_length": TALK_LENGTH,
                "include_speaker_notes": True,
                "include_backup_slides": False,
            },
            timeout=30,
        )
        gen_resp.raise_for_status()
        job_id = gen_resp.json().get("job_id")
        if not job_id:
            log.error(f"No job_id returned from /api/generate. Response: {gen_resp.json()}")
            return None

        # Step 3: Poll /api/jobs/{job_id} until done
        log.info(f"Polling job {job_id[:8]}...")
        for attempt in range(60):  # up to 5 minutes
            time.sleep(5)
            poll_resp = requests.get(
                f"{SLIDESCHOLAR_API}/api/jobs/{job_id}",
                timeout=10,
            )
            poll_resp.raise_for_status()
            poll_data = poll_resp.json()
            status = poll_data.get("status")
            log.info(f"  Job status: {status} (attempt {attempt + 1})")

            if status == "done":
                download_url = poll_data.get("download_url")
                if not download_url:
                    log.error("No download_url in completed job")
                    return None
                # Step 4: Download the .pptx
                dl_resp = requests.get(
                    f"{SLIDESCHOLAR_API}{download_url}",
                    timeout=30,
                )
                dl_resp.raise_for_status()
                return dl_resp.content

            elif status == "error":
                log.error(f"Job failed: {poll_data.get('detail')}")
                return None

        log.error("Job timed out after 5 minutes")
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
    subject = "I turned your paper into slides — free to use"
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
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        attachment = MIMEApplication(pptx_bytes, Name=filename)
        attachment["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(attachment)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, to_email, msg.as_string())

        log.info(f"Email sent to {to_email}")
        return True

    except Exception as e:
        log.error(f"Failed to send email to {to_email}: {e}")
        return False


def guess_first_name(email: str) -> str:
    """Best-effort first name from email address."""
    local = email.split("@")[0]
    parts = re.split(r'[._\-]', local)
    name = parts[0] if len(parts) >= 2 else local
    return name.capitalize() if len(name) > 1 else "there"


def run():
    log.info("=== SlideScholar arXiv Agent starting ===")
    papers = fetch_arxiv_papers(PAPERS_PER_RUN)

    sent_count = 0
    skip_count = 0

    for paper in papers:
        log.info(f"Processing: {paper['title'][:70]}")
        paper['title'] = paper['title'].replace('\xa0', ' ').encode('ascii', 'ignore').decode('ascii')

        try:
            pdf_resp = requests.get(paper["pdf_url"], timeout=30)
            pdf_resp.raise_for_status()
            pdf_bytes = pdf_resp.content
        except Exception as e:
            log.warning(f"Could not download PDF: {e}")
            skip_count += 1
            continue

        emails = extract_emails_from_pdf(pdf_bytes)
        if not emails:
            log.info(f"No emails found in PDF, skipping: {paper['title'][:60]}")
            skip_count += 1
            continue
        log.info(f"Found {len(emails)} email(s): {emails}")

        pptx_bytes = generate_slides(pdf_bytes, paper["title"])
        if not pptx_bytes:
            log.warning("Slide generation failed, skipping email")
            skip_count += 1
            continue

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

        time.sleep(5)

    log.info(f"=== Done. Sent: {sent_count}, Skipped: {skip_count} ===")


if __name__ == "__main__":
    run()
