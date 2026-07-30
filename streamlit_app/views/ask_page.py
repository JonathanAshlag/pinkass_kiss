"""Ask question page — chat interface."""

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import api_post
from streamlit_app.state import CHAT_HISTORY


def render(user_id: str):
    st.header(UI["ask_title"])

    if CHAT_HISTORY not in st.session_state:
        st.session_state[CHAT_HISTORY] = []

    # Display chat history
    for msg in st.session_state[CHAT_HISTORY]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("cited_pages"):
                st.caption(f"{UI['cited_pages']}: {', '.join(msg['cited_pages'])}")

    # Chat input
    question = st.chat_input(UI["ask_placeholder"])
    if question:
        st.session_state[CHAT_HISTORY].append({"role": "user", "content": question})

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner(UI["loading"]):
                messages = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state[CHAT_HISTORY]
                ]
                result = api_post("/ask", user_id=user_id, json_data={"messages": messages})

                if result and "error" not in result:
                    answer = result.get("answer", "")
                    cited = result.get("cited_pages", [])
                    st.markdown(answer)
                    if cited:
                        st.caption(f"{UI['cited_pages']}: {', '.join(cited)}")
                    st.session_state[CHAT_HISTORY].append({
                        "role": "assistant",
                        "content": answer,
                        "cited_pages": cited,
                    })
                else:
                    error_msg = result.get("error", "שגיאה בתקשורת עם השרת") if result else "שגיאה בתקשורת"
                    st.error(error_msg)

    if st.button("נקה שיחה"):
        st.session_state[CHAT_HISTORY] = []
        st.rerun()
