"""Create/edit a bundle."""

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import api_get, api_put, load_bundle_into_editor
from streamlit_app.state import BUNDLE_EDITOR_NAME, BUNDLE_EDITOR_ENTRIES, BUNDLE_SEARCH_RESULTS


def render(user_id: str):
    st.header(UI["nav_bundle_create"])

    if BUNDLE_EDITOR_ENTRIES not in st.session_state:
        st.session_state[BUNDLE_EDITOR_ENTRIES] = []
    if BUNDLE_EDITOR_NAME not in st.session_state:
        st.session_state[BUNDLE_EDITOR_NAME] = ""

    col1, col2 = st.columns([4, 1])
    with col1:
        name = st.text_input(
            UI["bundle_name"],
            value=st.session_state[BUNDLE_EDITOR_NAME],
            placeholder=UI["bundle_name_placeholder"],
            key="bundle_name_field",
            label_visibility="collapsed",
        )
    with col2:
        if st.button(UI["bundle_load"], key="bundle_load_btn") and name:
            load_bundle_into_editor(user_id, name)
            st.rerun()

    st.session_state[BUNDLE_EDITOR_NAME] = name

    st.markdown(f"**{UI['bundle_entries']}**")
    entries = st.session_state[BUNDLE_EDITOR_ENTRIES]
    if entries:
        for i, e in enumerate(entries):
            c1, c2 = st.columns([5, 1])
            with c1:
                form_label = UI["bundle_form_description"] if e["content_form"] == "description" else UI["bundle_form_full_info"]
                st.write(f"📄 {e['title']} — {form_label}")
            with c2:
                if st.button(UI["bundle_remove"], key=f"bundle_rm_{i}"):
                    st.session_state[BUNDLE_EDITOR_ENTRIES].pop(i)
                    st.rerun()
    else:
        st.caption(UI["bundle_no_entries"])

    if name and st.button(UI["bundle_save"], key="bundle_save_btn"):
        payload = {"entries": [{"page_id": e["page_id"], "content_form": e["content_form"]} for e in entries]}
        result = api_put(f"/bundles/{name}", user_id=user_id, json_data=payload)
        if result and "error" not in result:
            st.success(UI["success"])
        else:
            st.error(f"{UI['error']}: {result.get('error', '') if result else ''}")

    st.divider()
    st.markdown(f"**{UI['bundle_search_label']}**")

    col1, col2 = st.columns([4, 1])
    with col1:
        query = st.text_input(
            UI["bundle_search_label"],
            key="bundle_search_query",
            placeholder=UI["search_placeholder"],
            label_visibility="collapsed",
        )
    with col2:
        if st.button(UI["search_button"], key="bundle_search_btn"):
            if query:
                results = api_get("/pages/search", user_id=user_id, params={"query": query})
                st.session_state[BUNDLE_SEARCH_RESULTS] = results or []

    results = st.session_state.get(BUNDLE_SEARCH_RESULTS)
    if results is not None:
        if results:
            for page in results[:8]:
                with st.expander(f"📄 {page['title']}"):
                    st.markdown(f"**{UI['bundle_form_description']}:** {page.get('description', '')}")
                    st.divider()
                    st.markdown(f"**{UI['bundle_form_full_info']}:**")
                    st.markdown(page.get("content", ""))

                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(UI["bundle_add_description"], key=f"bundle_add_description_{page['page_id']}"):
                            st.session_state[BUNDLE_EDITOR_ENTRIES].append(
                                {"page_id": page["page_id"], "title": page["title"], "content_form": "description"}
                            )
                            st.rerun()
                    with c2:
                        if st.button(UI["bundle_add_full_info"], key=f"bundle_add_full_info_{page['page_id']}"):
                            st.session_state[BUNDLE_EDITOR_ENTRIES].append(
                                {"page_id": page["page_id"], "title": page["title"], "content_form": "full_info"}
                            )
                            st.rerun()
        else:
            st.caption(UI["no_results"])
