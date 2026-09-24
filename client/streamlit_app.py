"""
streamlit_app.py — chat client for the FastAPI backend.

Run with:  uv run streamlit run client/streamlit_app.py

This file talks to the backend ONLY over HTTP (httpx) - it holds no LLM
logic, no dataset, no memory of its own. That boundary (thin client, all
intelligence + state behind the API) is the "clean API design" the
assessment is evaluating.
"""

import httpx
import streamlit as st

API_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="dubizzle cars assistant", page_icon="🚗")
st.title("🚗 dubizzle cars assistant")

# ---- Sidebar: simulate "logging in" as a user_id, to demo long-term memory ----
with st.sidebar:
    st.header("Session")
    st.caption("Enter the same user ID next time you run this app to test "
               "long-term memory recall across sessions.")
    user_id_input = st.text_input("Your user ID (optional)", value=st.session_state.get("user_id_input", ""))
    name_input = st.text_input("Your name (only used if this is a new ID)")
    if st.button("Start / restart session"):
        resp = httpx.post(f"{API_URL}/session/start", json={
            "user_id": user_id_input or None,
            "name": name_input or None,
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        st.session_state.session_id = data["session_id"]
        st.session_state.user_id_input = data["user_id"]
        st.session_state.chat_history = [("assistant", data["greeting"])]
        st.session_state.returning_user = data["returning_user"]
        st.rerun()

if "session_id" not in st.session_state:
    st.info("Enter an optional user ID and click **Start / restart session** in the sidebar to begin.")
    st.stop()

if st.session_state.get("returning_user"):
    st.success(f"Recognized returning user: {st.session_state.user_id_input}")

# ---- Chat history ----
for role, content in st.session_state.chat_history:
    with st.chat_message(role):
        st.markdown(content)

# ---- Chat input ----
if prompt := st.chat_input("Ask about cars, book a viewing, tell me your budget..."):
    st.session_state.chat_history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            resp = httpx.post(f"{API_URL}/chat", json={
                "session_id": st.session_state.session_id,
                "message": prompt,
            }, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        st.markdown(data["reply"])

        cars = data.get("matched_cars", [])
        if cars:
            with st.expander(f"📋 {len(cars)} matching listing(s) referenced"):
                for c in cars:
                    st.write(
                        f"**#{c['listing_id']} — {c['year']} {c['make'].title()} {c['model'].title()}** "
                        f"({c.get('trim', '')})"
                    )
                    if c.get("photo_url"):
                        st.image(c["photo_url"], width=200)

    st.session_state.chat_history.append(("assistant", data["reply"]))
