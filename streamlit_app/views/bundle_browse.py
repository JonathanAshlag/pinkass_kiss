"""Browse existing bundles."""

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import api_get, format_date, load_bundle_into_editor
from streamlit_app.state import NAV_BUNDLE_CREATE, navigate_to


def render(user_id: str):
    st.header(UI["nav_bundle_browse"])

    bundles = (api_get("/bundles", user_id=user_id) or {}).get("bundles", [])
    if bundles:
        for b in bundles:
            with st.expander(f"📦 {b['name']} — {b['entry_count']} דפים"):
                st.markdown(f"**עודכן:** {format_date(b.get('updated_at'))}")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button(UI["bundle_load_to_editor"], key=f"bundle_load_{b['name']}"):
                        load_bundle_into_editor(user_id, b["name"])
                        navigate_to(NAV_BUNDLE_CREATE)
                with col2:
                    if st.button(UI["bundle_preview"], key=f"bundle_preview_{b['name']}"):
                        preview = api_get(f"/bundles/{b['name']}/preview", user_id=user_id)
                        if preview and "error" not in preview:
                            st.session_state[f"bundle_preview_text_{b['name']}"] = preview.get("rendered_text", "")
                        else:
                            st.error(f"{UI['error']}: {preview.get('error', '') if preview else ''}")

                preview_text = st.session_state.get(f"bundle_preview_text_{b['name']}")
                if preview_text is not None:
                    st.markdown(f"**{UI['bundle_rendered_text']}**")
                    st.text_area(
                        UI["bundle_rendered_text"],
                        value=preview_text,
                        height=200,
                        disabled=True,
                        key=f"bundle_preview_area_{b['name']}",
                        label_visibility="collapsed",
                    )
    else:
        st.info(UI["bundle_no_bundles"])
