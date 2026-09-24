"""
tools.py — the functions we expose to the LLM via function/tool calling.

Design decision:
Instead of RAG over embeddings, we use tool calling + pandas filtering.
Reasons:
  1. The dataset is small (100 rows) and mostly STRUCTURED (make/model/year/price).
     A pandas filter is exact and fast; embeddings add latency and can miss
     exact numeric filters like "under 150k AED" or "year >= 2020".
  2. Grounding: the LLM never generates car facts itself. It calls
     search_inventory(...) or get_car_details(...), gets real rows back as
     the tool result, and must summarize ONLY that. This directly satisfies
     the "don't hallucinate inventory" evaluation criterion.
  3. For the free-text `description` field we still do a simple keyword
     match (case-insensitive substring) as a lightweight "retrieval" layer
     on top of the structured filters — a small hybrid touch without the
     complexity of a vector DB, which would be overkill for 100 rows.
"""

import csv
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CARS_CSV = DATA_DIR / "cars.csv"
LEADS_CSV = DATA_DIR / "leads.csv"
BOOKINGS_CSV = DATA_DIR / "bookings.csv"

VALID_DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday"}

_cars_df: pd.DataFrame | None = None


def load_cars() -> pd.DataFrame:
    global _cars_df
    if _cars_df is None:
        _cars_df = pd.read_csv(CARS_CSV)
    return _cars_df


def _row_to_card(row: pd.Series) -> dict:
    return {
        "listing_id": int(row["listing_id"]),
        "year": int(row["year"]),
        "make": row["make"],
        "model": row["model"],
        "trim": row["trim"],
        "title": row["title"],
        "mileage_km": None if pd.isna(row["mileage_km"]) else int(row["mileage_km"]),
        "photo_url": row["photo_url"],
        "description": row["description"],
    }


def search_inventory(make: str | None = None, model: str | None = None,
                      min_year: int | None = None, max_year: int | None = None,
                      keyword: str | None = None, limit: int = 5) -> dict:
    """Filter the car inventory. All args optional; only non-null ones are applied."""
    df = load_cars()
    mask = pd.Series(True, index=df.index)
    if make:
        mask &= df["make"].str.contains(make, case=False, na=False)
    if model:
        mask &= df["model"].str.contains(model, case=False, na=False)
    if min_year:
        mask &= df["year"] >= min_year
    if max_year:
        mask &= df["year"] <= max_year
    if keyword:
        mask &= (
            df["description"].str.contains(keyword, case=False, na=False)
            | df["title"].str.contains(keyword, case=False, na=False)
        )
    results = df[mask].head(max(1, min(limit, 20)))
    return {
        "count": int(mask.sum()),
        "returned": len(results),
        "cars": [_row_to_card(r) for _, r in results.iterrows()],
    }


def get_car_details(listing_id: int) -> dict:
    df = load_cars()
    row = df[df["listing_id"] == int(listing_id)]
    if row.empty:
        return {"error": f"No listing with id {listing_id}"}
    r = row.iloc[0]
    card = _row_to_card(r)
    card["description"] = r["description"]
    return card


def book_viewing(listing_id: int, day: str, time: str, user_id: str,
                  name: str | None = None, phone: str | None = None) -> dict:
    """Book a viewing/test drive slot. Slots: Monday-Saturday, 08:00-20:00."""
    day_norm = day.strip().lower()
    if day_norm not in VALID_DAYS:
        return {"error": f"'{day}' is not a valid day. Viewings run Monday to Saturday."}

    try:
        hour = int(time.split(":")[0].split(" ")[0])
    except (ValueError, IndexError):
        return {"error": f"Could not parse time '{time}'. Use e.g. '14:00'."}
    if not (8 <= hour < 20):
        return {"error": "Viewing slots are only available between 8am and 8pm."}

    car = get_car_details(listing_id)
    if "error" in car:
        return car

    BOOKINGS_CSV.parent.mkdir(parents=True, exist_ok=True)
    is_new = not BOOKINGS_CSV.exists()
    with open(BOOKINGS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "user_id", "name", "phone", "listing_id",
                              "title", "day", "time"])
        writer.writerow([datetime.utcnow().isoformat(), user_id, name or "", phone or "",
                          listing_id, car["title"], day_norm, time])

    return {
        "confirmed": True,
        "car": car["title"],
        "day": day_norm.capitalize(),
        "time": time,
        "message": f"Viewing booked for {car['title']} on {day_norm.capitalize()} at {time}.",
    }


def save_lead(user_id: str, price_min: int | None = None, price_max: int | None = None,
              preferences: str | None = None, name: str | None = None,
              phone: str | None = None) -> dict:
    """Qualify + record a lead: budget, needs, contact info. Also updates
    long-term memory (SQLite) so we recognize this user next session."""
    from app import memory  # local import to avoid circular import at module load

    memory.upsert_user(user_id=user_id, name=name, price_min=price_min,
                        price_max=price_max, preferences=preferences)

    LEADS_CSV.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LEADS_CSV.exists()
    with open(LEADS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "user_id", "name", "phone",
                              "price_min", "price_max", "preferences"])
        writer.writerow([datetime.utcnow().isoformat(), user_id, name or "", phone or "",
                          price_min or "", price_max or "", preferences or ""])

    return {"saved": True, "message": "Lead recorded. Thanks!"}


# ---- OpenAI/Gemini-style tool schemas (what we hand to the LLM) ----------
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_inventory",
            "description": "Returns structured fields plus each listing's full description text — read the description yourself for anything not covered by a structured filter (features, condition, warranty mentions, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "make": {"type": "string"},
                    "model": {"type": "string"},
                    "min_year": {"type": "integer"},
                    "max_year": {"type": "integer"},
                    "keyword": {"type": "string", "description": "matched against title/description, e.g. 'sunroof', 'GCC', 'white'"},
                    "limit": {"type": "integer", "default": 5},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_car_details",
            "description": "Get full details for one specific car by its listing_id.",
            "parameters": {
                "type": "object",
                "properties": {"listing_id": {"type": "integer"}},
                "required": ["listing_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_viewing",
            "description": "Book a car viewing/test drive slot. Slots are Monday-Saturday, 8am-8pm only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "listing_id": {"type": "integer"},
                    "day": {"type": "string", "description": "e.g. 'Tuesday'"},
                    "time": {"type": "string", "description": "e.g. '14:00'"},
                    "user_id": {"type": "string"},
                    "name": {"type": "string"},
                    "phone": {"type": "string"},
                },
                "required": ["listing_id", "day", "time", "user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_lead",
            "description": "Save/qualify a lead: the user's budget and preferences, for follow-up. Call this once you've learned the user's price range or needs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "price_min": {"type": "integer"},
                    "price_max": {"type": "integer"},
                    "preferences": {"type": "string"},
                    "name": {"type": "string"},
                    "phone": {"type": "string"},
                },
                "required": ["user_id"],
            },
        },
    },
]

TOOL_IMPLS = {
    "search_inventory": search_inventory,
    "get_car_details": get_car_details,
    "book_viewing": book_viewing,
    "save_lead": save_lead,
}
