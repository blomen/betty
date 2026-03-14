# backend/src/providers/comeon_dom_js.py
"""
JavaScript evaluation snippets for ComeOn DOM scraping.

All page.evaluate() strings used by the ComeOn DOM scraper.
Kept in one module to separate JS from Python logic.
"""

# Expand all country accordions on the /leagues directory page
JS_EXPAND_ALL_COUNTRIES = """() => {
    const wrappers = document.querySelectorAll('li[data-expanded="false"]');
    let clicked = 0;
    for (const wrapper of wrappers) {
        const btn = wrapper.querySelector('button');
        if (btn) {
            btn.click();
            clicked++;
        }
    }
    return clicked;
}"""

# Collect all league URLs from the expanded league directory
JS_COLLECT_LEAGUE_URLS = """() => {
    const leagues = [];
    const seen = new Set();
    document.querySelectorAll('a[href*="/leagues/"]').forEach(a => {
        const href = a.getAttribute('href');
        const match = href.match(/\\/leagues\\/(\\d+)-(.+?)(?:\\/|$|\\?)/);
        if (match && !seen.has(match[1])) {
            seen.add(match[1]);
            leagues.push({
                id: parseInt(match[1]),
                name: a.textContent.trim(),
                href: href.split('?')[0]
            });
        }
    });
    return leagues;
}"""

# Parse all game cards on a league page into structured data
JS_PARSE_GAME_CARDS = """() => {
    const cards = document.querySelectorAll('[data-at="game-card"]');
    const events = [];

    for (const card of cards) {
        const scoreRow = card.querySelector('[class*="ScoreRow"]');
        if (scoreRow) continue;

        const link = card.querySelector('a[data-at="link-to-event"]');
        if (!link) continue;
        const href = link.getAttribute('href') || '';
        const idMatch = href.match(/\\/events\\/(\\d+)/);
        if (!idMatch) continue;

        const participants = card.querySelectorAll('small[class*="Participant"]');
        const teams = [];
        const seenTeams = new Set();
        for (const p of participants) {
            const name = p.textContent.trim();
            if (name && !seenTeams.has(name)) {
                seenTeams.add(name);
                teams.push(name);
            }
        }
        if (teams.length < 2) continue;

        const timeEl = card.querySelector('[class*="game-card-time"]');
        const timeText = timeEl ? timeEl.textContent.trim() : '';

        const oddsBtns = card.querySelectorAll('button[data-at="sportsbook-selection-btn"]');
        const odds = [];
        for (const btn of oddsBtns) {
            const label = btn.getAttribute('aria-label');
            if (label) odds.push(label);
        }

        events.push({
            eventId: idMatch[1],
            home: teams[0],
            away: teams[1],
            timeText: timeText,
            odds: odds
        });
    }
    return events;
}"""

# Get all market pill texts on the current league page
JS_GET_MARKET_PILLS = """() => {
    const pills = [];
    document.querySelectorAll('[class*="pill__Wrapper"]').forEach(pill => {
        const text = pill.textContent.trim();
        if (text) pills.push(text);
    });
    return pills;
}"""

# Click a market pill by text content
JS_CLICK_PILL = """(targetText) => {
    const pills = document.querySelectorAll('[class*="pill__Wrapper"]');
    for (const pill of pills) {
        if (pill.textContent.trim() === targetText) {
            const btn = pill.closest('button') || pill;
            btn.click();
            return true;
        }
    }
    return false;
}"""

# Get only the odds aria-labels from game cards (after tab switch)
JS_GET_CARD_ODDS = """() => {
    const cards = document.querySelectorAll('[data-at="game-card"]');
    const result = {};
    for (const card of cards) {
        if (card.querySelector('[class*="ScoreRow"]')) continue;
        const link = card.querySelector('a[data-at="link-to-event"]');
        if (!link) continue;
        const href = link.getAttribute('href') || '';
        const idMatch = href.match(/\\/events\\/(\\d+)/);
        if (!idMatch) continue;

        const oddsBtns = card.querySelectorAll('button[data-at="sportsbook-selection-btn"]');
        const odds = [];
        for (const btn of oddsBtns) {
            const label = btn.getAttribute('aria-label');
            if (label) odds.push(label);
        }
        result[idMatch[1]] = odds;
    }
    return result;
}"""
