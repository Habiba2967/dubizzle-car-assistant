"""
llm.py — thin wrapper around LiteLLM's completion() that implements the
"function calling" agent loop by hand.

Design decision:
call the model, if it asks to use a tool then run the tool and feed the result back, 
repeat until it answers in plain text.

"""

import json
import os

import litellm

from app.tools import TOOL_IMPLS, TOOL_SCHEMAS

MODEL = os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash")
MAX_TOOL_ROUNDS = 4


def run_agent_turn(messages: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """
    messages: full running conversation, OpenAI-style [{role, content}, ...]
    Returns: (assistant_reply_text, updated_messages, matched_cars)
    """
    matched_cars: list[dict] = []
    messages = list(messages)  # don't mutate caller's list in place

    for _ in range(MAX_TOOL_ROUNDS):
        response = litellm.completion(
            model=MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
        )
        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            final_text = msg.content or ""
            messages.append({"role": "assistant", "content": final_text})
            return final_text, messages, matched_cars

        # The model wants to call one or more tools.
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [tc.model_dump() for tc in tool_calls],
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            impl = TOOL_IMPLS.get(name)
            if impl is None:
                result = {"error": f"Unknown tool {name}"}
            else:
                try:
                    result = impl(**args)
                except Exception as exc:  # keep the loop alive on bad args
                    result = {"error": str(exc)}

            if name in ("search_inventory",) and isinstance(result, dict):
                matched_cars.extend(result.get("cars", []))
            if name == "get_car_details" and isinstance(result, dict) and "error" not in result:
                matched_cars.append(result)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": name,
                "content": json.dumps(result, default=str),
            })

    # Safety valve: too many tool round-trips, force a plain-text answer.
    messages.append({"role": "user", "content": "Please answer now in plain text, no more tool calls."})
    response = litellm.completion(model=MODEL, messages=messages)
    final_text = response.choices[0].message.content or "Sorry, something went wrong."
    messages.append({"role": "assistant", "content": final_text})
    return final_text, messages, matched_cars
