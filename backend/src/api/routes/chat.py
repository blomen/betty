"""Chat API routes (Claude integration)."""

import os
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..schemas import ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


async def stream_anthropic_response(system: str, messages: list[dict]):
    """Stream responses from Anthropic API."""
    import httpx
    import json

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield 'data: {"content": "Error: ANTHROPIC_API_KEY not set"}\n\n'
        yield 'data: [DONE]\n\n'
        return

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 4096,
                    "system": system,
                    "messages": messages,
                    "stream": True,
                },
                timeout=60.0,
            )

            if response.status_code != 200:
                yield f'data: {{"content": "API error: {response.status_code}"}}\n\n'
                yield 'data: [DONE]\n\n'
                return

            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                yield f'data: {{"content": {json.dumps(text)}}}\n\n'
                    except:
                        pass

            yield 'data: [DONE]\n\n'

        except Exception as e:
            yield f'data: {{"content": "Error: {str(e)}"}}\n\n'
            yield 'data: [DONE]\n\n'


@router.post("")
async def chat(request: ChatRequest):
    """Chat endpoint with Claude API streaming."""
    system = request.system or "You are a helpful betting analytics assistant."
    messages = [{"role": m.role, "content": m.content} for m in request.messages]

    if request.stream:
        return StreamingResponse(
            stream_anthropic_response(system, messages),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
    else:
        # Non-streaming response (simplified)
        return {"content": "Streaming is recommended for chat."}
