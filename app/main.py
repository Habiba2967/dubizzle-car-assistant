"""
main.py — FastAPI backend.

Endpoints:
  POST /session/start   -> start a session; recognizes returning users (long-term memory)
  POST /chat            -> send a message, get the assistant's reply (short-term memory
                            is the running message list kept per session_id, in-memory)
  GET  /inventory/search -> direct structured search (useful for testing / a non-chat UI)
  GET  /health

Short-term vs long-term memory (explain this in the interview):
  - SHORT-TERM: `SESSIONS[session_id]` below is just a Python dict living in this
    process's memory. It holds the full running chat transcript for the session,
    which is how the agent resolves "is there a warranty on it?" after "what's the
    mileage on the first Honda?" without the user repeating themselves. It resets
    if the server restarts - that's fine for a single conversation session.
  - LONG-TERM: app/memory.py persists to SQLite on disk, keyed by user_id (not
    session_id), so it survives restarts and is what lets us recognize the SAME
    user across two completely different sessions ("welcome back, Habiba").
"""

import uuid

from dotenv import load_dotenv
load_dotenv()  # picks up GEMINI_API_KEY from .env before litellm needs it

from fastapi import FastAPI, HTTPException

from app import memory, tools
from app.llm import run_agent_turn
from app.prompts import SYSTEM_PROMPT
from app.schemas import ChatRequest, ChatResponse, StartSessionRequest, StartSessionResponse

app = FastAPI(title="dubizzle cars AI Assistant")

# session_id -> list of OpenAI-style chat messages (short-term memory)
SESSIONS: dict[str, list[dict]] = {}
# session_id -> user_id, so /chat knows who it's saving leads for
SESSION_USER: dict[str, str] = {}


@app.on_event("startup")
def startup():
    memory.init_db()
    tools.load_cars()  # warm the inventory cache


@app.get("/health")
def health():
    return {"status": "ok", "cars_loaded": len(tools.load_cars())}


@app.post("/session/start", response_model=StartSessionResponse)
def start_session(req: StartSessionRequest):
    user_id = req.user_id or f"guest-{uuid.uuid4().hex[:8]}"
    existing = memory.get_user(user_id)
    returning = existing is not None

    system_content = SYSTEM_PROMPT
    if returning:
        system_content += "\n\n" + memory.user_memory_summary(user_id)
        greeting = (
            f"Welcome back{', ' + existing['name'] if existing['name'] else ''}! "
            "I remember what you were looking for last time - want to continue "
            "where we left off, or start a new search?"
        )
    else:
        if req.name:
            memory.upsert_user(user_id=user_id, name=req.name)
        greeting = "Hi! I'm the dubizzle cars assistant. What kind of car are you looking for today?"

    session_id = uuid.uuid4().hex
    SESSIONS[session_id] = [
        {"role": "system", "content": system_content},
        {"role": "assistant", "content": greeting},
    ]
    SESSION_USER[session_id] = user_id

    return StartSessionResponse(
        session_id=session_id, user_id=user_id, greeting=greeting, returning_user=returning,
    )


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if req.session_id not in SESSIONS:
        raise HTTPException(404, "Unknown session_id. Call /session/start first.")

    user_id = SESSION_USER[req.session_id]
    messages = SESSIONS[req.session_id]
    messages.append({"role": "user", "content": req.message})

    # Make sure the model always has this session's user_id available for
    # tool calls like save_lead/book_viewing without asking the user for it.
    messages_for_llm = messages + [
        {"role": "system", "content": f"(internal) current user_id = {user_id}"}
    ]

    reply, updated, matched_cars = run_agent_turn(messages_for_llm)
    # store only the real conversation, not the injected internal note
    SESSIONS[req.session_id] = [m for m in updated if m.get("content") != f"(internal) current user_id = {user_id}"]

    return ChatResponse(session_id=req.session_id, reply=reply, matched_cars=matched_cars)


@app.get("/inventory/search")
def inventory_search(make: str | None = None, model: str | None = None,
                      min_year: int | None = None, max_year: int | None = None,
                      min_price: int | None = None, max_price: int | None = None,
                      keyword: str | None = None, limit: int = 5):
    return tools.search_inventory(make, model, min_year, max_year, min_price, max_price, keyword, limit)
