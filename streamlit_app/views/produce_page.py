"""Document upload/produce page — two-phase ingestion review."""

import time

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import api_post, api_get, render_alias_chips
from streamlit_app.state import PRODUCE_BATCH_ID


def _start_batch(user_id: str, uploaded_files, ingestion_context: str) -> None:
    files = [("files", (f.name, f.read(), f.type or "application/octet-stream")) for f in uploaded_files]
    data = {"ingestion_context": ingestion_context} if ingestion_context else None
    result = api_post("/produce", user_id=user_id, files=files, data=data)
    if result and "error" not in result and result.get("batch_id"):
        st.session_state[PRODUCE_BATCH_ID] = result["batch_id"]
        st.rerun()
    else:
        st.error(f"{UI['error']}: {result.get('error', '') if result else 'שגיאת תקשורת'}")


def _render_upload_form(user_id: str) -> None:
    st.info(UI["supported_formats"])

    uploaded_files = st.file_uploader(
        UI["upload_files"],
        accept_multiple_files=True,
        type=["pdf", "docx", "html", "htm", "txt", "md"],
    )
    ingestion_context = st.text_area(
        UI["ingestion_context_field"],
        placeholder=UI["ingestion_context_placeholder"],
    )

    if st.button(UI["upload_button"]) and uploaded_files:
        with st.spinner(UI["loading"]):
            _start_batch(user_id, uploaded_files, ingestion_context)


def _approve(user_id: str, batch_id: str, candidate_id: str, edits: dict | None = None) -> bool:
    result = api_post(
        f"/produce/batches/{batch_id}/candidates/{candidate_id}/approve",
        user_id=user_id,
        json_data=edits or {},
    )
    if not result or "error" in result:
        st.error(f"{UI['error']}: {result.get('error', '') if result else ''}")
        return False
    return True


def _reject(user_id: str, batch_id: str, candidate_id: str) -> bool:
    result = api_post(f"/produce/batches/{batch_id}/candidates/{candidate_id}/reject", user_id=user_id)
    if not result or "error" in result:
        st.error(f"{UI['error']}: {result.get('error', '') if result else ''}")
        return False
    return True


def _get_current_values(cid: str, c: dict) -> dict:
    """Current field values for this candidate: the saved edit draft if one exists
    (survives leaving edit mode — see _render_edit_form), otherwise the server's
    original proposal."""
    draft = st.session_state.get(f"draft_{cid}")
    if draft:
        return draft
    return {
        "title": c["title"],
        "description": c["description"],
        "content": c["content"],
        "aliases": ", ".join(c["aliases"]),
    }


def _collect_edits(c: dict, cid: str) -> dict:
    values = _get_current_values(cid, c)
    return {
        "title": values["title"],
        "description": values["description"],
        "content": values["content"],
        "aliases": [a.strip() for a in values["aliases"].split(",") if a.strip()],
    }


def _type_label(action: str) -> str:
    return {
        "create": UI["candidate_create_header"],
        "merge": UI["candidate_merge_header"],
        "link": UI["candidate_link_header"],
    }.get(action, action)


def _render_view_pane(cid: str, c: dict) -> None:
    """Render the candidate's current (possibly edited) fields as a read-only page view."""
    values = _get_current_values(cid, c)
    aliases = [a.strip() for a in values["aliases"].split(",") if a.strip()]

    st.subheader(values["title"])
    render_alias_chips(aliases)
    if values["description"]:
        st.info(f"**{UI['description_field']}:** {values['description']}")
    st.markdown(values["content"] or "")


def _render_original_pane(c: dict) -> None:
    """Render the live matched page as a read-only page view (never editable)."""
    st.subheader(c.get("matched_page_title") or "")
    render_alias_chips(c.get("matched_page_aliases"))
    if c.get("matched_page_description"):
        st.info(f"**{UI['description_field']}:** {c['matched_page_description']}")
    st.markdown(c.get("matched_page_content") or "")


def _render_edit_form(cid: str, c: dict, title_editable: bool = True) -> None:
    values = _get_current_values(cid, c)
    # For a merge, the target page already exists and its title is immutable — the
    # server drops any title in the update regardless, so display (and save into the
    # draft) the live matched page's actual title rather than the candidate's own
    # extracted one, which would otherwise look editable but silently do nothing.
    displayed_title = values["title"] if title_editable else (c.get("matched_page_title") or values["title"])
    st.text_input(UI["title_field"], value=displayed_title, key=f"title_{cid}", disabled=not title_editable)
    if not title_editable:
        st.caption(UI["title_immutable_hint"])
    st.text_input(UI["description_field"], value=values["description"], key=f"desc_{cid}")
    st.text_area(UI["content_field"], value=values["content"], height=250, key=f"content_{cid}")
    st.text_input(UI["aliases_field"], value=values["aliases"], key=f"aliases_{cid}")

    # Snapshot the widgets' live values into a plain (non-widget) session_state entry.
    # Streamlit purges a widget's session_state slot once that widget stops being
    # instantiated on a rerun (e.g. switching to view mode) — without this snapshot,
    # re-entering edit mode (or approving from view mode) would silently revert to the
    # server's original proposal instead of what the user typed.
    st.session_state[f"draft_{cid}"] = {
        "title": st.session_state[f"title_{cid}"],
        "description": st.session_state[f"desc_{cid}"],
        "content": st.session_state[f"content_{cid}"],
        "aliases": st.session_state[f"aliases_{cid}"],
    }


def _render_view_or_edit(cid: str, c: dict, title_editable: bool = True) -> None:
    """View-mode by default (mirrors a published page's view), with an Edit toggle."""
    editing_key = f"editing_{cid}"
    if st.session_state.get(editing_key):
        _render_edit_form(cid, c, title_editable=title_editable)
        if st.button(UI["candidate_done_editing_button"], key=f"done_edit_{cid}"):
            st.session_state[editing_key] = False
            st.session_state[f"row_open_{cid}"] = True
            st.rerun()
    else:
        _render_view_pane(cid, c)
        if st.button(UI["candidate_edit_button"], key=f"edit_{cid}"):
            st.session_state[editing_key] = True
            st.session_state[f"row_open_{cid}"] = True
            st.rerun()


def _render_approve_reject(user_id: str, batch_id: str, cid: str, c: dict, with_edits: bool) -> None:
    col1, col2 = st.columns(2)
    with col1:
        if st.button(UI["approve_button"], key=f"approve_{cid}"):
            edits = _collect_edits(c, cid) if with_edits else None
            if _approve(user_id, batch_id, cid, edits):
                st.rerun()
    with col2:
        if st.button(UI["reject_button"], key=f"reject_{cid}"):
            if _reject(user_id, batch_id, cid):
                st.rerun()


def _render_create_candidate(user_id: str, batch_id: str, c: dict) -> None:
    cid = c["candidate_id"]
    _render_view_or_edit(cid, c)
    _render_approve_reject(user_id, batch_id, cid, c, with_edits=True)


def _render_merge_candidate(user_id: str, batch_id: str, c: dict) -> None:
    cid = c["candidate_id"]
    left, right = st.columns(2)
    with left:
        st.caption(UI["original_content_label"])
        _render_original_pane(c)
    with right:
        st.caption(UI["proposed_content_label"])
        _render_view_or_edit(cid, c, title_editable=False)
    _render_approve_reject(user_id, batch_id, cid, c, with_edits=True)


def _render_link_candidate(user_id: str, batch_id: str, c: dict) -> None:
    cid = c["candidate_id"]
    st.info(UI["add_reference_confirm"].format(title=c["matched_page_title"]))
    _render_approve_reject(user_id, batch_id, cid, c, with_edits=False)


def _render_candidate_row(user_id: str, batch_id: str, c: dict) -> None:
    cid = c["candidate_id"]
    label = f"{c['title']}  —  {_type_label(c['action'])}"
    with st.expander(label, expanded=st.session_state.get(f"row_open_{cid}", False)):
        if c["action"] == "create":
            _render_create_candidate(user_id, batch_id, c)
        elif c["action"] == "merge":
            _render_merge_candidate(user_id, batch_id, c)
        else:
            _render_link_candidate(user_id, batch_id, c)


def _render_review(user_id: str, batch_id: str, batch: dict) -> None:
    candidates = batch.get("candidates", [])
    pending = [c for c in candidates if c["status"] == "pending"]

    if not candidates:
        st.info(UI["produce_no_candidates"])
    elif not pending:
        approved = len([c for c in candidates if c["status"] == "approved"])
        st.success(f"{UI['success']} — {UI['generated_pages']}: {approved}")
    else:
        st.subheader(UI["produce_review_title"])
        col1, col2 = st.columns(2)
        with col1:
            if st.button(UI["approve_all_button"]):
                for c in pending:
                    _approve(user_id, batch_id, c["candidate_id"])
                st.rerun()
        with col2:
            if st.button(UI["reject_all_button"]):
                for c in pending:
                    _reject(user_id, batch_id, c["candidate_id"])
                st.rerun()

        by_file: dict[str, list[dict]] = {}
        for c in pending:
            by_file.setdefault(c["filename"], []).append(c)

        for filename, group in by_file.items():
            st.markdown(f"**{filename}**")
            for c in group:
                _render_candidate_row(user_id, batch_id, c)

    if not pending:
        if st.button(UI["start_new_ingestion"]):
            st.session_state[PRODUCE_BATCH_ID] = None
            st.rerun()


def render(user_id: str):
    st.header(UI["produce_title"])

    batch_id = st.session_state.get(PRODUCE_BATCH_ID)
    if not batch_id:
        _render_upload_form(user_id)
        return

    status_placeholder = st.empty()
    batch = api_get(f"/produce/batches/{batch_id}", user_id=user_id)
    while batch and batch.get("status") == "running":
        status_placeholder.info(batch.get("progress_message") or UI["loading"])
        time.sleep(1.5)
        batch = api_get(f"/produce/batches/{batch_id}", user_id=user_id)
    status_placeholder.empty()

    if not batch:
        st.error(UI["error"])
        st.session_state[PRODUCE_BATCH_ID] = None
        return

    if batch.get("status") == "error":
        st.error(f"{UI['error']}: {batch.get('error', '')}")
        if st.button(UI["start_new_ingestion"]):
            st.session_state[PRODUCE_BATCH_ID] = None
            st.rerun()
        return

    _render_review(user_id, batch_id, batch)
