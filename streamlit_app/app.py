"""פנקס כיס — Streamlit main app."""
from dotenv import load_dotenv

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import apply_rtl, api_get

# Get path to logo files relative to project root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, '.env'))
LOGO_IMG = os.path.join(_PROJECT_ROOT, "logo_img.png")
LOGO_TEXT = os.path.join(_PROJECT_ROOT, "logo_text.png")

st.set_page_config(
    page_title=UI["app_title"],
    page_icon="📖",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_rtl()

# Session state init
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "user_data" not in st.session_state:
    st.session_state.user_data = None


def show_login():
    """Display login screen."""
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if os.path.exists(LOGO_IMG):
            st.image(LOGO_IMG, width=500)
        # st.title(UI["app_title"])
        st.subheader(UI["app_subtitle"])
        user_id = st.text_input(UI["login_prompt"], key="login_input")
        if st.button(UI["login_button"]):
            if user_id:
                # Verify user exists
                user_data = api_get("/users/me", user_id=user_id)
                if user_data and "error" not in user_data:
                    st.session_state.user_id = user_id
                    st.session_state.user_data = user_data
                    st.rerun()
                else:
                    st.error(f"{UI['error']}: משתמש לא נמצא")


def show_sidebar():
    """Display sidebar navigation."""
    if "current_page" not in st.session_state:
        st.session_state.current_page = "nav_browse"

    with st.sidebar:
        if os.path.exists(LOGO_TEXT):
            st.image(LOGO_TEXT, width=250)
        else:
            st.title(UI["app_title"])

        user = st.session_state.user_data
        st.markdown(f"**{UI['user_name']}:** {user.get('name', '')}")
        st.markdown(f"**{UI['permission_level']}:** {user.get('permission_level', '')}")
        st.divider()

        nav_items = [
            ("nav_browse", "📂"),
            ("nav_search", "🔍"),
            ("nav_ask", "💬"),
        ]

        if user.get("permission_level") in ("editor", "admin"):
            nav_items.extend([
                ("nav_create", "✏️"),
                ("nav_produce", "📄"),
            ])

        nav_items.extend([
            ("nav_my_requests", "📋"),
            ("nav_my_approvals", "✅"),
        ])

        if user.get("permission_level") == "admin":
            nav_items.append(("nav_admin", "⚙️"))

        for key, icon in nav_items:
            active = st.session_state.current_page == key
            label = f"{icon} **{UI[key]}**" if active else f"{icon} {UI[key]}"
            if st.button(label, key=f"nav_{key}", use_container_width=True):
                st.session_state.current_page = key
                st.rerun()

        st.divider()
        if st.button(f"🚪 {UI['logout']}", use_container_width=True):
            st.session_state.user_id = None
            st.session_state.user_data = None
            st.rerun()

        return UI[st.session_state.current_page]


def main():
    if not st.session_state.user_id:
        show_login()
        return

    selection = show_sidebar()
    user_id = st.session_state.user_id

    if selection == UI["nav_browse"]:
        from streamlit_app.views.browse import render
        render(user_id)
    elif selection == UI["nav_search"]:
        from streamlit_app.views.search import render
        render(user_id)
    elif selection == UI["nav_create"]:
        from streamlit_app.views.create_edit import render_create
        render_create(user_id)
    elif selection == UI["nav_ask"]:
        from streamlit_app.views.ask_page import render
        render(user_id)
    elif selection == UI["nav_produce"]:
        from streamlit_app.views.produce_page import render
        render(user_id)
    elif selection == UI["nav_my_requests"]:
        from streamlit_app.views.my_requests import render
        render(user_id)
    elif selection == UI["nav_my_approvals"]:
        from streamlit_app.views.my_approvals import render
        render(user_id)
    elif selection == UI["nav_admin"]:
        from streamlit_app.views.admin import render
        render(user_id)


if __name__ == "__main__":
    main()
