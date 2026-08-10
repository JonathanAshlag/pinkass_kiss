"""פנקס כיס — Streamlit main app."""
from dotenv import load_dotenv

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import apply_rtl, api_get
from streamlit_app.state import (
    USER_ID, USER_DATA, CURRENT_PAGE, EDITING_PAGE, PP_CTX,
    NAV_BROWSE, NAV_CREATE, NAV_SEARCH, NAV_ASK, NAV_PRODUCE,
    NAV_MY_REQUESTS, NAV_MY_APPROVALS, NAV_ADMIN,
    NAV_BUNDLE_CREATE, NAV_BUNDLE_BROWSE,
    navigate_to,
)

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
if USER_ID not in st.session_state:
    st.session_state[USER_ID] = None
if USER_DATA not in st.session_state:
    st.session_state[USER_DATA] = None


def show_login():
    """Display login screen."""
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if os.path.exists(LOGO_IMG):
            st.image(LOGO_IMG, width=500)
        st.subheader(UI["app_subtitle"])
        user_id = st.text_input(UI["login_prompt"], key="login_input")
        if st.button(UI["login_button"]):
            if user_id:
                user_data = api_get("/users/me", user_id=user_id)
                if user_data and "error" not in user_data:
                    st.session_state[USER_ID] = user_id
                    st.session_state[USER_DATA] = user_data
                    st.rerun()
                else:
                    st.error(f"{UI['error']}: משתמש לא נמצא")


def show_sidebar():
    """Display sidebar navigation."""
    if CURRENT_PAGE not in st.session_state:
        st.session_state[CURRENT_PAGE] = NAV_BROWSE

    with st.sidebar:
        if os.path.exists(LOGO_TEXT):
            st.image(LOGO_TEXT, width=250)
        else:
            st.title(UI["app_title"])

        user = st.session_state[USER_DATA]
        st.markdown(f"**{UI['user_name']}:** {user.get('name', '')}")
        st.markdown(f"**{UI['permission_level']}:** {user.get('permission_level', '')}")
        st.divider()

        nav_items = [
            (NAV_BROWSE, "📂"),
            (NAV_SEARCH, "🔍"),
            (NAV_ASK, "💬"),
        ]

        if user.get("permission_level") in ("editor", "admin"):
            nav_items.extend([
                (NAV_CREATE, "✏️"),
                (NAV_PRODUCE, "📄"),
            ])

        nav_items.extend([
            (NAV_MY_REQUESTS, "📋"),
            (NAV_MY_APPROVALS, "✅"),
        ])

        if user.get("permission_level") == "admin":
            nav_items.extend([
                (NAV_BUNDLE_CREATE, "🧩"),
                (NAV_BUNDLE_BROWSE, "📦"),
                (NAV_ADMIN, "⚙️"),
            ])

        for key, icon in nav_items:
            active = st.session_state[CURRENT_PAGE] == key
            label = f"{icon} **{UI[key]}**" if active else f"{icon} {UI[key]}"
            if st.button(label, key=f"nav_{key}", use_container_width=True):
                if key == NAV_CREATE:
                    navigate_to(NAV_CREATE, **{EDITING_PAGE: None, PP_CTX: None})
                else:
                    navigate_to(key)

        st.divider()
        if st.button(f"🚪 {UI['logout']}", use_container_width=True):
            st.session_state[USER_ID] = None
            st.session_state[USER_DATA] = None
            st.rerun()

        return UI[st.session_state[CURRENT_PAGE]]


def main():
    if not st.session_state[USER_ID]:
        show_login()
        return

    selection = show_sidebar()
    user_id = st.session_state[USER_ID]

    if selection == UI[NAV_BROWSE]:
        from streamlit_app.views.browse import render
        render(user_id)
    elif selection == UI[NAV_SEARCH]:
        from streamlit_app.views.search import render
        render(user_id)
    elif selection == UI[NAV_CREATE]:
        if st.session_state.get(EDITING_PAGE):
            from streamlit_app.views.create_edit import render_edit
            render_edit(user_id)
        else:
            from streamlit_app.views.create_edit import render_create
            render_create(user_id)
    elif selection == UI[NAV_ASK]:
        from streamlit_app.views.ask_page import render
        render(user_id)
    elif selection == UI[NAV_PRODUCE]:
        from streamlit_app.views.produce_page import render
        render(user_id)
    elif selection == UI[NAV_MY_REQUESTS]:
        from streamlit_app.views.my_requests import render
        render(user_id)
    elif selection == UI[NAV_MY_APPROVALS]:
        from streamlit_app.views.my_approvals import render
        render(user_id)
    elif selection == UI[NAV_BUNDLE_CREATE]:
        from streamlit_app.views.bundle_create import render
        render(user_id)
    elif selection == UI[NAV_BUNDLE_BROWSE]:
        from streamlit_app.views.bundle_browse import render
        render(user_id)
    elif selection == UI[NAV_ADMIN]:
        from streamlit_app.views.admin import render
        render(user_id)


if __name__ == "__main__":
    main()
