"""Session state key constants and navigation helper."""

import streamlit as st

# Auth
USER_ID = "user_id"
USER_DATA = "user_data"

# Navigation
CURRENT_PAGE = "current_page"

# Browse
VIEWING_PAGE = "viewing_page"
BROWSE_SELECTED_TAG = "browse_selected_tag"
BROWSE_EXPANDED_IDS = "browse_expanded_ids"

# Edit
EDITING_PAGE = "editing_page"

# Chat
CHAT_HISTORY = "chat_history"

# Parent picker
PP_CTX = "_pp_ctx"
PP_SEL_ID = "_pp_sel_id"
PP_SEL_TITLE = "_pp_sel_title"
PP_RESULTS = "_pp_results"

# Bundle editor (admin)
BUNDLE_EDITOR_NAME = "_bundle_editor_name"
BUNDLE_EDITOR_ENTRIES = "_bundle_editor_entries"
BUNDLE_SEARCH_RESULTS = "_bundle_search_results"

# Page route names (match UI dict keys)
NAV_BROWSE = "nav_browse"
NAV_SEARCH = "nav_search"
NAV_CREATE = "nav_create"
NAV_ASK = "nav_ask"
NAV_PRODUCE = "nav_produce"
NAV_MY_REQUESTS = "nav_my_requests"
NAV_MY_APPROVALS = "nav_my_approvals"
NAV_ADMIN = "nav_admin"
NAV_BUNDLE_CREATE = "nav_bundle_create"
NAV_BUNDLE_BROWSE = "nav_bundle_browse"


def navigate_to(page: str, **overrides) -> None:
    """Set current_page, apply any extra session state overrides, and rerun."""
    st.session_state[CURRENT_PAGE] = page
    for key, val in overrides.items():
        st.session_state[key] = val
    st.rerun()
