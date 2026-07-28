"""Shared helpers for the Streamlit app."""

import os

import streamlit as st
import requests

API_URL = os.environ.get("PINKAS_API_URL", "http://localhost:8080")


def apply_rtl():
    """Apply RTL layout globally."""
    st.markdown("""
    <style>
        /* Global RTL */
        .main .block-container, .stSidebar, .stApp {
            direction: rtl;
            text-align: right;
        }
        /* Inputs and selects */
        .stTextInput input, .stTextArea textarea, .stSelectbox select,
        .stMultiSelect, .stDateInput input {
            direction: rtl;
            text-align: right;
        }
        /* Tables */
        .stDataFrame, table {
            direction: rtl;
        }
        /* Expanders */
        .streamlit-expanderHeader {
            direction: rtl;
            text-align: right;
        }
        /* Keep code blocks LTR */
        code, pre, .stCode {
            direction: ltr !important;
            text-align: left !important;
            unicode-bidi: embed;
        }
        /* Markdown content default RTL, code stays LTR */
        .stMarkdown {
            direction: rtl;
            text-align: right;
        }
        .stMarkdown code, .stMarkdown pre {
            direction: ltr !important;
            unicode-bidi: embed;
        }
        /* Tabs */
        .stTabs [data-baseweb="tab-list"] {
            direction: rtl;
        }
        /* Chat messages */
        .stChatMessage {
            direction: rtl;
            text-align: right;
        }
        /* Hide Streamlit's default hamburger menu items and footer */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        /* Radio buttons (sidebar nav) RTL */
        .stRadio > div {
            direction: rtl;
            text-align: right;
        }
        /* Sidebar header */
        [data-testid="stSidebarHeader"] {
            direction: rtl;
        }
    </style>
    """, unsafe_allow_html=True)


def api_get(path: str, user_id: str = None, params: dict = None) -> dict | list | None:
    """Make a GET request to the API."""
    headers = {}
    if user_id:
        headers["X-User-Id"] = user_id
    try:
        resp = requests.get(f"{API_URL}{path}", headers=headers, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def api_post(path: str, user_id: str = None, json_data: dict = None, files=None) -> dict | None:
    """Make a POST request to the API."""
    headers = {}
    if user_id:
        headers["X-User-Id"] = user_id
    try:
        if files:
            resp = requests.post(f"{API_URL}{path}", headers=headers, files=files, timeout=300)
        else:
            resp = requests.post(f"{API_URL}{path}", headers=headers, json=json_data, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        return {"error": resp.text}
    except Exception as e:
        return {"error": str(e)}


def api_put(path: str, user_id: str = None, json_data: dict = None) -> dict | None:
    """Make a PUT request to the API."""
    headers = {}
    if user_id:
        headers["X-User-Id"] = user_id
    try:
        resp = requests.put(f"{API_URL}{path}", headers=headers, json=json_data, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        return {"error": resp.text}
    except Exception as e:
        return {"error": str(e)}


def api_delete(path: str, user_id: str = None) -> dict | None:
    """Make a DELETE request to the API."""
    headers = {}
    if user_id:
        headers["X-User-Id"] = user_id
    try:
        resp = requests.delete(f"{API_URL}{path}", headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        return {"error": resp.text}
    except Exception as e:
        return {"error": str(e)}


def format_date(date_str: str | None) -> str:
    """Format date string to DD/MM/YYYY."""
    if not date_str:
        return ""
    try:
        from datetime import datetime
        if "T" in str(date_str):
            dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
            return dt.strftime("%d/%m/%Y %H:%M")
        parts = str(date_str).split("-")
        if len(parts) == 3:
            return f"{parts[2]}/{parts[1]}/{parts[0]}"
    except Exception:
        pass
    return str(date_str)


def get_status_display(status: str) -> str:
    """Get Hebrew display name for a status."""
    from streamlit_app.strings import UI
    mapping = {
        "draft": UI["status_draft"],
        "pending_approval": UI["status_pending_approval"],
        "published": UI["status_published"],
        "rejected": UI["status_rejected"],
        "deleted": UI["status_deleted"],
        "pending": UI["status_pending"],
        "approved": UI["status_approved"],
    }
    return mapping.get(status, status)


def get_type_display(req_type: str) -> str:
    """Get Hebrew display name for a request type."""
    from streamlit_app.strings import UI
    mapping = {
        "create": UI["type_create"],
        "edit": UI["type_edit"],
        "delete": UI["type_delete"],
        "review": UI["type_review"],
    }
    return mapping.get(req_type, req_type)
