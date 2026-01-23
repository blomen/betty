# MCP Server Setup Guide

This guide explains how to configure Model Context Protocol (MCP) servers for the OddOpp project.

## What are MCP Servers?

MCP servers extend Claude's capabilities by providing:
- Database access (query SQLite directly)
- API interactions (test betting provider endpoints)
- Web search (research providers/odds formats)
- Memory (remember project-specific context)
- GitHub integration (automated PR creation)

## Installation Steps

### 1. Locate Claude Desktop Config

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
**Mac**: `~/Library/Application Support/Claude/claude_desktop_config.json`

### 2. Copy Configuration

Copy the contents from `.claude/mcp_config.json` to your Claude Desktop config file.

### 3. Get API Keys

#### GitHub Personal Access Token
1. Visit: https://github.com/settings/tokens
2. Click "Generate new token (classic)"
3. Select scopes: `repo`, `read:org`
4. Copy token and replace `<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>` in config

#### Brave Search API Key (Optional)
1. Visit: https://brave.com/search/api/
2. Sign up for free tier (2,000 queries/month)
3. Copy API key and replace `<BSAtpBZSL7U234AvwJo6VkwTMs6ty6b>` in config

### 4. Install Prerequisites

Ensure you have Node.js and Python installed:

```bash
# Verify installations
node --version   # Should be v18+
python --version # Should be 3.10+

# uvx comes with uv (Python package installer)
pip install uv
```

### 5. Restart Claude Desktop

Close and reopen Claude Desktop for changes to take effect.

## Configured MCP Servers

### sqlite
**Purpose**: Direct database queries
**Usage**:
- "Show me all arbitrage opportunities with >5% profit"
- "Query events table for today's NBA games"
- "Get all odds for provider 'unibet'"

**Database Path**: `C:\Users\rasmu\oddopp\backend\data\oddopp.db`

### github
**Purpose**: Repository management
**Usage**:
- "Create a PR for the gecko provider implementation"
- "List open issues related to Kambi"
- "Show recent commits on main branch"

**Requires**: GitHub Personal Access Token

### fetch
**Purpose**: HTTP requests and API testing
**Usage**:
- "Fetch kambi API endpoint for event 123456"
- "Test the Snabbare markets endpoint"
- "Get raw response from Spectate discovery"

**No API key required**

### brave-search
**Purpose**: Web search for research
**Usage**:
- "Search for Betsson API documentation"
- "Find information about American odds formats"
- "Research Polymarket probability calculations"

**Requires**: Brave Search API key (optional - 2k free queries/month)

### memory
**Purpose**: Cross-session context retention
**Usage**:
- "Remember that Kambi uses ISO-8601 timestamps"
- "What did we learn about Spectate rate limits?"
- "Recall the normalization rules for UFC fighters"

**No API key required**

### context7
**Purpose**: Enhanced code understanding
**Usage**:
- Advanced code analysis and pattern recognition
- Contextual project documentation
- Intelligent code navigation

**No API key required**

## Troubleshooting

### Server Won't Start
- Check that `uvx` and `npx` are in your PATH
- Verify Node.js version is v18 or higher
- Check Claude Desktop logs: `%APPDATA%\Claude\logs\mcp.log`

### SQLite Path Issues
- Ensure database exists: `C:\Users\rasmu\oddopp\backend\data\oddopp.db`
- Use double backslashes in Windows paths: `C:\\Users\\...`

### GitHub Authentication Failed
- Regenerate token with correct scopes (`repo`, `read:org`)
- Ensure no extra spaces in token value
- Token should start with `ghp_` or `github_pat_`

### Fetch Timeouts
- Some betting APIs have rate limits
- Add delays between requests
- Check if API requires authentication headers

## Project-Specific Use Cases

### Debugging Extraction Pipeline
```
# Query failed extractions
sqlite> SELECT * FROM events WHERE provider = 'betsson' AND extracted_at > datetime('now', '-1 hour')

# Test API endpoint
fetch> GET https://eu-offering-api.kambicdn.com/offering/v2018/pivuslarl/...
```

### Value Bet Analysis
```
# Find high-value bets
sqlite> SELECT e.home_team, e.away_team, o.market, o.outcome, o.odds, o.edge_percent
        FROM events e
        JOIN odds o ON e.id = o.event_id
        WHERE o.edge_percent > 5
        ORDER BY o.edge_percent DESC

# Remember threshold adjustments
memory> Remember: We set minimum edge threshold to 3% for NFL games due to line movement
```

### Provider Research
```
# Find new provider documentation
brave-search> "Snabbare sportsbook API documentation"

# Research odds formats
brave-search> "decimal odds vs american odds conversion formula"
```

### Development Workflow
```
# Create feature branch PR
github> Create PR for branch "feature/gecko-provider" with title "Add Gecko/Betsson provider support"

# Check CI status
github> Show check runs for latest commit
```

## Benefits for OddOpp Project

1. **Faster Debugging**: Query database without writing Python scripts
2. **API Testing**: Test provider endpoints directly from chat
3. **Documentation**: Search for provider APIs and betting terminology
4. **Context Retention**: Remember provider quirks across sessions
5. **Automation**: Create PRs and manage issues without leaving Claude
6. **Code Understanding**: Better analysis with Context7

## Security Notes

- Never commit API keys to version control
- Rotate GitHub tokens every 90 days
- Use minimal token scopes (principle of least privilege)
- Store tokens in Claude Desktop config (not in project files)
