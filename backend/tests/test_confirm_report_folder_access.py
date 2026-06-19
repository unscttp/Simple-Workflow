import json
import tempfile
import unittest

from backend.tools import TOOL_REGISTRY, get_active_permission_state, set_active_session


class ConfirmReportFolderAccessTest(unittest.TestCase):
    def test_confirm_updates_current_non_default_session(self) -> None:
        session_id = "unit-test-session"
        set_active_session(
            session_id,
            {"file_access_granted": False, "allowed_report_folder": None},
        )

        with tempfile.TemporaryDirectory() as folder:
            payload = TOOL_REGISTRY["confirm_report_folder_access"](
                granted=True,
                folder=folder,
            )

            response = json.loads(payload)
            state = get_active_permission_state()

        self.assertEqual(response["status"], "granted")
        self.assertTrue(state["file_access_granted"])
        self.assertEqual(state["allowed_report_folder"], folder)


if __name__ == "__main__":
    unittest.main()
