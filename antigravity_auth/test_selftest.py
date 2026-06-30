import json
import tempfile
import unittest
from unittest.mock import patch


class TestSelftest(unittest.TestCase):
  def test_run_selftest_passes_offline_round_trip(self):
    from antigravity_auth.selftest import run_selftest

    with tempfile.TemporaryDirectory() as tmpdir:
      rows = run_selftest(packaging_root=tmpdir)

    self.assertTrue(rows)
    self.assertTrue(all(row.status == "PASS" for row in rows))
    checks = {row.check for row in rows}
    self.assertIn("message transform", checks)
    self.assertIn("request envelope", checks)
    self.assertIn("response transform", checks)
    self.assertIn("plugin manifests", checks)
    self.assertIn("packaging guard", checks)

  def test_run_selftest_fails_when_packaging_root_has_local_credentials(self):
    from pathlib import Path
    from antigravity_auth.selftest import run_selftest

    with tempfile.TemporaryDirectory() as tmpdir:
      package_dir = Path(tmpdir) / "antigravity_auth"
      package_dir.mkdir()
      (package_dir / "_credentials.py").write_text("DUMMY = 'not-a-secret'\n", encoding="utf-8")
      rows = run_selftest(packaging_root=tmpdir)

    packaging_rows = [row for row in rows if row.check == "packaging guard"]
    self.assertEqual(len(packaging_rows), 1)
    self.assertEqual(packaging_rows[0].status, "FAIL")
    self.assertIn("_credentials.py", packaging_rows[0].detail)

  def test_format_selftest_rows_reports_failure(self):
    from antigravity_auth.selftest import SelftestRow, format_selftest_rows

    output = format_selftest_rows([
      SelftestRow("PASS", "one", "ok"),
      SelftestRow("FAIL", "two", "bad"),
    ])

    self.assertIn("PASS one: ok", output)
    self.assertIn("FAIL two: bad", output)
    self.assertIn("Result: FAIL", output)

  def test_response_round_trip_extracts_usage_headers(self):
    from antigravity_auth.selftest import _check_response_round_trip

    rows = _check_response_round_trip()

    self.assertEqual(len(rows), 1)
    self.assertEqual(rows[0].status, "PASS")
    self.assertEqual(rows[0].check, "response transform")

  def test_response_round_trip_reports_invalid_json_failure(self):
    from antigravity_auth import selftest

    with patch(
      "antigravity_auth.selftest.transform_antigravity_response",
      return_value=(json.dumps({"unexpected": True}), None, None),
    ):
      rows = selftest._check_response_round_trip()

    self.assertEqual(rows[0].status, "FAIL")

  def test_print_selftest_returns_false_on_failed_row(self):
    from antigravity_auth.selftest import SelftestRow, print_selftest

    with patch("antigravity_auth.selftest.run_selftest", return_value=[
      SelftestRow("FAIL", "sample", "nope"),
    ]), patch("builtins.print") as mock_print:
      ok = print_selftest()

    self.assertFalse(ok)
    output = "\n".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
    self.assertIn("Result: FAIL", output)
