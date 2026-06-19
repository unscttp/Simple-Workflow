import json

from pydantic import BaseModel, Field

from ..shared import audit
from ..shared.audit import (
    ERROR_CATEGORY_PERMISSION_DENIED,
    record_audit_event,
)
from ..shared.pathing import UNAUTHORIZED_FILE_ACCESS_TEXT


class ConfirmReportFolderAccessArgs(BaseModel):
    granted: bool = Field(..., description="用户是否同意授权。")
    folder: str = Field(..., description="用户确认授权的目录绝对路径。")


def confirm_report_folder_access(granted: bool, folder: str) -> str:
    folder_text = folder.strip()
    if not folder_text:
        raise ValueError("folder 不能为空。")
    audit.SESSION_PERMISSION_STATE[audit.ACTIVE_SESSION_ID] = {
        "file_access_granted": bool(granted),
        "allowed_report_folder": folder_text if granted else None,
    }
    record_audit_event("confirm_report_folder_access", folder_text, "authorized" if granted else "unauthorized", "allow" if granted else "deny", error_category=None if granted else ERROR_CATEGORY_PERMISSION_DENIED)
    return json.dumps({"status": "granted" if granted else "denied", "file_access_granted": bool(granted), "allowed_report_folder": folder_text if granted else None, "message": None if granted else UNAUTHORIZED_FILE_ACCESS_TEXT}, ensure_ascii=False, indent=2)
