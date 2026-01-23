# MCP Server Usage Guide - OddOpp Project

Quick reference for using MCP servers with the betting analytics platform.

## SQLite Server - Database Queries

### Schema Exploration
```
"Show me the schema of the events table"
"What columns does the odds table have?"
"Describe the opportunities table structure"
```

### Data Analysis
```
"Query the 10 most recent events"
"Show all providers and their balances"
"How many odds records are from Polymarket?"
"Find events from today with more than 10 odds entries"
```

### Provider Statistics
```
"Count odds records per provider"
"Show providers with the most events"
"Which provider has the highest average odds?"
```

### Value Bet Hunting
```
"Find odds where the edge_percent is greater than 5%"
"Show me events with odds from both Polymarket and Unibet"
"Query arbitrage opportunities from the opportunities table"
```

### Advanced Queries
```
"Find events where home team contains 'Lakers'"
"Show all odds for soccer events happening today"
"Get the most recent 20 odds updates with provider names"
```

## GitHub Server - Repository Management

### Branch & Commits
```
"Show recent commits on this repo"
"What's the current branch?"
"List all branches"
"Show the last 5 commit messages"
```

### Issues & PRs
```
"List open issues"
"Show recent pull requests"
"Create an issue for: Add Betsson provider support"
"Show the status of PR #123"
```

### Code Review
```
"Show changes in the last commit"
"What files changed in commit abc123?"
"Show diff for backend/src/providers/gecko.py"
```

### PR Creation
```
"Create a PR for the current branch"
"Draft a PR with title 'Add Gecko provider' and description from recent commits"
```

## Fetch Server - API Testing

### Test Provider APIs
```
"Fetch https://api.polymarket.com/markets (if public endpoint)"
"Test GET request to https://httpbin.org/json"
"Fetch the Kambi API endpoint with headers"
```

### Debug Endpoints
```
"Test the Snabbare markets API"
"Check if the Spectate discovery endpoint is responding"
"Fetch odds data from provider X endpoint"
```

### Response Inspection
```
"Fetch URL and show response headers"
"Get the JSON structure from this API endpoint"
"Test if this endpoint requires authentication"
```

## Memory Server - Project Context

### Remember Provider Details
```
"Remember: Kambi providers use ISO-8601 timestamps in UTC"
"Remember: Snabbare API has rate limit of 100 requests/minute"
"Remember: Spectate requires event_ids from discovery endpoint first"
```

### Remember Bugs & Fixes
```
"Remember: Betsson odds sometimes include suspended markets - filter them out"
"Remember: UFC fighter names need special normalization for 'Jr.' and 'III'"
"Remember: Minimum edge threshold is 3% for NFL to avoid false positives"
```

### Remember Configurations
```
"Remember: Polymarket is the truth source - always fetch it first"
"Remember: Use Kelly fraction of 0.25 for conservative betting"
"Remember: Only providers with balance > 0 should trigger bet recommendations"
```

### Recall Information
```
"What do you remember about Kambi?"
"Recall the rate limits for provider APIs"
"What normalization rules have we established?"
```

## Brave Search - Research

### Provider Documentation
```
"Search for Betsson API documentation"
"Find information about Kambi sportsbook API"
"Search for Snabbare odds feed documentation"
```

### Betting Concepts
```
"Search for Kelly criterion betting strategy"
"Find information about arbitrage betting risks"
"Look up American odds to decimal odds conversion"
```

### Technical Research
```
"Search for SQLAlchemy 2.0 async session patterns"
"Find FastAPI WebSocket implementation examples"
"Look up Playwright anti-detection techniques"
```

## Context7 - Code Analysis

### Architecture Understanding
```
"Explain the provider extraction architecture"
"How does the matching system work?"
"Show me the relationship between Event and Odds models"
```

### Code Navigation
```
"Where is the Kelly criterion calculation implemented?"
"Find all classes that inherit from Retriever"
"Show me how canonical event IDs are generated"
```

### Refactoring Insights
```
"Analyze the provider factory pattern"
"What design patterns are used in the pipeline?"
"Suggest improvements to the normalization system"
```

## Project-Specific Workflows

### Daily Odds Analysis
1. Use SQLite to query recent events
2. Check for arbitrage opportunities
3. Use Memory to recall provider-specific quirks
4. Use Fetch to test any suspicious API responses

### Adding New Provider
1. Use Brave Search to find provider API docs
2. Use Fetch to test endpoints and structure
3. Use Context7 to understand existing provider patterns
4. Use Memory to record provider-specific details
5. Use GitHub to create PR when ready

### Debugging Extraction Issues
1. Use SQLite to find failed extractions
2. Use Fetch to test provider API manually
3. Use Memory to recall known issues with that provider
4. Use GitHub to check if issue already reported

### Value Bet Investigation
1. Use SQLite to find high edge% opportunities
2. Use Memory to recall edge thresholds per sport
3. Use Fetch to verify odds are still available
4. Use SQLite to check Polymarket comparison

## Combining MCP Servers

### Example: Investigate Missing Odds
```
Step 1: "Query events from last hour with zero odds entries"
Step 2: "What do you remember about this provider's rate limits?"
Step 3: "Fetch the provider's API endpoint to check if it's down"
Step 4: "Search for recent outages for this provider"
```

### Example: Add New Market Type
```
Step 1: "Search for documentation on 'player props' betting markets"
Step 2: "Show me where market normalization is implemented"
Step 3: "Remember: Player props use different outcome format than game lines"
Step 4: "Create an issue to add player props support"
```

### Example: Optimize Edge Detection
```
Step 1: "Query opportunities table for false positive patterns"
Step 2: "What edge thresholds have we configured?"
Step 3: "Analyze the value.py edge calculation logic"
Step 4: "Remember: Increase NFL threshold to 3.5% based on analysis"
```

## Tips for Effective MCP Usage

### SQLite Queries
- Use table/column names from schema: `events`, `odds`, `providers`, etc.
- Filter by `updated_at` for recent data: `WHERE updated_at > datetime('now', '-1 hour')`
- Join events and odds: `FROM events e JOIN odds o ON e.id = o.event_id`

### GitHub Operations
- Always check current branch before creating PRs
- Use commit history to understand recent changes
- Reference issues in commit messages: `Fixes #123`

### Fetch Requests
- Test with httpbin.org first to verify Fetch is working
- Some betting APIs require user-agent headers
- Rate limits may block repeated test requests

### Memory Storage
- Be specific with stored information
- Include context: provider name, sport, date range
- Use clear recall queries: "What did we learn about X?"

### Brave Search
- Include year in searches: "Betsson API 2026"
- Use specific terms: "API documentation" not just "docs"
- Combine with technical terms: "REST API endpoints"

### Context7 Analysis
- Ask about patterns and relationships
- Use for understanding complex code flows
- Great for refactoring insights

## Common Queries Cheat Sheet

| Task | Query Example |
|------|---------------|
| Recent events | "Query events from last 24 hours" |
| Provider stats | "Count odds by provider" |
| High value bets | "Find odds with edge_percent > 5%" |
| Arbitrage | "Show opportunities where profit > 2%" |
| Recent commits | "List last 10 commits" |
| Test API | "Fetch https://api.example.com/endpoint" |
| Remember detail | "Remember: Provider X uses UTC timestamps" |
| Recall info | "What do you remember about rate limits?" |
| Search docs | "Search for Kambi API documentation" |
| Code analysis | "Explain the extractor factory pattern" |

## Troubleshooting

### SQLite: "no such table"
- Check database path is correct in config
- Verify table exists: "Show all table names in database"
- Database might be empty - run extraction pipeline

### GitHub: "authentication failed"
- Check token in claude_desktop_config.json
- Regenerate token with repo access
- Ensure token hasn't expired

### Fetch: "timeout"
- Provider API might have rate limits
- Check if API requires authentication
- Verify URL is correct and accessible

### Memory: Returns nothing
- Memory is session-specific initially
- Store information first: "Remember: X"
- Use specific recall queries

### Brave Search: No results
- Check API key is valid
- Free tier has 2k queries/month limit
- Try more specific search terms

### Context7: Limited analysis
- Provide more context about what you're analyzing
- Ask specific questions about code structure
- Works best with clear file/function references

## Next Steps

Now that MCP servers are configured, you can:

1. Query your 490k+ odds records directly from chat
2. Automate PR creation for provider updates
3. Test betting APIs without writing scripts
4. Build up provider knowledge in Memory
5. Research new providers and markets easily
6. Get deeper insights into your codebase

Try starting with: "Query the 10 most recent events with their odds counts"
