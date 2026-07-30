"""Create and edit page."""

import streamlit as st
from datetime import date

from streamlit_app.strings import UI
from streamlit_app.helpers import api_post, api_put, api_get
from streamlit_app.state import (
    EDITING_PAGE, VIEWING_PAGE, PP_CTX, PP_SEL_ID, PP_SEL_TITLE, PP_RESULTS,
    NAV_BROWSE,
    navigate_to,
)


def _render_parent_picker(user_id: str, context: str, initial_parent_id: str | None = None) -> str | None:
    """Fuzzy-search picker for the parent page. Returns selected page_id or None."""
    # Reset when the context changes (different page being edited, or switching create↔edit)
    if st.session_state.get(PP_CTX) != context:
        st.session_state[PP_CTX] = context
        st.session_state[PP_SEL_ID] = initial_parent_id
        st.session_state[PP_SEL_TITLE] = None
        st.session_state[PP_RESULTS] = None

    # Lazy-load title when only page_id is known
    if st.session_state[PP_SEL_ID] and not st.session_state[PP_SEL_TITLE]:
        fetched = api_get(f"/pages/{st.session_state[PP_SEL_ID]}", user_id=user_id)
        if fetched:
            st.session_state[PP_SEL_TITLE] = fetched.get("title", st.session_state[PP_SEL_ID])

    st.markdown(f"**{UI['parent_page']}**")

    if st.session_state[PP_SEL_ID]:
        col1, col2 = st.columns([4, 1])
        with col1:
            st.info(f"📄 {st.session_state[PP_SEL_TITLE] or st.session_state[PP_SEL_ID]}")
        with col2:
            if st.button(UI["clear_parent"], key=f"pp_clear_{context}"):
                st.session_state[PP_SEL_ID] = None
                st.session_state[PP_SEL_TITLE] = None
                st.session_state[PP_RESULTS] = None
                st.rerun()
    else:
        st.caption(UI["no_parent"])

    col1, col2 = st.columns([4, 1])
    with col1:
        query = st.text_input(
            UI["parent_search_label"],
            key=f"pp_query_{context}",
            placeholder=UI["search_placeholder"],
            label_visibility="collapsed",
        )
    with col2:
        if st.button(UI["search_button"], key=f"pp_search_{context}"):
            if query:
                results = api_get("/pages/search", user_id=user_id, params={"query": query})
                st.session_state[PP_RESULTS] = results or []

    results = st.session_state.get(PP_RESULTS)
    if results is not None:
        if results:
            for page in results[:8]:
                if st.button(f"📄 {page['title']}", key=f"pp_sel_{context}_{page['page_id']}"):
                    st.session_state[PP_SEL_ID] = page["page_id"]
                    st.session_state[PP_SEL_TITLE] = page["title"]
                    st.session_state[PP_RESULTS] = None
                    st.rerun()
        else:
            st.caption(UI["no_results"])

    return st.session_state[PP_SEL_ID]


def render_create(user_id: str):
    st.header(UI["create_page"])

    title = st.text_input(UI["title_field"])
    description = st.text_input(UI["description_field"])
    parent_id = _render_parent_picker(user_id, context="create")
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
        if not description:
            st.error("יש להזין תיאור קצר")
            return

        data = {
            "title": title,
            "description": description,
            "content": content,
            "parent_id": parent_id,
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
            st.session_state[PP_CTX] = None
        else:
            st.error(f"{UI['error']}: {result.get('error', '') if result else 'שגיאת תקשורת'}")


def render_edit(user_id: str):
    """Render edit form for an existing page."""
    page_id = st.session_state[EDITING_PAGE]
    page = api_get(f"/pages/{page_id}", user_id=user_id)

    if not page:
        st.error("לא ניתן לטעון את הדף")
        return

    st.header(UI["edit_page_existing"])
    st.caption(page.get("title", ""))

    title = st.text_input(UI["title_field"], value=page.get("title", ""))
    description = st.text_input(UI["description_field"], value=page.get("description", "") or "")
    parent_id = _render_parent_picker(
        user_id,
        context=f"edit_{page_id}",
        initial_parent_id=page.get("parent_id"),
    )
    content = st.text_area(UI["content_field"], value=page.get("content", ""), height=300)

    current_date = None
    if page.get("next_approval_date"):
        try:
            parts = page["next_approval_date"].split("-")
            current_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            pass

    approval_date = st.date_input(UI["approval_date"], value=current_date)

    col1, col2 = st.columns(2)
    with col1:
        if st.button(UI["save_button"]):
            data = {}
            if title != page.get("title"):
                data["title"] = title
            if description != (page.get("description") or ""):
                data["description"] = description
            if content != page.get("content"):
                data["content"] = content
            if parent_id != page.get("parent_id"):
                data["parent_id"] = parent_id
            if approval_date:
                data["next_approval_date"] = approval_date.isoformat()

            if data:
                result = api_put(f"/pages/{page_id}", user_id=user_id, json_data=data)
                if result and "error" not in result:
                    st.success(UI["success"])
                    navigate_to(NAV_BROWSE, **{
                        EDITING_PAGE: None,
                        VIEWING_PAGE: page_id,
                        PP_CTX: None,
                    })
                else:
                    st.error(f"{UI['error']}: {result.get('error', '') if result else ''}")
            else:
                st.info("לא בוצעו שינויים")

    with col2:
        if st.button("ביטול"):
            navigate_to(NAV_BROWSE, **{
                EDITING_PAGE: None,
                VIEWING_PAGE: page_id,
                PP_CTX: None,
            })
