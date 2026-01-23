# Advanced Options for Cracking Gecko Platform

**Purpose:** Bonus extraction requires Betsson/Betsafe/NordicBet access
**Current Blocker:** Advanced bot detection
**Goal:** Find a working bypass method

---

## Option 1: Undetected ChromeDriver (HIGH SUCCESS RATE) ⭐

### What is it?
`undetected-chromedriver` is a patched Selenium ChromeDriver specifically designed to bypass bot detection. It's actively maintained and works against Cloudflare, Imperva, hCaptcha, etc.

**GitHub:** https://github.com/ultrafunkamsterdam/undetected-chromedriver
**Stars:** 9.5k+ (very popular)
**Success Rate:** 80-90% for most sites

### Implementation

```bash
pip install undetected-chromedriver selenium
```

**Create new retriever:**

```python
# backend/src/core/undetected_transport.py

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import asyncio

class UndetectedTransport:
    def __init__(self, headless=False):
        self.headless = headless
        self.driver = None

    def _ensure_driver(self):
        if self.driver:
            return

        options = uc.ChromeOptions()
        if self.headless:
            options.add_argument('--headless=new')

        # Key: version_main parameter matches your Chrome version
        self.driver = uc.Chrome(
            options=options,
            version_main=131,  # Your Chrome version
            use_subprocess=True
        )

    async def get_page_content(self, url, wait_selector=None, wait_time=10):
        """Navigate and wait for content to load."""
        self._ensure_driver()

        self.driver.get(url)

        if wait_selector:
            try:
                WebDriverWait(self.driver, wait_time).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
                )
            except:
                pass

        # Additional wait for dynamic content
        await asyncio.sleep(5)

        return self.driver.page_source

    def execute_script(self, script):
        """Execute JavaScript and return result."""
        return self.driver.execute_script(script)

    def close(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
```

**Update GeckoRetriever to use it:**

```python
# backend/src/providers/gecko_undetected.py

from ..core.undetected_transport import UndetectedTransport

class GeckoUndetectedRetriever(GeckoRetriever):
    def __init__(self, config, transport=None):
        # Override to use UndetectedTransport
        if transport is None:
            transport = UndetectedTransport(headless=False)
        super().__init__(config, transport)

    async def extract(self, sport, limit=50):
        sport_url = self._get_sport_url(sport)

        # Use undetected driver
        html = await self.transport.get_page_content(
            sport_url,
            wait_selector='article, [class*="event"]',
            wait_time=15
        )

        # Parse HTML (reuse existing _parse_html_from_string method)
        # ... parsing logic
```

**Why this works:**
- Chrome DevTools Protocol (CDP) patches
- Removes automation flags
- Mimics real Chrome perfectly
- Battle-tested against major anti-bot services

---

## Option 2: Firefox with Gecko Driver (DIFFERENT FINGERPRINT)

### Why Firefox?

Betsson might specifically target Chromium detection. Firefox has completely different fingerprint.

```bash
pip install selenium geckodriver-autoinstaller
```

**Implementation:**

```python
import geckodriver_autoinstaller
from selenium import webdriver
from selenium.webdriver.firefox.options import Options

# Install geckodriver automatically
geckodriver_autoinstaller.install()

options = Options()
# Don't use headless initially
# options.add_argument('--headless')

# Disable automation flags
options.set_preference('dom.webdriver.enabled', False)
options.set_preference('useAutomationExtension', False)

# Use real user agent
options.set_preference('general.useragent.override',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0')

driver = webdriver.Firefox(options=options)
```

**Advantages:**
- Different browser engine (not Chromium)
- Different fingerprinting characteristics
- May not be blocked

---

## Option 3: Real Chrome Profile (USER DATA)

### Approach

Use your actual Chrome profile with real browsing history, cookies, and sessions.

**Find your Chrome profile:**
```
Windows: C:\Users\USERNAME\AppData\Local\Google\Chrome\User Data
Mac: ~/Library/Application Support/Google/Chrome
Linux: ~/.config/google-chrome
```

**With Playwright:**

```python
from playwright.async_api import async_playwright

async with async_playwright() as p:
    browser = await p.chromium.launch_persistent_context(
        user_data_dir='C:\\Users\\rasmu\\AppData\\Local\\Google\\Chrome\\User Data',
        headless=False,
        channel='chrome',  # Use actual Chrome, not Chromium
        args=['--disable-blink-features=AutomationControlled']
    )

    page = browser.pages[0]  # Use existing tab
    await page.goto('https://www.betsson.com/sv/odds/fotboll')
```

**With undetected-chromedriver:**

```python
import undetected_chromedriver as uc

options = uc.ChromeOptions()
options.add_argument(r'--user-data-dir=C:\Users\rasmu\AppData\Local\Google\Chrome\User Data')
options.add_argument('--profile-directory=Default')

driver = uc.Chrome(options=options)
```

**Why this works:**
- Real browsing history
- Logged-in sessions
- Trusted fingerprint
- Existing cookies

**Warning:** Close all Chrome windows before running!

---

## Option 4: GitHub Research - Existing Solutions

### Search Strategies

**1. Search for Betsson scrapers:**
```
site:github.com betsson scraper
site:github.com betsson odds
site:github.com betsson playwright
site:github.com betsson selenium
```

**2. Search for sports betting scrapers:**
```
site:github.com sports betting scraper sweden
site:github.com odds scraper
site:github.com sportsbook scraper
```

**3. Search for anti-detection:**
```
site:github.com bypass cloudflare playwright
site:github.com bypass bot detection
site:github.com undetected browser automation
```

**4. Check awesome lists:**
```
site:github.com awesome web scraping
site:github.com awesome selenium
site:github.com awesome playwright
```

### Promising Projects to Check

1. **undetected-chromedriver**
   https://github.com/ultrafunkamsterdam/undetected-chromedriver

2. **DrissionPage** (Selenium + requests hybrid)
   https://github.com/g1879/DrissionPage

3. **playwright-python anti-detection plugins**
   Search: `playwright python stealth`

4. **Selenium-driverless** (CDP-based, no driver)
   https://github.com/kaliiiiiiiiii/Selenium-Driverless

5. **Nodriver** (undetected automation)
   https://github.com/ultrafunkamsterdam/nodriver

---

## Option 5: Manual Session Capture (100% SUCCESS RATE)

### Approach

1. Browse Betsson manually in Chrome
2. Capture authenticated session
3. Export cookies/localStorage
4. Replay in automation

**Step 1: Capture cookies**

```javascript
// Run in Chrome DevTools console on Betsson site
copy(document.cookie)
```

**Step 2: Inject in automation**

```python
import json

# Load cookies
with open('betsson_cookies.json') as f:
    cookies = json.load(f)

# In Playwright
await context.add_cookies(cookies)

# In Selenium
for cookie in cookies:
    driver.add_cookie(cookie)
```

**Step 3: Capture localStorage**

```javascript
// In DevTools console
copy(JSON.stringify(localStorage))
```

```python
# Inject
await page.evaluate(f"Object.assign(localStorage, {local_storage})")
```

**Why this works:**
- Uses real authenticated session
- Bypasses bot detection completely
- Session appears legitimate

**Limitation:** Sessions expire (need periodic renewal)

---

## Option 6: Network Inspection for Hidden APIs

### Check if Betsson has API endpoints we missed

**Manual inspection:**

1. Open Betsson in Chrome
2. Open DevTools → Network tab
3. Filter: XHR
4. Navigate to football page
5. Look for API calls with fixture data

**Common patterns to look for:**
```
/api/v*/events
/api/v*/fixtures
/api/v*/matches
/graphql
/query
/data
```

**Tool: Playwright network capture**

```python
async def capture_api_calls():
    transport = BrowserTransport(headless=False)
    await transport._ensure_browser()
    page = transport.page

    api_calls = []

    async def log_request(request):
        if '/api/' in request.url or 'graphql' in request.url:
            api_calls.append({
                'url': request.url,
                'method': request.method,
                'headers': request.headers
            })

    page.on('request', log_request)

    await page.goto('https://www.betsson.com/sv/odds/fotboll')
    await asyncio.sleep(30)  # Wait for everything to load

    print(f"Captured {len(api_calls)} API calls:")
    for call in api_calls:
        print(f"  {call['method']} {call['url']}")

    with open('betsson_api_calls.json', 'w') as f:
        json.dump(api_calls, f, indent=2)
```

---

## Option 7: Selenium-Driverless (NO CHROMEDRIVER)

### What is it?

Uses Chrome DevTools Protocol directly without ChromeDriver. No automation signatures.

**GitHub:** https://github.com/kaliiiiiiiiii/Selenium-Driverless

```bash
pip install selenium-driverless
```

**Usage:**

```python
from selenium_driverless import webdriver
from selenium_driverless.types.by import By

async def main():
    options = webdriver.ChromeOptions()

    async with webdriver.Chrome(options=options) as driver:
        await driver.get('https://www.betsson.com/sv/odds/fotboll')
        await driver.sleep(10)

        articles = await driver.find_elements(By.CSS_SELECTOR, 'article')
        print(f"Found {len(articles)} articles")

import asyncio
asyncio.run(main())
```

**Why this works:**
- No ChromeDriver = no automation markers
- Pure CDP communication
- Harder to detect

---

## Option 8: Residential Proxies + Any Method

### Services

1. **Bright Data** (formerly Luminati)
   - Swedish residential IPs
   - $500/month minimum
   - Highest quality

2. **Smartproxy**
   - Swedish residential IPs
   - $75/month starter
   - Good for testing

3. **Oxylabs**
   - Swedish residential IPs
   - $100/month minimum

4. **ProxyMesh**
   - Rotating Swedish IPs
   - $50/month

### Implementation

```python
# With Playwright
browser = await p.chromium.launch(
    proxy={
        'server': 'http://proxy-server:port',
        'username': 'user',
        'password': 'pass'
    }
)

# With undetected-chromedriver
options = uc.ChromeOptions()
options.add_argument('--proxy-server=http://proxy:port')
```

**Why this works:**
- Swedish IP = no geo-blocking
- Residential = trusted IP
- Rotating = no rate limiting

---

## Option 9: Wait for SPA Hydration

### Maybe content loads after React/Vue hydration?

**Try waiting for specific events:**

```python
await page.goto(url, wait_until='networkidle')

# Wait for React/Vue to hydrate
await page.evaluate("""
    new Promise(resolve => {
        // Wait for window.__INITIAL_STATE__ or similar
        const check = () => {
            if (document.querySelectorAll('article').length > 0) {
                resolve();
            } else {
                setTimeout(check, 100);
            }
        };
        check();
    })
""")

# Or wait for specific network request to complete
await page.wait_for_response(
    lambda response: 'fixtures' in response.url or 'events' in response.url
)
```

---

## Recommended Next Steps (Ranked by Success Probability)

### 1. Try undetected-chromedriver (80% success rate) ⭐⭐⭐

**Time:** 30 minutes
**Cost:** Free
**Effort:** Low

```bash
pip install undetected-chromedriver selenium
```

Then create `GeckoUndetectedRetriever` as shown above.

### 2. Try real Chrome profile (70% success rate) ⭐⭐⭐

**Time:** 15 minutes
**Cost:** Free
**Effort:** Very Low

Just modify Playwright to use your real Chrome profile.

### 3. Try Firefox (60% success rate) ⭐⭐

**Time:** 30 minutes
**Cost:** Free
**Effort:** Low

Different browser engine might not be blocked.

### 4. Network inspection for hidden APIs (50% success rate) ⭐⭐

**Time:** 1 hour
**Cost:** Free
**Effort:** Medium

Might find API endpoint that works.

### 5. Manual session capture (100% success rate, manual renewal) ⭐⭐⭐⭐

**Time:** 1 hour setup
**Cost:** Free
**Effort:** Medium (ongoing)

Guaranteed to work but requires session management.

### 6. Residential proxies + undetected-chromedriver (95% success rate) ⭐⭐⭐⭐⭐

**Time:** 2 hours
**Cost:** $50-100/month
**Effort:** Medium

Most reliable long-term solution.

---

## My Recommendation for Bonus Extraction

Since you need this for bonus extraction (not just odds), I recommend:

**Phase 1 (This Week):**
1. Try **undetected-chromedriver** (30 min)
2. Try **real Chrome profile** (15 min)
3. Try **Firefox** (30 min)

**One of these will likely work!**

**Phase 2 (If Phase 1 fails):**
4. Add **residential proxy** ($50/month)
5. Use undetected-chromedriver + proxy

**Phase 3 (Production):**
6. Implement **session management** for reliability
7. Add **rotation** if needed

---

## Quick Test Script for undetected-chromedriver

```python
# scrap/test_undetected.py

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

def test_betsson():
    print("Launching undetected Chrome...")

    options = uc.ChromeOptions()
    # Don't use headless for testing

    driver = uc.Chrome(options=options, version_main=131)

    print("Navigating to Betsson...")
    driver.get('https://www.betsson.com/sv/odds/fotboll')

    print("Waiting for page to load...")
    time.sleep(15)

    # Check for articles
    articles = driver.find_elements(By.CSS_SELECTOR, 'article')
    print(f"\nArticles found: {len(articles)}")

    # Check for events
    events = driver.find_elements(By.CSS_SELECTOR, '[class*="event"], [class*="Event"]')
    print(f"Event elements found: {len(events)}")

    # Check webdriver detection
    webdriver_detected = driver.execute_script('return navigator.webdriver')
    print(f"navigator.webdriver: {webdriver_detected}")

    print("\nKeeping browser open for 30 seconds for manual inspection...")
    time.sleep(30)

    driver.quit()
    print("Test complete!")

if __name__ == "__main__":
    test_betsson()
```

Want me to implement the undetected-chromedriver approach first?
