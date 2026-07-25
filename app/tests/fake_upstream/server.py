"""Minimal Fake OpenAI-compatible upstream for integration tests (no real API keys)."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Fake OpenAI Upstream")
CALLS: list[dict[str, Any]] = []


@app.post("/v1/chat/completions")
async def chat(request: Request, authorization: Optional[str] = Header(default=None)):
    body = await request.json()
    CALLS.append({"body": body, "auth": authorization, "ts": time.time()})
    model = body.get("model") or "fake-model"
    stream = bool(body.get("stream"))
    # scenario switches via model name suffix
    if str(model).endswith("-429"):
        return JSONResponse(status_code=429, content={"error": {"message": "rate limit"}})
    if str(model).endswith("-500"):
        return JSONResponse(status_code=500, content={"error": {"message": "upstream boom"}})
    if str(model).endswith("-bad"):
        content = ""  # empty → quality failure candidate
    elif str(model).endswith("-arith"):
        content = "2+2=5"
    else:
        content = "Hello from fake upstream. 2+2=4"

    usage = {
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "total_tokens": 20,
    }
    if stream:

        async def gen():
            for ch in content:
                chunk = {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": ch}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
            final = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": usage,
            }
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


@app.get("/healthz")
def health():
    return {"ok": True, "calls": len(CALLS)}


@app.post("/__reset")
def reset():
    CALLS.clear()
    return {"ok": True}
