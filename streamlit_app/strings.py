"""Hebrew UI strings for the Streamlit app."""

UI = {
    "app_title": "פנקס כיס",
    "app_subtitle": "ויקי ידע ארגוני",
    "login": "כניסה",
    "login_prompt": "הזן מזהה משתמש",
    "login_button": "התחבר",
    "logout": "התנתק",
    "permission_level": "רמת הרשאה",
    "user_name": "שם משתמש",

    # Navigation
    "nav_browse": "עיון",
    "nav_search": "חיפוש",
    "nav_create": "יצירת דף",
    "nav_edit": "עריכת דף",
    "nav_ask": "שאל שאלה",
    "nav_produce": "העלאת מסמך",
    "nav_my_requests": "הבקשות שלי",
    "nav_my_approvals": "אישורים",
    "nav_admin": "ניהול",

    # Browse
    "page_tree": "עץ דפים",
    "tag_folders": "תיקיות תגיות",
    "untagged_folder": "ללא תגית",
    "back_to_folders": "חזרה לתיקיות",
    "page_title": "כותרת",
    "page_content": "תוכן",
    "page_status": "סטטוס",
    "page_history": "היסטוריה",
    "page_references": "הפניות",
    "no_pages": "אין דפים להצגה",
    "parent_page": "דף אב",
    "root_page": "דף שורש (ללא אב)",

    # Search
    "search_placeholder": "הקלד מונח לחיפוש...",
    "search_button": "חפש",
    "search_results": "תוצאות חיפוש",
    "no_results": "לא נמצאו תוצאות",

    # Create/Edit
    "create_page": "יצירת דף חדש",
    "edit_page": "עריכת דף",
    "edit_page_existing": "עריכת דף קיים",
    "delete_page": "מחיקת דף",
    "title_field": "כותרת",
    "description_field": "תיאור קצר",
    "content_field": "תוכן (Markdown)",
    "aliases_field": "כינויים (מופרדים בפסיק)",
    "tags_field": "תגיות",
    "approval_date": "תאריך אישור הבא",
    "save_button": "שמור",
    "delete_button": "מחק",
    "confirm_delete": "האם למחוק את הדף?",

    # Parent picker
    "parent_search_label": "חיפוש דף אב",
    "no_parent": "ללא דף אב",
    "clear_parent": "נקה",

    # Ask
    "ask_title": "שאל שאלה",
    "ask_placeholder": "הקלד שאלה...",
    "ask_button": "שלח",
    "answer": "תשובה",
    "cited_pages": "דפים שצוטטו",

    # Produce
    "produce_title": "העלאת מסמכים",
    "upload_files": "בחר קבצים להעלאה",
    "upload_button": "העלה ועבד",
    "generated_pages": "דפים שנוצרו",
    "supported_formats": "פורמטים נתמכים: PDF, DOCX, HTML, TXT, MD",

    # Requests
    "my_requests_title": "הבקשות שלי",
    "request_type": "סוג בקשה",
    "request_status": "סטטוס",
    "request_date": "תאריך",
    "no_requests": "אין בקשות",

    # Approvals
    "approvals_title": "אישורים ממתינים",
    "approve_button": "אשר",
    "reject_button": "דחה",
    "comment_field": "הערה",
    "no_approvals": "אין אישורים ממתינים",
    "decision_history": "היסטוריית החלטות",

    # Admin
    "admin_title": "ניהול מערכת",
    "workflows_tab": "תהליכי עבודה",
    "users_tab": "משתמשים",
    "create_workflow": "יצירת תהליך עבודה",
    "workflow_name": "שם תהליך",
    "workflow_desc": "תיאור",
    "workflow_steps": "שלבי אישור (מזהי משתמשים, מופרדים בפסיקים)",
    "create_user": "יצירת משתמש",
    "assign_workflow": "שיוך תהליך עבודה",

    # Bundles
    "nav_bundle_create": "יצירת חבילה",
    "nav_bundle_browse": "עיון בחבילות",
    "bundle_name": "שם חבילה",
    "bundle_name_placeholder": "שם חבילה קיים או חדש",
    "bundle_load": "טען",
    "bundle_entries": "דפים בחבילה",
    "bundle_no_entries": "לא נבחרו דפים",
    "bundle_add_description": "הוסף (תיאור)",
    "bundle_add_full_info": "הוסף (תוכן מלא)",
    "bundle_remove": "הסר",
    "bundle_save": "שמור חבילה",
    "bundle_existing": "חבילות קיימות",
    "bundle_no_bundles": "אין חבילות",
    "bundle_preview": "תצוגה מקדימה",
    "bundle_load_to_editor": "טען לעריכה",
    "bundle_rendered_text": "טקסט מעובד",
    "bundle_form_description": "תיאור",
    "bundle_form_full_info": "תוכן מלא",
    "bundle_search_label": "חיפוש דף להוספה",

    # Status values
    "status_draft": "טיוטה",
    "status_pending_approval": "ממתין לאישור",
    "status_published": "מפורסם",
    "status_rejected": "נדחה",
    "status_deleted": "נמחק",
    "status_pending": "ממתין",
    "status_approved": "אושר",

    # Request types
    "type_create": "יצירה",
    "type_edit": "עריכה",
    "type_delete": "מחיקה",
    "type_review": "סקירה",

    # Actions
    "action_create": "נוצר",
    "action_edit": "נערך",
    "action_delete": "נמחק",
    "action_approve": "אושר",
    "action_reject": "נדחה",

    # Misc
    "loading": "טוען...",
    "error": "שגיאה",
    "success": "בוצע בהצלחה",
    "download_file": "הורד קובץ מקור",
    "linked_page": "דף מקושר",
    "file_reference": "קובץ מקור",
    "timestamp": "חותמת זמן",
    "user": "משתמש",
    "action": "פעולה",
    "comment": "הערה",
}
