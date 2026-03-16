# backend/src/providers/comeon_dom_js.py
"""
JavaScript evaluation snippets for ComeOn DOM scraping.

All page.evaluate() strings used by the ComeOn DOM scraper.
Kept in one module to separate JS from Python logic.
"""

# Count how many country accordions exist on the /leagues directory page
JS_GET_COUNTRY_COUNT = """() => {
    return document.querySelectorAll('li[data-expanded]').length;
}"""

# Click a specific country accordion by index (0-based)
JS_CLICK_COUNTRY_AT_INDEX = """(index) => {
    const wrappers = document.querySelectorAll('li[data-expanded]');
    if (index >= wrappers.length) return false;
    const btn = wrappers[index].querySelector('button');
    if (btn) { btn.click(); return true; }
    return false;
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
        // Skip live events: pre-match cards have UpcomingGameTime, live ones don't
        const upcomingTime = card.querySelector('[class*="UpcomingGameTime"]');
        if (!upcomingTime) continue;

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

# Get market tab pill texts (not navigation pills)
# Market pills have parent <button>, nav pills have parent <a>
JS_GET_MARKET_PILLS = """() => {
    const pills = [];
    document.querySelectorAll('[class*="pill__Wrapper"]').forEach(pill => {
        if (pill.parentElement && pill.parentElement.tagName === 'BUTTON') {
            const text = pill.textContent.trim();
            if (text) pills.push(text);
        }
    });
    return pills;
}"""

# Click a market tab pill by text content (only button-parented pills)
JS_CLICK_PILL = """(targetText) => {
    const pills = document.querySelectorAll('[class*="pill__Wrapper"]');
    for (const pill of pills) {
        if (pill.parentElement && pill.parentElement.tagName === 'BUTTON' &&
            pill.textContent.trim() === targetText) {
            pill.parentElement.click();
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
        if (!card.querySelector('[class*="UpcomingGameTime"]')) continue;
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

# Combined: parse game cards + pills + click spread/total tabs + collect all odds
# in a single evaluate() call. Accepts {spreadKeywords, totalKeywords, otKeywords, otSports, sport}
# to select the right pill. Returns {events, pills, spreadOdds, totalOdds}.
# Uses async with internal delays to let DOM update after tab clicks.
JS_SCRAPE_ALL_MARKETS = """async ({spreadKeywords, totalKeywords, otKeywords, otSports, sport}) => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));

    // --- Helper: collect odds from current tab ---
    function getCardOdds() {
        const cards = document.querySelectorAll('[data-at="game-card"]');
        const result = {};
        for (const card of cards) {
            if (!card.querySelector('[class*="UpcomingGameTime"]')) continue;
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
    }

    // --- Helper: click a pill by text ---
    function clickPill(targetText) {
        const pills = document.querySelectorAll('[class*="pill__Wrapper"]');
        for (const pill of pills) {
            if (pill.parentElement && pill.parentElement.tagName === 'BUTTON' &&
                pill.textContent.trim() === targetText) {
                pill.parentElement.click();
                return true;
            }
        }
        return false;
    }

    // --- Step 1: Parse game cards (1x2 default tab) ---
    const cards = document.querySelectorAll('[data-at="game-card"]');
    const events = [];
    for (const card of cards) {
        const upcomingTime = card.querySelector('[class*="UpcomingGameTime"]');
        if (!upcomingTime) continue;
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
        events.push({ eventId: idMatch[1], home: teams[0], away: teams[1], timeText, odds });
    }

    // --- Step 2: Get market pills ---
    const pills = [];
    document.querySelectorAll('[class*="pill__Wrapper"]').forEach(pill => {
        if (pill.parentElement && pill.parentElement.tagName === 'BUTTON') {
            const text = pill.textContent.trim();
            if (text) pills.push(text);
        }
    });

    // --- Step 3: Select best spread/total pills (same logic as Python) ---
    const preferOt = otSports.includes(sport);
    function pickPill(candidates) {
        if (!candidates.length) return null;
        if (preferOt) {
            const ot = candidates.filter(p => otKeywords.some(kw => p.toLowerCase().includes(kw)));
            if (ot.length) return ot[0];
        }
        for (const p of candidates) {
            const l = p.toLowerCase();
            if (!l.includes('halvlek') && !l.includes('halv') && !l.includes('half')) return p;
        }
        return candidates[0];
    }
    const spreadCandidates = pills.filter(p => spreadKeywords.some(kw => p.toLowerCase().includes(kw)));
    const totalCandidates = pills.filter(p => totalKeywords.some(kw => p.toLowerCase().includes(kw)));
    const spreadPill = pickPill(spreadCandidates);
    const totalPill = pickPill(totalCandidates);

    // --- Step 4: Click spread tab, wait, collect ---
    let spreadOdds = {};
    if (spreadPill && clickPill(spreadPill)) {
        await sleep(600);
        spreadOdds = getCardOdds();
    }

    // --- Step 5: Click total tab, wait, collect ---
    let totalOdds = {};
    if (totalPill && clickPill(totalPill)) {
        await sleep(600);
        totalOdds = getCardOdds();
    }

    return { events, pills, spreadOdds, totalOdds };
}"""
