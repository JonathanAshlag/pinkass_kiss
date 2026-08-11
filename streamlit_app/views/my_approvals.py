"""My Approvals page."""

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import (
    api_get, api_post, format_date, get_status_display, get_type_display
)


def _last_history_timestamp(req: dict) -> str:
    history = req.get("history") or []
    return history[-1].get("timestamp", "") if history else ""


def _render_request(req: dict, user_id: str, is_pending: bool):
    status_display = get_status_display(req.get("status", ""))
    type_display = get_type_display(req.get("type", ""))
    page_name = req.get("page_title") or req.get("page_id", "")

    with st.expander(
        f"{'⏳ ' if is_pending else '✅ '}"
        f"{page_name} | {type_display} | {status_display} | {format_date(req.get('created_at', ''))}"
    ):
        st.markdown(f"**מזהה בקשה:** `{req['request_id']}`")
        st.markdown(f"**מזהה דף:** `{req['page_id']}`")
        st.markdown(f"**מבקש:** {req.get('requested_by', '')}")

        proposed = req.get("proposed_content")
        if proposed:
            if proposed.get("title"):
                st.markdown(f"**כותרת:** {proposed['title']}")
            if proposed.get("content"):
                st.markdown("**תוכן מוצע:**")
                st.markdown(proposed["content"][:500])
            if proposed.get("trust_tier") == "verified":
                st.markdown(f"**{UI['trust_tier_requested']}**")

        # Decision form (only for pending requests)
        if is_pending:
            st.divider()
            comment = st.text_input(
                UI["comment_field"],
                key=f"comment_{req['request_id']}"
            )
            col1, col2 = st.columns(2)
            with col1:
                if st.button(UI["approve_button"], key=f"approve_{req['request_id']}"):
                    result = api_post(
                        f"/approvals/{req['request_id']}/decide",
                        user_id=user_id,
                        json_data={"decision": "approve", "comment": comment},
                    )
                    if result and "error" not in result:
                        st.toast(UI["success"], icon="✅")
                        st.rerun()
                    else:
                        st.error(f"{UI['error']}: {result.get('error', '') if result else ''}")
            with col2:
                if st.button(UI["reject_button"], key=f"reject_{req['request_id']}"):
                    result = api_post(
                        f"/approvals/{req['request_id']}/decide",
                        user_id=user_id,
                        json_data={"decision": "reject", "comment": comment},
                    )
                    if result and "error" not in result:
                        st.toast(UI["success"], icon="✅")
                        st.rerun()
                    else:
                        st.error(f"{UI['error']}: {result.get('error', '') if result else ''}")

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


def render(user_id: str):
    st.header(UI["approvals_title"])

    approvals = api_get("/me/approvals", user_id=user_id)

    if not approvals:
        st.info(UI["no_approvals"])
        return

    pending = [r for r in approvals if r.get("status") == "pending"]
    approved = [r for r in approvals if r.get("status") == "approved"]
    approved.sort(key=_last_history_timestamp, reverse=True)
    recently_approved = approved[:5]

    st.subheader(UI["pending_approvals_section"])
    if pending:
        for req in pending:
            _render_request(req, user_id, is_pending=True)
    else:
        st.info(UI["no_pending_approvals"])

    st.divider()

    st.subheader(UI["recently_approved_section"])
    if recently_approved:
        for req in recently_approved:
            _render_request(req, user_id, is_pending=False)
    else:
        st.info(UI["no_recently_approved"])
