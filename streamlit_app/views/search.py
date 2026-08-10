"""Search page."""

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import api_get, get_status_display, get_allowed_tags
from streamlit_app.state import VIEWING_PAGE, NAV_BROWSE, navigate_to


def render(user_id: str):
    st.header(UI["nav_search"])

    query = st.text_input(UI["search_placeholder"])
    tags = st.multiselect(UI["tags_field"], options=get_allowed_tags(user_id), default=[])

    if st.button(UI["search_button"]) and query:
        params = {"query": query}
        if tags:
            params["tags"] = tags
        results = api_get("/pages/search", user_id=user_id, params=params)
        if results:
            st.subheader(f"{UI['search_results']} ({len(results)})")
            for page in results:
                with st.expander(f"📄 {page['title']} — {get_status_display(page.get('status', ''))}"):
                    st.markdown(page.get("content", "")[:500])
                    if st.button("פתח דף", key=f"open_{page['page_id']}"):
                        navigate_to(NAV_BROWSE, **{VIEWING_PAGE: page["page_id"]})
        else:
            st.info(UI["no_results"])
