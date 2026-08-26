"""My Requests page."""

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import api_get, format_date, get_status_display, get_type_display


def render(user_id: str):
    st.header(UI["my_requests_title"])

    requests = api_get("/me/requests", user_id=user_id)

    if not requests:
        st.info(UI["no_requests"])
        return

    for req in requests:
        status_display = get_status_display(req.get("status", ""))
        type_display = get_type_display(req.get("type", ""))

        page_name = req.get("page_title") or req.get("page_id", "")

        with st.expander(
            f"{page_name} | {type_display} | {status_display} | {format_date(req.get('created_at', ''))}"
        ):
            st.markdown(f"**מזהה בקשה:** `{req['request_id']}`")
            st.markdown(f"**מזהה דף:** `{req['page_id']}`")
            st.markdown(f"**שלב נוכחי:** {req.get('current_step', 0)}")

            # Show proposed content if exists
            proposed = req.get("proposed_content")
            if proposed:
                if proposed.get("title"):
                    st.markdown(f"**כותרת מוצעת:** {proposed['title']}")
                if proposed.get("content"):
                    with st.container():
                        st.markdown("**תוכן מוצע:**")
                        st.markdown(proposed["content"])
                if proposed.get("trust_tier") == "verified":
                    st.markdown(f"**{UI['trust_tier_requested']}**")

            # History
            history = req.get("history", [])
            if history:
                st.divider()
                st.markdown(f"**{UI['decision_history']}:**")
                for entry in history:
                    st.markdown(
                        f"- {format_date(entry.get('timestamp'))} | "
                        f"{entry.get('user_id', '')} | "
                        f"{entry.get('decision', '')} | "
                        f"{entry.get('comment', '')}"
                    )
