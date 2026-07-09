"""Browse page — hierarchical tree and page viewer."""

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import (
    api_get, format_date, get_status_display, API_URL
)


def _build_tree(pages: list) -> dict:
    """Build a nested tree structure from flat page list."""
    by_id = {p["page_id"]: p for p in pages}
    roots = []
    children = {}
    for p in pages:
        parent = p.get("parent_id")
        if not parent or parent not in by_id:
            roots.append(p)
        else:
            children.setdefault(parent, []).append(p)
    return roots, children


def _render_tree_node(page: dict, children: dict, level: int, user_id: str):
    """Render a tree node with expander."""
    prefix = "─ " * level
    label = f"{prefix}📄 {page['title']}"
    if st.button(label, key=f"tree_{page['page_id']}"):
        st.session_state.viewing_page = page["page_id"]
        st.rerun()
    for child in children.get(page["page_id"], []):
        _render_tree_node(child, children, level + 1, user_id)


def render(user_id: str):
    st.header(UI["nav_browse"])

    if "viewing_page" not in st.session_state:
        st.session_state.viewing_page = None

    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader(UI["page_tree"])
        tree_data = api_get("/pages/tree", user_id=user_id)
        if tree_data:
            roots, children = _build_tree(tree_data)
            for root in roots:
                _render_tree_node(root, children, 0, user_id)
        else:
            st.info(UI["no_pages"])

    with col2:
        if st.session_state.viewing_page:
            page = api_get(f"/pages/{st.session_state.viewing_page}", user_id=user_id)
            if page:
                st.subheader(page["title"])
                st.caption(f"{UI['page_status']}: {get_status_display(page.get('status', ''))}")
                st.markdown(page.get("content", ""))

                # References
                refs = page.get("references", [])
                if refs:
                    st.divider()
                    st.markdown(f"**{UI['page_references']}:**")
                    for ref in refs:
                        if ref["type"] == "file" and ref.get("file_id"):
                            st.markdown(f"📎 [{UI['file_reference']}]({API_URL}/files/{ref['file_id']})")
                        elif ref["type"] == "page" and ref.get("page_id"):
                            if st.button(f"📄 {UI['linked_page']}: {ref['page_id'][:8]}...", key=f"ref_{ref['page_id']}"):
                                st.session_state.viewing_page = ref["page_id"]
                                st.rerun()

                # History
                with st.expander(UI["page_history"]):
                    history = api_get(f"/pages/{st.session_state.viewing_page}/history", user_id=user_id)
                    if history:
                        for entry in reversed(history):
                            st.markdown(
                                f"**{format_date(entry.get('timestamp'))}** | "
                                f"{entry.get('user_id', '')} | "
                                f"{entry.get('action', '')} | "
                                f"{entry.get('comment', '')}"
                            )
                    else:
                        st.info("אין היסטוריה")

                # Edit/Delete buttons
                user_data = st.session_state.user_data
                if user_data and user_data.get("permission_level") in ("editor", "admin"):
                    st.divider()
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(UI["edit_page"], key="edit_btn"):
                            st.session_state.editing_page = page["page_id"]
                            st.session_state.edit_data = page
                    with c2:
                        if st.button(UI["delete_button"], key="delete_btn"):
                            from streamlit_app.helpers import api_delete
                            result = api_delete(f"/pages/{page['page_id']}", user_id=user_id)
                            if result and "error" not in result:
                                st.success(UI["success"])
                                st.session_state.viewing_page = None
                                st.rerun()
            else:
                st.error("לא ניתן לטעון את הדף")
