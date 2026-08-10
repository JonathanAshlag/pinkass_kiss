"""Browse page — hierarchical tree and page viewer."""

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import api_get, format_date, get_status_display, get_allowed_tags, API_URL
from streamlit_app.state import (
    USER_DATA, VIEWING_PAGE, EDITING_PAGE, PP_CTX,
    BROWSE_SELECTED_TAG, BROWSE_EXPANDED_IDS,
    NAV_BROWSE, NAV_CREATE,
    navigate_to,
)

UNTAGGED = "__untagged__"


def _filter_tag_chain(pages: list, selected_tag: str) -> tuple[list, dict]:
    """Restrict pages to those carrying selected_tag (or untagged) plus their structural ancestors."""
    by_id = {p["page_id"]: p for p in pages}

    def qualifies(p: dict) -> bool:
        tags = p.get("tags") or []
        return not tags if selected_tag == UNTAGGED else selected_tag in tags

    included_ids: set[str] = set()
    for p in pages:
        if not qualifies(p):
            continue
        visited: set[str] = set()
        cur = p
        while cur and cur["page_id"] not in visited:
            visited.add(cur["page_id"])
            included_ids.add(cur["page_id"])
            parent_id = cur.get("parent_id")
            cur = by_id.get(parent_id) if parent_id else None

    filtered = [p for p in pages if p["page_id"] in included_ids]
    children_by_parent: dict = {}
    for p in filtered:
        parent_id = p.get("parent_id")
        if parent_id in included_ids:
            children_by_parent.setdefault(parent_id, []).append(p)
    roots = [p for p in filtered if not p.get("parent_id") or p["parent_id"] not in included_ids]
    return roots, children_by_parent


def _render_tag_tree_node(page: dict, children_by_parent: dict, level: int):
    """Render a tree node; a single click toggles its children and loads its detail panel."""
    expanded_ids = st.session_state[BROWSE_EXPANDED_IDS]
    pid = page["page_id"]
    kids = children_by_parent.get(pid, [])
    prefix = "─ " * level
    icon = "📂" if kids else "📄"
    label = f"{prefix}{icon} {page['title']}"
    if st.button(label, key=f"tagtree_{pid}"):
        st.session_state[VIEWING_PAGE] = pid
        if pid in expanded_ids:
            expanded_ids.discard(pid)
        else:
            expanded_ids.add(pid)
        st.rerun()
    if pid in expanded_ids:
        for child in kids:
            _render_tag_tree_node(child, children_by_parent, level + 1)


def _render_layer1(pages: list, user_id: str):
    """Layer 1: one folder per tag, plus an untagged folder."""
    st.subheader(UI["tag_folders"])
    allowed_tags = get_allowed_tags(user_id)
    counts = {t: 0 for t in allowed_tags}
    untagged_count = 0
    for p in pages:
        p_tags = p.get("tags") or []
        if not p_tags:
            untagged_count += 1
        else:
            for t in p_tags:
                if t in counts:
                    counts[t] += 1
    for tag in allowed_tags:
        if st.button(f"📁 {tag} ({counts[tag]})", key=f"tagfolder_{tag}"):
            st.session_state[BROWSE_SELECTED_TAG] = tag
            st.session_state[BROWSE_EXPANDED_IDS] = set()
            st.rerun()
    if st.button(f"📁 {UI['untagged_folder']} ({untagged_count})", key="tagfolder_untagged"):
        st.session_state[BROWSE_SELECTED_TAG] = UNTAGGED
        st.session_state[BROWSE_EXPANDED_IDS] = set()
        st.rerun()


def _render_layer2(pages: list):
    """Layer 2: the hierarchy chain leading to pages carrying the selected tag."""
    selected = st.session_state[BROWSE_SELECTED_TAG]
    label = UI["untagged_folder"] if selected == UNTAGGED else selected
    if st.button(f"← {UI['back_to_folders']}"):
        st.session_state[BROWSE_SELECTED_TAG] = None
        st.session_state[BROWSE_EXPANDED_IDS] = set()
        st.rerun()
    st.subheader(f"{UI['page_tree']}: {label}")
    roots, children_by_parent = _filter_tag_chain(pages, selected)
    if not roots:
        st.info(UI["no_pages"])
    for root in roots:
        _render_tag_tree_node(root, children_by_parent, 0)


def render(user_id: str):
    st.header(UI["nav_browse"])

    if VIEWING_PAGE not in st.session_state:
        st.session_state[VIEWING_PAGE] = None
    if BROWSE_SELECTED_TAG not in st.session_state:
        st.session_state[BROWSE_SELECTED_TAG] = None
    if BROWSE_EXPANDED_IDS not in st.session_state:
        st.session_state[BROWSE_EXPANDED_IDS] = set()

    col1, col2 = st.columns([1, 2])

    with col1:
        tree_data = api_get("/pages/tree", user_id=user_id)
        if not tree_data:
            st.subheader(UI["page_tree"])
            st.info(UI["no_pages"])
        elif st.session_state[BROWSE_SELECTED_TAG] is None:
            _render_layer1(tree_data, user_id)
        else:
            _render_layer2(tree_data)

    with col2:
        if st.session_state[VIEWING_PAGE]:
            page = api_get(f"/pages/{st.session_state[VIEWING_PAGE]}", user_id=user_id)
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
                                st.session_state[VIEWING_PAGE] = ref["page_id"]
                                st.rerun()

                # History
                with st.expander(UI["page_history"]):
                    history = api_get(f"/pages/{st.session_state[VIEWING_PAGE]}/history", user_id=user_id)
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
                user_data = st.session_state[USER_DATA]
                if user_data and user_data.get("permission_level") in ("editor", "admin"):
                    st.divider()
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(UI["edit_page"], key="edit_btn"):
                            navigate_to(NAV_CREATE, **{
                                EDITING_PAGE: page["page_id"],
                                PP_CTX: None,
                            })
                    with c2:
                        if st.button(UI["delete_button"], key="delete_btn"):
                            from streamlit_app.helpers import api_delete
                            result = api_delete(f"/pages/{page['page_id']}", user_id=user_id)
                            if result and "error" not in result:
                                st.success(UI["success"])
                                st.session_state[VIEWING_PAGE] = None
                                st.rerun()
            else:
                st.error("לא ניתן לטעון את הדף")
