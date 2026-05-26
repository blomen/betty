"""One-shot: fetch all @AnonSportsConsulting transcripts → docs/knowledge/anon-sports-consulting-raw.md."""

from __future__ import annotations

import sys
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi

VIDEOS = [
    ("U3XsaNWQgpk", "How to Bet on Esports and Where (Full Guide)"),
    ("ozh8wSKHHvc", "Anon Sports - Rich Off Sports (Lyrics)"),
    ("8GomGM7ndZU", "NFL Betting Breakdown: Strategies for Sides, Totals, and Props"),
    ("RfmizgXagm4", "Is Sports Betting Rigged? The Truth You NEED to Hear"),
    (
        "sx5UdlkxQzc",
        "The Secret to Winning Over/Under Bets (Even If You're New to Sports Betting)",
    ),
    ("ONORdAzgjrw", "The ONE Betting Strategy I Wish I Knew Sooner"),
    ("Fub5H1mS0n4", "Bankroll Management Like a Pro - Stop Going Broke Betting Sports"),
    ("CXoWNbJrjTI", "The Psychology of Sports Betting - How the Books Trick You"),
    ("tAr0hLzr3pQ", "How I Use Public Betting Data to Find Winning Picks"),
    ("Su5pZ6fnDpw", "MLB Betting Strategies You Should Be Using (But Probably Aren't)"),
    ("lNERIkislJY", "Public vs Sharp Money: How to Track and React to Betting Action"),
    ("EwhmXnrwM0w", "Why 95% of Sports Bettors Lose (And How Not to Be One of Them)"),
    ("U3KAUWumEdE", "What Bookmakers Don't Want You To Know"),
    ("At-N2QFE4zk", "Betting Psychology: How to Master Your Emotions and Avoid Tilt"),
    ("qrs5lh4VShk", "Understanding Value Bets: How Sharps Win Long-Term"),
    ("0AGf2-8zM9M", "How Odds Are Made: Inside the Mind of a Sportsbook"),
    ("3sv_By_H1to", "I Got Rich From Sports Betting When I Applied These 6 Habits"),
    ("RWpmepPAZWE", "Beginner's Guide To Sports Betting On The MLB (FREE COURSE)"),
]

OUT = Path("docs/knowledge/anon-sports-consulting-raw.md")
api = YouTubeTranscriptApi()

lines = [
    "# @AnonSportsConsulting - Raw Video Transcripts",
    "",
    "Source: https://www.youtube.com/@AnonSportsConsulting",
    f"Videos: {len(VIDEOS)}",
    "",
    "---",
    "",
]

for vid, title in VIDEOS:
    print(f"Fetching {vid} - {title}", file=sys.stderr)
    try:
        fetched = api.fetch(vid)
        text_parts = [snip.text.replace("\n", " ") for snip in fetched]
        body = " ".join(text_parts).replace("  ", " ").strip()
        lines.append(f"## {title}")
        lines.append(f"**Video:** https://www.youtube.com/watch?v={vid}")
        lines.append("")
        # Paragraph wrap: break every ~30 segments
        chunk = []
        per_para = 25
        for i, snip in enumerate(fetched):
            chunk.append(snip.text.replace("\n", " "))
            if len(chunk) >= per_para:
                lines.append(" ".join(chunk).strip())
                lines.append("")
                chunk = []
        if chunk:
            lines.append(" ".join(chunk).strip())
            lines.append("")
        lines.append("---")
        lines.append("")
    except Exception as exc:
        print(f"  FAIL: {exc}", file=sys.stderr)
        lines.append(f"## {title}")
        lines.append(f"**Video:** https://www.youtube.com/watch?v={vid}")
        lines.append(f"_Transcript unavailable: {exc}_")
        lines.append("")
        lines.append("---")
        lines.append("")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)", file=sys.stderr)
