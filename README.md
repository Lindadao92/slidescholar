# SlideScholar

Upload a research paper. Get a presentation-ready slide deck.

**Live:** https://slidescholar.vercel.app  
**Built:** 2025

---

## What it does

- Upload any research paper as a PDF
- AI parses structure, extracts key findings, and generates slides
- Built-in conference agent: runs daily via GitHub Actions, finds 
  relevant conference papers, and sends outreach emails automatically
- Freemium model with Stripe integration

## Stack

Next.js · FastAPI · Railway · Stripe · Vercel · Python · GitHub Actions

## Conference Agent

An autonomous agent (`.github/workflows/conference_agent.yml`) that:
- Runs every day at 10:00 AM UTC via cron
- Finds new conference papers matching SlideScholar's use case
- Sends personalized outreach emails via Gmail
- Maintains a sent-papers cache to avoid duplicates
- Built with Python 3.11, runs entirely on GitHub Actions — no server needed

## Background

78 commits, 157 deployments. Built and iterated actively throughout 2025.
Co-built with Claude Code.
