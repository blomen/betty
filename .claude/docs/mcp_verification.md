# MCP Server Verification Report

Generated: 2026-01-23

## Prerequisites Status

| Component | Version | Status |
|-----------|---------|--------|
| Node.js | v22.19.0 | ✓ OK (requires v18+) |
| Python | 3.13.5 | ✓ OK (requires 3.10+) |
| npm/npx | 10.9.3 | ✓ OK |
| uv/uvx | 0.9.26 | ✓ OK (installed) |

## Database Status

**Path**: `C:\Users\rasmu\oddopp\backend\data\oddopp.db`

| Table | Records | Notes |
|-------|---------|-------|
| events | 4,705 | Canonical events across providers |
| odds | 490,237 | Multi-provider odds data |
| providers | 13 | All enabled |
| opportunities | 0 | None detected yet |
| bets | 0 | No manual bets tracked |
| profiles | Unknown | User settings |

**Configured Providers** (13 total):
- Polymarket (truth source)
- 888Sport.Se
- Casumo.Com
- Expekt.Se
- Leovegas.Com
- Mrgreen.Com
- Paf.Se
- Unibet.Se
- Atg.Se
- Betmgm.Se
- Speedybet.Com
- X3000.Se
- Snabbare.Com

## MCP Configuration

**Config File**: `.claude/mcp_config.json`

| Server | Status | Notes |
|--------|--------|-------|
| sqlite | ✓ Configured | DB path verified, uvx installed |
| github | ✓ Configured | Token added (ghp_zBP...) |
| fetch | ✓ Configured | No auth required |
| brave-search | ✓ Configured | API key added (BSAt...) |
| memory | ✓ Configured | No auth required |
| context7 | ✓ Configured | No auth required |

**Security**: `.claude/mcp_config.json` added to `.gitignore` ✓

## Final Setup Step

Copy `.claude/mcp_config.json` to Claude Desktop config:

**Windows**:
```cmd
# Open folder
explorer %APPDATA%\Claude

# Edit file (or create if missing)
notepad %APPDATA%\Claude\claude_desktop_config.json
```

**Mac**:
```bash
# Edit file (or create if missing)
nano ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

**Then**:
1. Copy entire contents from `.claude/mcp_config.json`
2. Paste into `claude_desktop_config.json`
3. Save and close
4. Restart Claude Desktop

## Test Commands

After restarting Claude Desktop, try these commands:

### SQLite
```
"Show me the schema of the events table"
"Query the 10 most recent events"
"How many odds records are from Polymarket?"
```

### GitHub
```
"List recent commits on this repo"
"Show the current branch and status"
"List open issues"
```

### Fetch
```
"Fetch https://httpbin.org/json"
"Test the httpbin.org/get endpoint"
```

### Memory
```
"Remember: Kambi providers use ISO-8601 timestamps"
"Remember: Minimum edge threshold is 3% for NFL"
"What do you remember about Kambi?"
```

### Brave Search
```
"Search for Betsson API documentation"
"Find information about American odds conversion"
```

### Context7
```
"Explain the architecture of the provider system"
"Show me the relationships between Event and Odds models"
```

## Troubleshooting

### MCP Servers Not Loading
- Check logs: `%APPDATA%\Claude\logs\mcp.log`
- Verify config is valid JSON (no trailing commas)
- Ensure API keys have no extra spaces/quotes

### SQLite Connection Failed
- Verify database path uses double backslashes: `C:\\Users\\...`
- Ensure database file exists: `dir backend\data\oddopp.db`

### GitHub Authentication Failed
- Token should start with `ghp_` or `github_pat_`
- Required scopes: `repo`, `read:org`
- Regenerate token if expired

### Fetch Timeouts
- Some betting APIs have rate limits
- Try testing with httpbin.org first
- Check if API requires auth headers

## Next Steps

1. Copy config to Claude Desktop
2. Restart Claude Desktop
3. Look for MCP indicators in UI
4. Test each server with example commands
5. Start querying your odds database!

## Project-Specific Query Examples

Once MCP servers are loaded, you can:

```sql
-- Find arbitrage opportunities (sum of implied probs < 1)
SELECT * FROM odds WHERE event_id IN (
  SELECT event_id FROM odds
  GROUP BY event_id
  HAVING SUM(1.0/odds) < 1
)

-- Get high-value bets (provider odds > Polymarket fair odds)
SELECT e.home_team, e.away_team, o.outcome, o.odds
FROM events e
JOIN odds o ON e.id = o.event_id
WHERE o.provider != 'Polymarket'
ORDER BY o.updated_at DESC
LIMIT 20

-- Provider performance (number of odds per provider)
SELECT provider, COUNT(*) as odds_count
FROM odds
GROUP BY provider
ORDER BY odds_count DESC
```

These queries will be much easier with SQLite MCP!
