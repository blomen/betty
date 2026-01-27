import type { Message, BettingContext } from '@/types';

const CLAUDE_API_ENDPOINT = '/api/chat';

export interface StreamCallbacks {
  onToken: (token: string) => void;
  onComplete: (fullResponse: string) => void;
  onError: (error: Error) => void;
}

function buildSystemPrompt(context: BettingContext): string {
  const parts = [
    `You are OddOpp, an AI assistant for sports betting analytics. You help users find value bets and arbitrage opportunities by analyzing odds across multiple bookmakers.`,
    ``,
    `Current data summary:`,
    `- ${context.arbitrage.length} arbitrage opportunities`,
    `- ${context.valueBets.length} value bets detected`,
    `- ${context.events.length} events tracked`,
    `- ${context.providers.length} providers connected`,
  ];

  if (context.arbitrage.length > 0) {
    parts.push(``, `Top arbitrage opportunities:`);
    context.arbitrage.slice(0, 3).forEach((arb, i) => {
      parts.push(`${i + 1}. ${arb.event} - ${arb.profit_pct.toFixed(2)}% profit`);
    });
  }

  if (context.valueBets.length > 0) {
    parts.push(``, `Top value bets:`);
    context.valueBets.slice(0, 3).forEach((vb, i) => {
      parts.push(`${i + 1}. ${vb.event} - ${vb.outcome} @ ${vb.odds} (${vb.edge_pct.toFixed(1)}% edge)`);
    });
  }

  parts.push(
    ``,
    `You can help users:`,
    `- Analyze current betting opportunities`,
    `- Explain arbitrage and value betting strategies`,
    `- Calculate optimal stake sizes using Kelly criterion`,
    `- Compare odds across providers`,
    `- Understand implied probabilities and margins`,
    ``,
    `Be concise, data-driven, and use tables/formatting when presenting odds data.`
  );

  return parts.join('\n');
}

export async function streamChat(
  messages: Message[],
  context: BettingContext,
  callbacks: StreamCallbacks,
  signal?: AbortSignal
): Promise<void> {
  const systemPrompt = buildSystemPrompt(context);

  const apiMessages = messages.map((m) => ({
    role: m.role,
    content: m.content,
  }));

  try {
    const response = await fetch(CLAUDE_API_ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        system: systemPrompt,
        messages: apiMessages,
        stream: true,
      }),
      signal,
    });

    if (!response.ok) {
      throw new Error(`Chat API error: ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('No response body');
    }

    const decoder = new TextDecoder();
    let fullResponse = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          if (data === '[DONE]') {
            callbacks.onComplete(fullResponse);
            return;
          }
          try {
            const parsed = JSON.parse(data);
            if (parsed.content) {
              fullResponse += parsed.content;
              callbacks.onToken(parsed.content);
            }
          } catch {
            // Skip malformed JSON
          }
        }
      }
    }

    callbacks.onComplete(fullResponse);
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      callbacks.onComplete('');
      return;
    }
    callbacks.onError(error instanceof Error ? error : new Error(String(error)));
  }
}

// Fallback for when backend chat isn't available - simulates responses
export async function simulateChat(
  messages: Message[],
  context: BettingContext,
  callbacks: StreamCallbacks
): Promise<void> {
  const lastMessage = messages[messages.length - 1];
  const input = lastMessage.content.toLowerCase();

  let response = '';

  if (input.includes('arbitrage') || input.includes('arb')) {
    if (context.arbitrage.length === 0) {
      response = 'No arbitrage opportunities currently detected. I scan for situations where the sum of implied probabilities across bookmakers is less than 100%, guaranteeing profit regardless of outcome.';
    } else {
      response = `Found **${context.arbitrage.length}** arbitrage opportunities:\n\n`;
      response += '| Event | Profit | Providers |\n|-------|--------|----------|\n';
      context.arbitrage.slice(0, 5).forEach((arb) => {
        const providers = arb.legs.map((l) => l.provider).join(', ');
        response += `| ${arb.event} | ${arb.profit_pct.toFixed(2)}% | ${providers} |\n`;
      });
    }
  } else if (input.includes('value') || input.includes('edge')) {
    if (context.valueBets.length === 0) {
      response = 'No value bets currently detected. Value bets occur when bookmaker odds exceed fair probability (derived from Polymarket).';
    } else {
      response = `Found **${context.valueBets.length}** value bets:\n\n`;
      response += '| Event | Outcome | Odds | Edge | Kelly |\n|-------|---------|------|------|-------|\n';
      context.valueBets.slice(0, 5).forEach((vb) => {
        response += `| ${vb.event} | ${vb.outcome} | ${vb.odds.toFixed(2)} | ${vb.edge_pct.toFixed(1)}% | ${(vb.kelly_stake * 100).toFixed(1)}% |\n`;
      });
    }
  } else if (input.includes('help') || input.includes('what can')) {
    response = `I can help you with:

- **Arbitrage detection** - Find guaranteed profit opportunities across bookmakers
- **Value bets** - Identify when bookmaker odds exceed fair probability
- **Kelly staking** - Calculate optimal bet sizes based on edge
- **Odds comparison** - Compare prices across providers
- **Probability analysis** - Convert odds to implied probabilities

Try asking: "Show me arbitrage opportunities" or "What value bets do you see?"`;
  } else if (input.includes('provider') || input.includes('bookmaker')) {
    if (context.providers.length === 0) {
      response = 'No providers currently connected. The system supports Kambi, Polymarket, and other bookmakers.';
    } else {
      response = `Connected providers:\n\n`;
      context.providers.forEach((p) => {
        const status = p.active ? '[OK]' : '[--]';
        response += `${status} **${p.name}** - Balance: $${p.balance.toFixed(2)}\n`;
      });
    }
  } else {
    response = `I'm OddOpp, your betting analytics assistant. I currently see:

- **${context.arbitrage.length}** arbitrage opportunities
- **${context.valueBets.length}** value bets
- **${context.events.length}** events tracked

Ask me about arbitrage, value bets, or specific events!`;
  }

  // Simulate streaming
  const words = response.split(' ');
  for (let i = 0; i < words.length; i++) {
    await new Promise((resolve) => setTimeout(resolve, 20 + Math.random() * 30));
    callbacks.onToken(words[i] + (i < words.length - 1 ? ' ' : ''));
  }
  callbacks.onComplete(response);
}
