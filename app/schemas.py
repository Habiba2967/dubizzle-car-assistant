from typing import Optional
from pydantic import BaseModel


class StartSessionRequest(BaseModel):
    user_id: Optional[str] = None   # if provided, we try to recognize a returning user
    name: Optional[str] = None      # optional, used if this is a brand-new user_id


class StartSessionResponse(BaseModel):
    session_id: str
    user_id: str
    greeting: str
    returning_user: bool


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    # any cars the assistant referenced this turn, so the client can render cards
    matched_cars: list[dict] = []
