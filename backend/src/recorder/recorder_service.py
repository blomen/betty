"""
Recorder Service — CDP-based browser action recording.

Connects to the user's Chrome via CDP, injects event listeners into all pages,
and captures clicks, inputs, navigations, and network requests. Actions are
buffered and flushed to DB, and broadcast over WebSocket for the live feed.
"""

import asyncio
import logging
from datetime import datetime, timezone

try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright

from .domain_detector import detect_provider

logger = logging.getLogger(__name__)

# JavaScript injected into every page to capture user actions.
# Calls window.__bbqRecord(data) which is exposed by Playwright.
CAPTURE_SCRIPT = """
(() => {
    if (window.__bbqRecorderInstalled) return;
    window.__bbqRecorderInstalled = true;

    function getSelector(el) {
        if (!el || !el.tagName) return null;
        // Priority 1: data-testid
        if (el.dataset && el.dataset.testid) return `[data-testid="${el.dataset.testid}"]`;
        // Priority 2: id
        if (el.id) return `#${el.id}`;
        // Priority 3: tag + classes (first 3)
        let sel = el.tagName.toLowerCase();
        if (el.className && typeof el.className === 'string') {
            const classes = el.className.trim().split(/\\s+/).slice(0, 3).filter(c => c.length < 40);
            if (classes.length) sel += '.' + classes.join('.');
        }
        return sel;
    }

    function getText(el) {
        if (!el) return null;
        const text = (el.innerText || el.textContent || '').trim();
        return text.length > 200 ? text.slice(0, 200) : text || null;
    }

    function buildAction(type, el, extra) {
        const data = {
            action_type: type,
            timestamp: new Date().toISOString(),
            url: window.location.href,
            page_title: document.title,
            css_selector: getSelector(el),
            element_tag: el ? el.tagName.toLowerCase() : null,
            element_text: getText(el),
            element_id: el ? el.id || null : null,
            element_class: el ? (typeof el.className === 'string' ? el.className : null) : null,
            viewport_width: window.innerWidth,
            viewport_height: window.innerHeight,
            ...extra,
        };
        return data;
    }

    // Click capture (capture phase to get it before any preventDefault)
    document.addEventListener('click', (e) => {
        const el = e.target;
        const data = buildAction('click', el, {
            x: e.clientX,
            y: e.clientY,
        });
        if (window.__bbqRecord) window.__bbqRecord(JSON.stringify(data));
    }, true);

    // Input capture (debounced — fire on change, not every keystroke)
    document.addEventListener('change', (e) => {
        const el = e.target;
        const isPassword = el.type === 'password';
        const data = buildAction('input', el, {
            input_value: isPassword ? '***' : (el.value || ''),
            input_type: el.type || 'text',
        });
        if (window.__bbqRecord) window.__bbqRecord(JSON.stringify(data));
    }, true);

    // Select capture
    document.addEventListener('change', (e) => {
        const el = e.target;
        if (el.tagName !== 'SELECT') return;
        const data = buildAction('select', el, {
            input_value: el.value || '',
            input_type: 'select',
        });
        if (window.__bbqRecord) window.__bbqRecord(JSON.stringify(data));
    }, true);

    // SPA navigation (popstate + hashchange)
    const navHandler = () => {
        const data = buildAction('navigation', null, {
            url: window.location.href,
            page_title: document.title,
        });
        if (window.__bbqRecord) window.__bbqRecord(JSON.stringify(data));
    };
    window.addEventListener('popstate', navHandler);
    window.addEventListener('hashchange', navHandler);
})();
"""


class RecorderService:
    """Manages CDP-based browser action recording."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._is_recording: bool = False
        self._current_session_id: int | None = None
        self._cdp_url: str | None = None
        self._action_sequence: int = 0
        self._action_buffer: list[dict] = []
        self._attached_pages: set = set()
        self._ws_broadcast = None  # Set externally to broadcast actions
        self._db_flush_callback = None  # Set externally to persist actions

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def current_session_id(self) -> int | None:
        return self._current_session_id

    def get_status(self) -> dict:
        """Return current recording status."""
        return {
            "is_recording": self._is_recording,
            "session_id": self._current_session_id,
            "cdp_url": self._cdp_url,
            "action_count": self._action_sequence,
            "buffered_actions": len(self._action_buffer),
        }

    async def start_recording(self, cdp_url: str, session_id: int) -> None:
        """Connect to Chrome via CDP and begin capturing actions on all pages."""
        if self._is_recording:
            raise RuntimeError("Already recording. Stop the current session first.")

        logger.info(f"Connecting to Chrome at {cdp_url} for recording session {session_id}")

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            logger.error(f"CDP connect failed: {type(e).__name__}: {e!r}", exc_info=True)
            await self._playwright.stop()
            self._playwright = None
            raise ConnectionError(
                f"Could not connect to Chrome at {cdp_url}: {type(e).__name__}: {e}"
            ) from e

        self._cdp_url = cdp_url
        self._current_session_id = session_id
        self._action_sequence = 0
        self._action_buffer = []
        self._attached_pages = set()
        self._is_recording = True

        # Attach to all existing pages
        contexts = self._browser.contexts
        for ctx in contexts:
            self._context = ctx
            for page in ctx.pages:
                await self._attach_to_page(page)
            # Listen for new pages (new tabs)
            ctx.on("page", lambda p: asyncio.ensure_future(self._attach_to_page(p)))

        logger.info(
            f"Recording started: session={session_id}, "
            f"pages={len(self._attached_pages)}"
        )

    async def stop_recording(self) -> dict:
        """Stop recording and clean up CDP connection."""
        if not self._is_recording:
            return {"error": "Not recording"}

        # Flush any remaining buffered actions
        if self._action_buffer and self._db_flush_callback:
            await self._db_flush_callback(self._action_buffer)
            self._action_buffer = []

        session_id = self._current_session_id
        action_count = self._action_sequence

        # Disconnect from Chrome (does not close the browser)
        try:
            if self._browser:
                await self._browser.close()
        except Exception as e:
            logger.warning(f"Error closing CDP connection: {e}")
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.warning(f"Error stopping playwright: {e}")

        self._browser = None
        self._context = None
        self._playwright = None
        self._is_recording = False
        self._current_session_id = None
        self._cdp_url = None
        self._attached_pages = set()

        logger.info(f"Recording stopped: session={session_id}, actions={action_count}")

        return {
            "session_id": session_id,
            "action_count": action_count,
        }

    async def _attach_to_page(self, page) -> None:
        """Install event listeners on a single page."""
        page_id = id(page)
        if page_id in self._attached_pages:
            return

        self._attached_pages.add(page_id)

        try:
            # Expose callback function for the injected script
            try:
                await page.expose_function("__bbqRecord", self._on_raw_action)
            except Exception:
                # Function may already be exposed (e.g., page navigated)
                pass

            # Inject capture script into future navigations
            await page.add_init_script(CAPTURE_SCRIPT)

            # Inject into the currently loaded page too
            try:
                await page.evaluate(CAPTURE_SCRIPT)
            except Exception:
                # Page might not be ready yet
                pass

            # Capture full-page navigations (not SPA — those are caught by JS)
            page.on("framenavigated", lambda frame: asyncio.ensure_future(
                self._on_navigation(frame)
            ))

            # Handle page close
            page.on("close", lambda: self._attached_pages.discard(page_id))

            logger.debug(f"Attached recorder to page: {page.url}")

        except Exception as e:
            logger.warning(f"Failed to attach recorder to page: {e}")
            self._attached_pages.discard(page_id)

    async def _on_navigation(self, frame) -> None:
        """Handle full-page navigation events."""
        if not self._is_recording:
            return
        # Only track main frame navigations
        if frame.parent_frame:
            return

        url = frame.url
        if not url or url == "about:blank":
            return

        provider_id = detect_provider(url)

        action = {
            "action_type": "navigation",
            "timestamp": datetime.now(timezone.utc),
            "sequence": self._action_sequence,
            "url": url,
            "page_title": "",
            "provider_id": provider_id,
        }

        try:
            action["page_title"] = await frame.title()
        except Exception:
            pass

        self._action_sequence += 1
        await self._process_action(action)

    async def _on_raw_action(self, raw_json: str) -> None:
        """Handle a captured action from the injected JS."""
        if not self._is_recording:
            return

        import json
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return

        provider_id = detect_provider(data.get("url", ""))

        action = {
            "action_type": data.get("action_type", "unknown"),
            "timestamp": datetime.now(timezone.utc),
            "sequence": self._action_sequence,
            "url": data.get("url"),
            "page_title": data.get("page_title"),
            "provider_id": provider_id,
            "css_selector": data.get("css_selector"),
            "element_tag": data.get("element_tag"),
            "element_text": data.get("element_text"),
            "element_id": data.get("element_id"),
            "element_class": data.get("element_class"),
            "x": data.get("x"),
            "y": data.get("y"),
            "viewport_width": data.get("viewport_width"),
            "viewport_height": data.get("viewport_height"),
            "input_value": data.get("input_value"),
            "input_type": data.get("input_type"),
        }

        self._action_sequence += 1
        await self._process_action(action)

    async def _process_action(self, action: dict) -> None:
        """Buffer the action, broadcast via WebSocket, and flush if needed."""
        action["session_id"] = self._current_session_id

        # Broadcast via WebSocket (real-time feed)
        if self._ws_broadcast:
            try:
                broadcast_data = {
                    "type": "action",
                    "data": {
                        **action,
                        "timestamp": action["timestamp"].isoformat()
                        if isinstance(action["timestamp"], datetime)
                        else action["timestamp"],
                    },
                }
                await self._ws_broadcast(broadcast_data)
            except Exception as e:
                logger.debug(f"WebSocket broadcast failed: {e}")

        # Buffer for DB flush
        self._action_buffer.append(action)

        # Flush every 10 actions
        if len(self._action_buffer) >= 10 and self._db_flush_callback:
            actions_to_flush = self._action_buffer[:]
            self._action_buffer = []
            try:
                await self._db_flush_callback(actions_to_flush)
            except Exception as e:
                logger.error(f"Failed to flush actions to DB: {e}")
                # Put them back
                self._action_buffer = actions_to_flush + self._action_buffer


# Singleton instance
_recorder_service: RecorderService | None = None


def get_recorder_service() -> RecorderService:
    """Get or create the singleton RecorderService."""
    global _recorder_service
    if _recorder_service is None:
        _recorder_service = RecorderService()
    return _recorder_service
