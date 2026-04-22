#!/usr/bin/env python3
"""
LinkedIn Content Pipeline — Senior Frontend Engineer Edition
============================================================
Runs every Sunday via GitHub Actions.
Steps:
  1. Scrape top frontend engineering articles from frontendcs.com
  2. Score each article via Claude API for relevance
  3. Fetch full content via Jina AI Reader
  4. Generate a LinkedIn post per article via Claude API
  5. Save all 10 posts to a Notion database
  6. Send a Telegram summary notification
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import anthropic
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
NOTION_API_KEY      = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID  = os.environ.get("NOTION_DATABASE_ID", "")
TELEGRAM_BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CLAUDE_MODEL = "claude-sonnet-4-20250514"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# Publish day schedule — 10 slots across ~3.5 weeks (Mon/Wed/Fri cadence)
PUBLISH_DAYS = [
    "Mon", "Wed", "Fri",
    "Mon2", "Wed2", "Fri2",
    "Mon3", "Wed3", "Fri3",
    "Mon4",
]

# ---------------------------------------------------------------------------
# LinkedIn ghostwriter system prompt
# ---------------------------------------------------------------------------
LINKEDIN_SYSTEM_PROMPT = """\
You are a ghostwriter for Anshu, a Senior Frontend Engineer with 6+ years of experience building at 50M+ monthly users scale at a major news platform. Stack: Next.js 15, React 19, TypeScript, Akamai CDN, Redis, MongoDB.

Goal: attract inbound from CTOs, VPs Eng, Hiring Managers at US/EU remote-first startups, especially YC-backed companies.

ANSHU'S POSITIONING:
Intersection of Senior Engineer credibility and Tech Lead decision-making. Not tutorials. Not career advice. What it actually means to build and think at scale — architecture tradeoffs, failure modes, decisions under constraints, lessons that only come from real production systems.

Target reaction from a CTO at a 200-person startup:
"This person thinks the way my senior engineers think. I want them."

AUDIENCE:
Primary: CTOs, VPs Eng, Hiring Managers at remote-first US/EU startups
Secondary: Senior and Staff engineers who follow for the insights

CONTENT PILLARS:
1. War Story Breakdown — What a real team faced, decided, learned at scale
2. The Scale Lens — How this looks from inside a real 50M-user system
3. Senior Engineer Mental Models — Decision frameworks, tradeoff thinking

STYLE REFERENCE — BLEND THESE 5:
1. RYAN PETERMAN (Staff, Instagram/Meta)
   Steal: specific metric hooks, level-contrast thinking (junior fix vs senior fix vs staff fix), zero corporate language
2. GERGELY OROSZ (Pragmatic Engineer)
   Steal: real company situation opener, decision-as-tradeoff framing, "most teams vs best teams" contrast, readable by non-coding CTOs
3. MAXI FERREIRA (Frontend at Scale, Staff)
   Steal: architecture thinking without jargon, showing full tradeoff space, respecting reader intelligence, calm confident tone
4. CALEB MELLAS (Staff)
   Steal: relatable pain point opener, personal experience woven naturally, force-multiplier framing, specific debate-opening CTA
5. JORDAN CUTLER (Senior, Pinterest)
   Steal: counterintuitive one-liner hooks, radical compression, sharp observations not essays

COMBINED VOICE RULES:
- Specific over generic. "50M users" beats "at scale."
- Contrast thinking: most engineers do X. Real scale engineers do Y.
- Tech Lead lens: not just what we did, but why that call was hard
- Personal grounding: use "we hit this too", "our team faced this exact wall", "we tried the other approach first" — implies real scars without naming employer. NEVER say Livemint or HT Digital.
- Calm confidence. Not trying to go viral.
- Zero fluff. No "In today's fast-paced world."
- Never sounds like AI wrote it.
- No em dashes in post body
- No buzzwords: game-changer, innovative, leverage, utilize, delve, supercharge, skyrocket, unlock

LINKEDIN POST FORMAT:
[HOOK — 1 line. The actual insight, not a setup for the insight. Counterintuitive, a specific number, or a precise reframe. Never a question. Never starts with "I".]

[CONTEXT — 2-3 short lines. Real company, real problem, real scale. Numbers whenever possible.]

[BREAKDOWN — 4-6 lines. Each line = one idea with full weight. Short bullets if it aids clarity. This is judgment on the decision, not narration of what happened.]

[YOUR LENS — 2-3 lines. One "we hit this too" line implying real production experience. Never name the employer. Use "we", "our team", "the system we built". Then: the transferable mental model this whole thing illustrates.]

[CTA — 1 question with no obvious right answer. Forces a tradeoff reveal. Makes smart people want to defend a position. Not "what do you think?" Not answerable in 3 words.]

[HASHTAGS — exactly 3 from: #frontendengineering #webperformance #systemdesign #reactjs #nextjs #softwaredevelopment #engineeringculture #techlead]

POST LENGTH: 1000-1300 characters. Dense with signal.

POST QUALITY BAR — MUST PASS ALL 7 BEFORE OUTPUTTING:
1. HOOK TEST: Would a CTO stop scrolling at line 1? If the hook is a setup for the real insight, rewrite it. The real insight IS the hook.
2. NARRATION TEST: Does any sentence just retell what the company did? Replace with judgment — why the decision was hard, what breaks if you get it wrong, what it means for other teams.
3. CREDIBILITY TEST: Is there one "we hit this too" line specific enough that a CTO thinks "this person has scars from this"? Vague empathy does not count.
4. QUOTABILITY TEST: Is there one line a senior engineer would screenshot? A transferable principle with a specific scope.
5. CTA TEST: Does the CTA have no obvious right answer? If it can be answered in 3 words, rewrite it.
6. PRECISION TEST: No absolute statements unless scoped. Overconfident claims trigger silent skepticism from CTOs.
7. COMPANY NAME TEST: Zero employer names. Livemint, HT Digital never appear. "We", "our team" always.

OUTPUT: One LinkedIn post. No preamble. No explanation. Just the post.\
"""


# ===========================================================================
# STEP 1 — SCRAPE ARTICLES FROM FRONTENDCS.COM
# ===========================================================================

def _extract_company_from_url(url: str) -> str:
    """Best-effort company name from a URL."""
    try:
        netloc = urlparse(url).netloc.replace("www.", "")
        return netloc.split(".")[0].capitalize()
    except Exception:
        return "Unknown"


def _scrape_source(name: str, url: str, now: datetime, cutoff: datetime) -> list[dict]:
    """Scrape articles from a single engineering blog."""
    articles: list[dict] = []

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LinkedInPipeline/1.0)"},
            timeout=30,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Try to find article links
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)

            # Skip if not a valid article link (too short, navigation, etc.)
            if not href.startswith("http") or len(text) < 20:
                continue
            if any(skip in href for skip in ["/tag/", "/category/", "/author/", "#"]):
                continue

            # Extract company from URL
            company = _extract_company_from_url(href)

            articles.append({
                "title": text,
                "company": company,
                "url": href,
                "date": now.isoformat(),
                "source": name,
            })

            if len(articles) >= 10:  # Max 10 per source
                break

    except Exception as exc:
        logger.debug(f"  Failed to scrape {name}: {exc}")

    return articles


def _scrape_aggregators(now: datetime, cutoff: datetime) -> list[dict]:
    """Scrape from aggregator sites that link to engineering content."""
    articles: list[dict] = []

    aggregators = [
        "https://news.ycombinator.com",
        "https://www.reddit.com/r/programming",
    ]

    for agg_url in aggregators:
        try:
            resp = requests.get(
                agg_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; LinkedInPipeline/1.0)"},
                timeout=30,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            # HN uses "titleline" class, Reddit uses "a" tags in posts
            for item in soup.select(".titleline a, .post a"):
                href = item.get("href", "")
                text = item.get_text(strip=True)

                if not href.startswith("http") or len(text) < 20:
                    continue
                # Filter for engineering-related domains
                eng_domains = ["engineering", "techblog", "dev.to", "medium.com", "blog."]
                if not any(d in href for d in eng_domains):
                    continue

                articles.append({
                    "title": text,
                    "company": _extract_company_from_url(href),
                    "url": href,
                    "date": now.isoformat(),
                    "source": "aggregator",
                })

                if len(articles) >= 20:
                    break

        except Exception as exc:
            logger.debug(f"  Failed to scrape {agg_url}: {exc}")

    return articles


# Engineering blogs to scrape (verified accessible)
SOURCES = [
    {"name": "frontendcs", "url": "https://frontendcs.com"},
    {"name": "engineering.fb", "url": "https://engineering.fb.com"},
    {"name": "netflixtechblog", "url": "https://netflixtechblog.com"},
    {"name": "shopify.engineering", "url": "https://shopify.engineering"},
    {"name": "vercel.blog", "url": "https://vercel.com/blog"},
    {"name": "pragmaticengineer", "url": "https://pragmaticengineer.com"},
    {"name": "stripe.blog", "url": "https://stripe.com/blog/engineering"},
    {"name": "airbnb.blog", "url": "https://airbnb.io/blog"},
]


def scrape_frontend_articles() -> list[dict]:
    """
    Fetch frontend case studies from the GitHub repo README:
    https://github.com/andrew--r/frontend-case-studies
    Parse markdown for article links and metadata.
    """
    logger.info("=" * 60)
    logger.info("STEP 1 — Scraping articles from GitHub frontend-case-studies")
    logger.info("=" * 60)

    GITHUB_RAW_URL = "https://raw.githubusercontent.com/andrew--r/frontend-case-studies/master/README.md"
    now = datetime.now(timezone.utc)
    articles: list[dict] = []

    try:
        resp = requests.get(GITHUB_RAW_URL, timeout=30)
        resp.raise_for_status()
        md = resp.text

        # Each case study is a markdown list item with a link, e.g.:
        # - [Airbnb: Lottie](https://airbnb.design/lottie/)
        for line in md.splitlines():
            if line.startswith("- ["):
                # Extract [Title](URL)
                try:
                    title_start = line.index("[") + 1
                    title_end = line.index("]", title_start)
                    url_start = line.index("(", title_end) + 1
                    url_end = line.index(")", url_start)
                    title = line[title_start:title_end].strip()
                    url = line[url_start:url_end].strip()
                    # Try to extract company from title (e.g. "Airbnb: Lottie")
                    if ":" in title:
                        company, case_title = title.split(":", 1)
                        company = company.strip()
                        case_title = case_title.strip()
                    else:
                        company = _extract_company_from_url(url)
                        case_title = title
                    articles.append({
                        "title": case_title,
                        "company": company,
                        "url": url,
                        "date": now.isoformat(),
                        "source": "github-frontend-case-studies",
                    })
                except Exception as exc:
                    logger.debug(f"Failed to parse line: {line} — {exc}")
            if len(articles) >= 40:
                break
    except Exception as exc:
        logger.error(f"Failed to fetch or parse GitHub README: {exc}")

    logger.info(f"Candidate articles collected: {len(articles)}")
    if not articles:
        logger.warning("No articles found — using fallback seed list")
        articles = _fallback_seed_articles(now)
    return articles[:40]


def _parse_article_item(
    item, now: datetime, cutoff: datetime
) -> Optional[dict]:
    """Extract title, company, URL, date from a single BeautifulSoup element."""
    # Title
    title_el = item.find(["h1", "h2", "h3", "h4"])
    if not title_el:
        title_el = item.find("a")
    title = title_el.get_text(strip=True) if title_el else ""
    if not title or len(title) < 8:
        return None

    # URL
    link_el = item.find("a", href=True)
    if not link_el:
        return None
    href = link_el["href"]
    if href.startswith("/"):
        href = f"https://frontendcs.com{href}"
    elif not href.startswith("http"):
        return None

    # Company
    company = ""
    for comp_sel in [".company", ".source", ".publisher", ".site", ".tag", "span", "small"]:
        el = item.select_one(comp_sel)
        if el:
            text = el.get_text(strip=True)
            if text and text != title:
                company = text
                break
    if not company:
        company = _extract_company_from_url(href)

    # Date
    article_date = now
    date_el = item.find("time") or item.select_one(".date, .published, [class*='date']")
    if date_el:
        raw = date_el.get("datetime", "") or date_el.get_text(strip=True)
        for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
            try:
                article_date = datetime.strptime(raw[:20].strip(), fmt).replace(
                    tzinfo=timezone.utc
                )
                break
            except ValueError:
                continue

    if article_date.tzinfo is None:
        article_date = article_date.replace(tzinfo=timezone.utc)

    # Filter: must be within 6 months and in current/previous year
    if article_date < cutoff:
        return None
    if article_date.year not in {now.year, now.year - 1}:
        return None

    return {
        "title": title,
        "company": company,
        "url": href,
        "date": article_date.isoformat(),
    }


def _harvest_links(soup: BeautifulSoup, now: datetime, cutoff: datetime) -> list[dict]:
    """Collect all external links from the page as article candidates."""
    skip_domains = {"frontendcs.com", "twitter.com", "x.com", "linkedin.com", "facebook.com"}
    seen_urls: set[str] = set()
    articles: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if not href.startswith("http") or not text or len(text) < 10:
            continue
        domain = urlparse(href).netloc.replace("www.", "")
        if domain in skip_domains or href in seen_urls:
            continue
        seen_urls.add(href)
        articles.append(
            {
                "title": text,
                "company": _extract_company_from_url(href),
                "url": href,
                "date": now.isoformat(),
            }
        )
        if len(articles) >= 40:
            break
    return articles


def _fallback_seed_articles(now: datetime) -> list[dict]:
    """Hard-coded seed articles used only if all scraping fails."""
    ts = now.isoformat()
    return [
        {"title": "Rebuilding Netflix.com with Modern Web Tech", "company": "Netflix", "url": "https://netflixtechblog.com", "date": ts},
        {"title": "How Shopify Reduced Storefront Response Times", "company": "Shopify", "url": "https://shopify.engineering", "date": ts},
        {"title": "Scaling Airbnb's Frontend Infrastructure", "company": "Airbnb", "url": "https://medium.com/airbnb-engineering", "date": ts},
        {"title": "Migrating to Next.js at Scale", "company": "Vercel", "url": "https://vercel.com/blog", "date": ts},
        {"title": "Web Performance at the Edge: Akamai Learnings", "company": "Akamai", "url": "https://www.akamai.com/blog", "date": ts},
        {"title": "React Server Components in Production", "company": "Meta", "url": "https://engineering.fb.com", "date": ts},
        {"title": "TypeScript at Scale: Lessons from 10M LOC", "company": "Microsoft", "url": "https://devblogs.microsoft.com", "date": ts},
        {"title": "Frontend Observability: What We Track at 50M MAU", "company": "Pinterest", "url": "https://medium.com/pinterest-engineering", "date": ts},
        {"title": "Rendering Strategies at Scale: SSR vs ISR vs CSR", "company": "Vercel", "url": "https://vercel.com/blog", "date": ts},
        {"title": "How We Cut Time-to-Interactive by 40% on Mobile", "company": "Google", "url": "https://web.dev", "date": ts},
    ]


# ===========================================================================
# STEP 2 — SCORE ARTICLES VIA CLAUDE API
# ===========================================================================

def score_articles(articles: list[dict]) -> list[dict]:
    """
    Score each article 1-10 for relevance to a Senior Frontend Engineer.
    Sort descending and return the top 10.
    """
    logger.info("=" * 60)
    logger.info(f"STEP 2 — Scoring {len(articles)} articles via Claude API")
    logger.info("=" * 60)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    scored: list[dict] = []

    for i, article in enumerate(articles, 1):
        logger.info(f"  [{i}/{len(articles)}] Scoring: {article['title'][:70]}")
        try:
            prompt = (
                "Score this frontend engineering article 1-10 for how relevant it is "
                "to a Senior Frontend Engineer who writes about performance, scale, "
                "architecture decisions, and AI in engineering.\n\n"
                "High scores (8-10): real war stories, production scale problems, "
                "architecture decisions at companies with millions of users, "
                "performance engineering, frontend at scale, AI integration in real systems.\n\n"
                "Low scores (1-4): tutorials, career advice, CSS tricks, beginner content, "
                "tooling comparisons with no scale context.\n\n"
                f"Article title: {article['title']}\n"
                f"Company: {article['company']}\n\n"
                'Reply with ONLY a JSON object: {"score": 8, "reason": "one line"}'
            )

            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=120,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Strip optional markdown fences
            if "```" in raw:
                parts = raw.split("```")
                raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

            result = json.loads(raw)
            score = max(1, min(10, int(result.get("score", 5))))
            reason = str(result.get("reason", ""))
            logger.info(f"      Score: {score}/10 — {reason}")

        except json.JSONDecodeError as exc:
            logger.warning(f"      JSON parse failed: {exc}. Defaulting to 5.")
            score, reason = 5, "Parse failed"
        except Exception as exc:
            logger.warning(f"      Claude scoring error: {exc}. Defaulting to 5.")
            score, reason = 5, "Scoring failed"

        article["score"] = score
        article["score_reason"] = reason
        scored.append(article)

    scored.sort(key=lambda a: a["score"], reverse=True)
    top10 = scored[:10]
    logger.info(f"Top-10 scores: {[a['score'] for a in top10]}")
    return top10


# ===========================================================================
# STEP 3 — FETCH FULL ARTICLE CONTENT VIA JINA AI READER
# ===========================================================================

def fetch_article_content(articles: list[dict]) -> list[dict]:
    """
    Retrieve full article text via https://r.jina.ai/<url>.
    Truncate to 4 000 characters. Fall back to title+company on error.
    """
    logger.info("=" * 60)
    logger.info("STEP 3 — Fetching full content via Jina AI Reader")
    logger.info("=" * 60)

    for i, article in enumerate(articles, 1):
        logger.info(f"  [{i}/10] Fetching: {article['url']}")
        try:
            jina_url = f"https://r.jina.ai/{article['url']}"
            resp = requests.get(
                jina_url,
                headers={"Accept": "text/plain"},
                timeout=30,
            )
            resp.raise_for_status()
            full_text = resp.text
            article["content"] = full_text[:4000]
            logger.info(f"      OK — {len(full_text):,} chars fetched, truncated to 4 000")
        except Exception as exc:
            logger.warning(f"      Jina fetch failed: {exc}. Using fallback.")
            article["content"] = (
                f"Article Title: {article['title']}\n"
                f"Company: {article['company']}\n"
                "[Full content unavailable — using title+company as context]"
            )

    return articles


# ===========================================================================
# STEP 4 — GENERATE LINKEDIN POSTS VIA CLAUDE API
# ===========================================================================

def generate_linkedin_posts(articles: list[dict]) -> list[dict]:
    """Generate one LinkedIn post per article using the ghostwriter system prompt."""
    logger.info("=" * 60)
    logger.info("STEP 4 — Generating LinkedIn posts via Claude API")
    logger.info("=" * 60)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    for i, article in enumerate(articles, 1):
        logger.info(f"  [{i}/10] Generating post for: {article['title'][:70]}")
        try:
            user_message = (
                f"Article Title: {article['title']}\n"
                f"Company: {article['company']}\n"
                f"Article URL: {article['url']}\n"
                f"Article Content: {article.get('content', article['title'])}"
            )

            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1200,
                system=LINKEDIN_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )

            post_text = response.content[0].text.strip()
            article["linkedin_post"] = post_text
            logger.info(f"      Generated — {len(post_text):,} chars")

        except Exception as exc:
            logger.error(f"      Post generation failed: {exc}")
            article["linkedin_post"] = "GENERATION_FAILED"

    return articles


# ===========================================================================
# STEP 5 — SAVE TO NOTION DATABASE
# ===========================================================================

def save_to_notion(articles: list[dict], week_of: str) -> dict:
    """
    Create one Notion page per article in the configured database.
    Continues on per-article failures and returns a success/failure tally.
    """
    logger.info("=" * 60)
    logger.info("STEP 5 — Saving posts to Notion")
    logger.info("=" * 60)

    results = {"success": 0, "failed": 0}

    # Rebuild headers each call so the bearer token is always fresh
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    for i, article in enumerate(articles):
        publish_day = PUBLISH_DAYS[i] if i < len(PUBLISH_DAYS) else f"Extra{i + 1}"
        logger.info(
            f"  [{i + 1}/10] Saving: {article['title'][:60]} — Slot: {publish_day}"
        )

        # Notion rich_text fields cap at 2 000 chars per block
        post_text = article.get("linkedin_post", "")[:2000]
        title_text = article["title"][:2000]
        company_text = article.get("company", "")[:2000]

        payload = {
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": {
                "Title": {
                    "title": [{"text": {"content": title_text}}]
                },
                "Company": {
                    "rich_text": [{"text": {"content": company_text}}]
                },
                "URL": {
                    "url": article["url"]
                },
                "Score": {
                    "number": article.get("score", 5)
                },
                "LinkedIn Post": {
                    "rich_text": [{"text": {"content": post_text}}]
                },
                "Publish Day": {
                    "select": {"name": publish_day}
                },
                "Week Of": {
                    "date": {"start": week_of}
                },
                "Status": {
                    "select": {"name": "Draft"}
                },
            },
        }

        try:
            resp = requests.post(
                "https://api.notion.com/v1/pages",
                headers=headers,
                json=payload,
                timeout=30,
            )
            if resp.status_code >= 400:
                logger.error(f"      Notion API error: {resp.status_code}")
                logger.error(f"      Response: {resp.text}")
                results["failed"] += 1
                return results
            resp.raise_for_status()
            results["success"] += 1
            logger.info(f"      Saved OK (Notion page id: {resp.json().get('id', '?')})")
        except Exception as exc:
            logger.error(f"      Notion save failed: {exc}")
            results["failed"] += 1

    logger.info(
        f"Notion results: {results['success']} saved, {results['failed']} failed"
    )
    return results


# ===========================================================================
# STEP 6 — TELEGRAM NOTIFICATION
# ===========================================================================

def send_telegram_notification(articles: list[dict], stats: dict) -> None:
    """Send a Telegram message summarising the week's pipeline run."""
    logger.info("=" * 60)
    logger.info("STEP 6 — Sending Telegram notification")
    logger.info("=" * 60)

    try:
        top3 = articles[:3]
        lines = [
            "Weekly posts ready. 10 drafts saved to Notion.",
            "",
            "Top articles this week:",
        ]
        for idx, a in enumerate(top3, 1):
            lines.append(
                f"{idx}. {a['title']} ({a.get('company', 'Unknown')}) "
                f"— Score: {a.get('score', '?')}/10"
            )
        lines += [
            "",
            "Review and approve in Notion before posting.",
        ]

        notion_failed = stats.get("notion_failed", 0)
        if notion_failed:
            lines.append(f"\n⚠️ {notion_failed} post(s) failed to save to Notion.")

        gen_failed = stats.get("gen_failed", 0)
        if gen_failed:
            lines.append(f"⚠️ {gen_failed} post(s) had GENERATION_FAILED status.")

        message = "\n".join(lines)

        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=30,
        )
        resp.raise_for_status()
        logger.info("Telegram notification sent successfully")

    except Exception as exc:
        logger.error(f"Telegram notification failed (non-fatal): {exc}")


# ===========================================================================
# MAIN
# ===========================================================================

def validate_env() -> None:
    """Exit early if any required secret is missing."""
    required = {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "NOTION_API_KEY": NOTION_API_KEY,
        "NOTION_DATABASE_ID": NOTION_DATABASE_ID,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)
    logger.info("All required environment variables are present.")


def main() -> None:
    logger.info("=" * 60)
    logger.info("LinkedIn Content Pipeline — Starting")
    logger.info(f"Run date (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    validate_env()

    week_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats: dict = {}

    # ------------------------------------------------------------------
    # Step 1 — Scrape
    # ------------------------------------------------------------------
    candidates = scrape_frontend_articles()
    if not candidates:
        logger.error("Zero candidate articles collected. Aborting.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2 — Score & select top 10
    # ------------------------------------------------------------------
    top10 = score_articles(candidates)
    stats["articles_scored"] = len(top10)

    # ------------------------------------------------------------------
    # Step 3 — Fetch full content
    # ------------------------------------------------------------------
    top10 = fetch_article_content(top10)

    # ------------------------------------------------------------------
    # Step 4 — Generate LinkedIn posts
    # ------------------------------------------------------------------
    top10 = generate_linkedin_posts(top10)
    stats["gen_failed"] = sum(
        1 for a in top10 if a.get("linkedin_post") == "GENERATION_FAILED"
    )

    # ------------------------------------------------------------------
    # Step 5 — Save to Notion
    # ------------------------------------------------------------------
    notion_results = save_to_notion(top10, week_of)
    stats["notion_success"] = notion_results["success"]
    stats["notion_failed"] = notion_results["failed"]

    # ------------------------------------------------------------------
    # Step 6 — Telegram notification
    # ------------------------------------------------------------------
    send_telegram_notification(top10, stats)

    # ------------------------------------------------------------------
    # Final summary log
    # ------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("Pipeline Complete — Run Summary")
    logger.info(f"  Week of:           {week_of}")
    logger.info(f"  Candidates found:  {len(candidates)}")
    logger.info(f"  Articles selected: {stats['articles_scored']}/10")
    logger.info(f"  Posts generated:   {10 - stats['gen_failed']}/10")
    logger.info(f"  Notion saves:      {stats['notion_success']} OK / {stats['notion_failed']} FAILED")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
