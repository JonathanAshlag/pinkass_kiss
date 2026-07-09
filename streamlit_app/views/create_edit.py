"""Create and edit page."""

import streamlit as st
from datetime import date

from streamlit_app.strings import UI
from streamlit_app.helpers import api_post, api_put, api_get


def render_create(user_id: str):
    st.header(UI["create_page"])

    # Check if we're editing
    if "editing_page" in st.session_state and st.session_state.editing_page:
        _render_edit(user_id)
        return

    title = st.text_input(UI["title_field"])
    parent_id = st.text_input(UI["parent_page"], placeholder=UI["root_page"])
    content = st.text_area(UI["content_field"], height=300)
    approval_date = st.date_input(
        UI["approval_date"],
        value=None,
        min_value=date.today(),
    )

    if st.button(UI["save_button"]):
        if not title:
            st.error("יש להזין כותרת")
            return

        data = {
            "title": title,
            "content": content,
            "parent_id": parent_id if parent_id else None,
        }
        if approval_date:
            data["next_approval_date"] = approval_date.isoformat()

        result = api_post("/pages", user_id=user_id, json_data=data)
        if result and "error" not in result:
            status = result.get("status", "")
            if status == "pending_approval":
                st.success(f"{UI['success']} — {UI['status_pending_approval']}")
            else:
                st.success(f"{UI['success']} — {UI['status_published']}")
        else:
            st.error(f"{UI['error']}: {result.get('error', '') if result else 'שגיאת תקשורת'}")


def _render_edit(user_id: str):
    """Render edit form for existing page."""
    page_id = st.session_state.editing_page
    page = st.session_state.get("edit_data") or api_get(f"/pages/{page_id}", user_id=user_id)

    if not page:
        st.error("לא ניתן לטעון את הדף")
        return

    st.subheader(f"{UI['edit_page']}: {page.get('title', '')}")

    title = st.text_input(UI["title_field"], value=page.get("title", ""))
    parent_id = st.text_input(UI["parent_page"], value=page.get("parent_id", "") or "")
    content = st.text_area(UI["content_field"], value=page.get("content", ""), height=300)

    current_date = None
    if page.get("next_approval_date"):
        try:
            parts = page["next_approval_date"].split("-")
            current_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            pass

    approval_date = st.date_input(
        UI["approval_date"],
        value=current_date,
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button(UI["save_button"]):
            data = {}
            if title != page.get("title"):
                data["title"] = title
            if content != page.get("content"):
                data["content"] = content
            if parent_id != (page.get("parent_id") or ""):
                data["parent_id"] = parent_id if parent_id else None
            if approval_date:
                data["next_approval_date"] = approval_date.isoformat()

            if data:
                result = api_put(f"/pages/{page_id}", user_id=user_id, json_data=data)
                if result and "error" not in result:
                    st.success(UI["success"])
                    st.session_state.editing_page = None
                    st.session_state.edit_data = None
                else:
                    st.error(f"{UI['error']}: {result.get('error', '') if result else ''}")
            else:
                st.info("לא בוצעו שינויים")

    with col2:
        if st.button("ביטול"):
            st.session_state.editing_page = None
            st.session_state.edit_data = None
            st.rerun()
