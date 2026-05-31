import unittest
from unittest.mock import patch

from . import project_context


class TestProjectContext(unittest.TestCase):
  def test_standard_tier_without_project_raises_project_required(self):
    with patch.object(project_context, "load_antigravity_code_assist", return_value={
      "allowedTiers": [{"id": "standard-tier"}],
      "ineligibleTiers": [{"id": "free-tier", "reasonCode": "RESTRICTED_AGE"}],
    }):
      with self.assertRaises(project_context.ProjectIdRequiredError) as ctx:
        project_context.resolve_antigravity_project_context("access")

    self.assertIn("standard-tier Antigravity is available", str(ctx.exception))
    self.assertIn("hermes antigravity set-project", str(ctx.exception))

  def test_standard_tier_with_project_onboards_project(self):
    calls = []

    def fake_onboard(access_token, *, tier_id, project_id=""):
      calls.append((access_token, tier_id, project_id))
      return {"done": True}

    with patch.object(project_context, "load_antigravity_code_assist", return_value={
      "allowedTiers": [{"id": "standard-tier"}],
    }), patch.object(project_context, "onboard_antigravity_user", side_effect=fake_onboard):
      ctx = project_context.resolve_antigravity_project_context(
        "access",
        stored_project_id="project-123",
      )

    self.assertEqual(ctx.project_id, "project-123")
    self.assertEqual(ctx.tier_id, "standard-tier")
    self.assertEqual(ctx.source, "antigravity-standard-tier")
    self.assertEqual(calls, [("access", "standard-tier", "project-123")])

  def test_free_tier_allowed_onboards_free_tier(self):
    with patch.object(project_context, "load_antigravity_code_assist", return_value={
      "allowedTiers": [{"id": "free-tier"}],
      "cloudaicompanionProject": "discovered-project",
    }), patch.object(project_context, "onboard_antigravity_user", return_value={
      "done": True,
      "response": {"cloudaicompanionProject": "managed-project"},
    }) as onboard:
      ctx = project_context.resolve_antigravity_project_context("access")

    onboard.assert_called_once_with("access", tier_id="free-tier", project_id="")
    self.assertEqual(ctx.project_id, "managed-project")
    self.assertEqual(ctx.managed_project_id, "managed-project")
    self.assertEqual(ctx.tier_id, "free-tier")

  def test_onboard_polls_long_running_operation(self):
    responses = [
      {"name": "operations/op-1", "done": False},
      {"done": True, "response": {"cloudaicompanionProject": "project-123"}},
    ]

    def fake_post(url, body, access_token, *, timeout=30):
      return responses.pop(0)

    with patch.object(project_context, "_post_url", side_effect=fake_post) as post_url, \
        patch.object(project_context.time, "sleep"):
      response = project_context.onboard_antigravity_user(
        "access",
        tier_id="free-tier",
      )

    self.assertEqual(response["response"]["cloudaicompanionProject"], "project-123")
    self.assertEqual(post_url.call_count, 2)
    self.assertIn("/v1internal/operations/op-1", post_url.call_args_list[1].args[0])


if __name__ == "__main__":
  unittest.main()
