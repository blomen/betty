# Mute Notifications — Design Spec

**Date**: 2026-03-26
**Status**: Approved

## Problem

Sportsbooks send email, SMS, and push notifications constantly. When using the mirror browser, we want to automatically silence these by replaying the provider's own "disable notifications" API call whenever we visit a site.

## Design

### Capture Phase (Discovery)

When the user manually disables notifications on a provider site, the mirror's existing response listener sees the API call. We add a new interception category — **notification settings** — that matches known patterns:

- URL keywords: `preferences`, `notifications`, `communication`, `consent`, `settings/contact`, `marketing`, `subscriptions`, `gdpr`
- Methods: `PUT`, `POST`, `PATCH`
- Heuristic: request body contains keys like `email`, `sms`, `push`, `marketing`, `newsletter` with boolean/toggle values

When a match is detected, the mirror stores a **recipe**:

```json
{
  "provider_id": "campobet",
  "captured_at": "2026-03-26T14:30:00Z",
  "request": {
    "method": "PUT",
    "url": "https://campobet.se/api/v1/account/preferences",
    "headers": {
      "content-type": "application/json"
    },
    "body": "{\"email\": false, \"sms\": false, \"push\": false}"
  },
  "status": "active"
}
```

Headers captured are only content-type and accept — auth cookies come from the browser context at replay time. The URL is stored as-is (absolute) since the domain is provider-specific.

### Storage

Recipes stored in `data/notification_recipes.json` — a flat JSON array of recipe objects. One recipe per provider (latest capture wins). File is loaded on MirrorService init and saved after each new capture.

### Auto-Replay

When `_check_provider_navigation` fires for a provider that has an active recipe:

1. Wait a short delay (2-3s) for auth cookies to settle after navigation
2. Use `context.request` (Playwright API context) to replay the captured request — this inherits the browser's cookies/session
3. If response is 2xx → log success, emit SSE `notifications_muted` event
4. If response is 4xx/5xx → log warning, mark recipe as `stale`, emit SSE `notifications_mute_failed`
5. Deduped per session via `_muted_providers` set (same pattern as `_detected_providers`)

### Interceptor Changes

Add to `BetInterceptor`:

- `_NOTIFICATION_KEYWORDS`: tuple of URL substrings to match notification settings calls
- In `_on_response`: new check after financial data — if URL matches notification keywords AND method is PUT/POST/PATCH AND response is 2xx, fire `on_notification_settings` callback
- New callback: `on_notification_settings(url, request_body, response_body, method, headers)`

### MirrorService Changes

- `_handle_notification_settings()`: parse the intercepted call, extract recipe, save to file
- `_replay_notification_mute(provider_id)`: called from provider detection, replays stored recipe
- `_load_recipes()` / `_save_recipes()`: JSON file I/O
- `_muted_providers: set[str]`: session dedup for replay

### API Endpoints

One lightweight management endpoint on the existing mirror router:

- `GET /api/mirror/notification-recipes` — list all stored recipes with status
- `DELETE /api/mirror/notification-recipes/{provider_id}` — remove a recipe (forces re-capture)

### Wiring Doc

New column `Mute Notifs` in `docs/mirror-wiring.md` — starts as `-` for all providers, updated to `Y` as recipes are captured and verified.

## What This Does NOT Do

- No provider-specific code — purely capture + replay
- No scraping account settings pages — only works with API-based preference endpoints
- No handling of providers that require multi-step UI flows to change settings (those stay `-` in the wiring doc)

## File Changes

| File | Change |
|------|--------|
| `backend/src/mirror/interceptor.py` | Add notification keywords, `on_notification_settings` callback, detection in `_on_response` |
| `backend/src/mirror/service.py` | Add recipe capture, storage, replay logic, new callback wiring |
| `backend/src/api/routes/mirror.py` | Add recipe list/delete endpoints |
| `docs/mirror-wiring.md` | Add `Mute Notifs` column |
| `backend/data/notification_recipes.json` | New file (auto-created on first capture) |
