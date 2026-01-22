
from bs4 import BeautifulSoup
import re

with open("snabbare_home.html", "r", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, "html.parser")
links = soup.find_all("a")

seen = set()
for l in links:
    href = l.get("href", "")
    text = l.get_text(strip=True)
    if "sportsbook" in href:
        if href not in seen:
            print(f"Link: {text} -> {href}")
            seen.add(href)

# Also look for anything that looks like a sport ID map in scripts
scripts = soup.find_all("script")
for s in scripts:
    if s.string and "sportId" in s.string:
        print("Found script with sportId references (truncated):")
        print(s.string[:500])
