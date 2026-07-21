"""Admin page — workflow and user management."""

import streamlit as st

from streamlit_app.strings import UI
from streamlit_app.helpers import (
    api_get, api_post, api_put, api_delete, format_date
)


def render(user_id: str):
    st.header(UI["admin_title"])

    tab1, tab2 = st.tabs([UI["workflows_tab"], UI["users_tab"]])

    with tab1:
        _render_workflows(user_id)

    with tab2:
        _render_users(user_id)


def _render_workflows(user_id: str):
    """Workflow management."""
    st.subheader(UI["create_workflow"])

    with st.form("create_workflow_form"):
        name = st.text_input(UI["workflow_name"])
        description = st.text_input(UI["workflow_desc"])
        steps_str = st.text_input(UI["workflow_steps"])
        submitted = st.form_submit_button(UI["save_button"])

        if submitted and name:
            steps = [s.strip() for s in steps_str.split(",") if s.strip()]
            result = api_post("/workflows", user_id=user_id, json_data={
                "name": name,
                "description": description,
                "steps": steps,
            })
            if result and "error" not in result:
                st.success(UI["success"])
                st.rerun()
            else:
                st.error(f"{UI['error']}: {result.get('error', '') if result else ''}")

    st.divider()
    st.subheader("תהליכי עבודה קיימים")

    workflows = api_get("/workflows", user_id=user_id)
    if workflows:
        for wf in workflows:
            with st.expander(f"📋 {wf.get('name', '')} — {wf.get('description', '')}"):
                st.markdown(f"**מזהה:** `{wf['workflow_id']}`")
                st.markdown(f"**שלבים:** {', '.join(wf.get('steps', []))}")

                # History
                history = wf.get("history", [])
                if history:
                    st.markdown("**היסטוריה:**")
                    for entry in history:
                        st.markdown(
                            f"- {format_date(entry.get('timestamp'))} | "
                            f"{entry.get('action', '')} | {entry.get('user_id', '')}"
                        )

                col1, col2 = st.columns(2)
                with col1:
                    new_name = st.text_input("שם חדש", value=wf.get("name", ""), key=f"wfname_{wf['workflow_id']}")
                    new_steps = st.text_input(
                        "שלבים חדשים",
                        value=", ".join(wf.get("steps", [])),
                        key=f"wfsteps_{wf['workflow_id']}"
                    )
                    if st.button("עדכן", key=f"wfupd_{wf['workflow_id']}"):
                        update_data = {}
                        if new_name != wf.get("name"):
                            update_data["name"] = new_name
                        steps = [s.strip() for s in new_steps.split(",") if s.strip()]
                        if steps != wf.get("steps"):
                            update_data["steps"] = steps
                        if update_data:
                            api_put(f"/workflows/{wf['workflow_id']}", user_id=user_id, json_data=update_data)
                            st.rerun()
                with col2:
                    if st.button("מחק", key=f"wfdel_{wf['workflow_id']}"):
                        api_delete(f"/workflows/{wf['workflow_id']}", user_id=user_id)
                        st.rerun()
    else:
        st.info("אין תהליכי עבודה")


def _render_users(user_id: str):
    """User management."""
    st.subheader(UI["create_user"])

    with st.form("create_user_form"):
        name = st.text_input(UI["user_name"])
        level = st.selectbox(UI["permission_level"], ["read_only", "editor", "admin"])
        workflow_id = st.text_input(UI["assign_workflow"], placeholder="השאר ריק אם אין")
        submitted = st.form_submit_button(UI["save_button"])

        if submitted and name:
            result = api_post("/users", user_id=user_id, json_data={
                "name": name,
                "permission_level": level,
                "workflow_id": workflow_id if workflow_id else None,
            })
            if result and "error" not in result:
                st.success(UI["success"])
                st.rerun()
            else:
                st.error(f"{UI['error']}: {result.get('error', '') if result else ''}")

    st.divider()
    st.subheader("משתמשים קיימים")

    users = api_get("/users", user_id=user_id)
    if users:
        for u in users:
            with st.expander(f"👤 {u.get('name', '')} ({u.get('permission_level', '')})"):
                st.markdown(f"**מזהה:** `{u['user_id']}`")
                st.markdown(f"**תהליך עבודה:** `{u.get('workflow_id', 'ללא')}`")

                # Update form
                new_level = st.selectbox(
                    "רמת הרשאה",
                    ["read_only", "editor", "admin"],
                    index=["read_only", "editor", "admin"].index(u.get("permission_level", "read_only")),
                    key=f"ulevel_{u['user_id']}"
                )
                new_wf = st.text_input(
                    "תהליך עבודה",
                    value=u.get("workflow_id", "") or "",
                    key=f"uwf_{u['user_id']}"
                )

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("עדכן", key=f"uupd_{u['user_id']}"):
                        update_data = {}
                        if new_level != u.get("permission_level"):
                            update_data["permission_level"] = new_level
                        if new_wf != (u.get("workflow_id") or ""):
                            update_data["workflow_id"] = new_wf if new_wf else None
                        if update_data:
                            api_put(f"/users/{u['user_id']}", user_id=user_id, json_data=update_data)
                            st.rerun()
                with col2:
                    if st.button("מחק", key=f"udel_{u['user_id']}"):
                        api_delete(f"/users/{u['user_id']}", user_id=user_id)
                        st.rerun()
    else:
        st.info("אין משתמשים")
