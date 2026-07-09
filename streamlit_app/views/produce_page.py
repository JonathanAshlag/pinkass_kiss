"""Document upload/produce page."""

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import api_post, get_status_display


def render(user_id: str):
    st.header(UI["produce_title"])
    st.info(UI["supported_formats"])

    uploaded_files = st.file_uploader(
        UI["upload_files"],
        accept_multiple_files=True,
        type=["pdf", "docx", "html", "htm", "txt", "md"],
    )

    if st.button(UI["upload_button"]) and uploaded_files:
        with st.spinner(UI["loading"]):
            files = []
            for f in uploaded_files:
                files.append(("files", (f.name, f.read(), f.type or "application/octet-stream")))

            result = api_post("/produce", user_id=user_id, files=files)

            if result and "error" not in result:
                generated = result.get("generated_pages", [])
                if generated:
                    st.success(f"{UI['success']} — {len(generated)} {UI['generated_pages']}")
                    for page in generated:
                        st.markdown(
                            f"- **{page.get('title', '')}** — "
                            f"{get_status_display(page.get('status', ''))}"
                        )
                else:
                    st.warning("לא נוצרו דפים")
            else:
                st.error(f"{UI['error']}: {result.get('error', '') if result else 'שגיאה'}")
