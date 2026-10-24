from ash_agent.agents.nodes.diagnose import build_user_prompt


class TestDiagnosePrompt:
    def test_includes_verified_lessons(self) -> None:
        prompt = build_user_prompt(
            {
                "source": "issue",
                "repository": "owner/repo",
                "ref": "main",
                "issue_number": 1,
                "issue_title": "tax bug",
                "issue_body": "100 vs 99",
                "prior_lessons": [
                    {
                        "root_cause": "tax before discount",
                        "patch_summary": "discount first",
                    }
                ],
            }
        )

        assert "Verified lessons for this repository" in prompt
        assert "tax before discount" in prompt
        assert "discount first" in prompt

    def test_without_lessons_has_no_lesson_section(self) -> None:
        prompt = build_user_prompt(
            {
                "source": "workflow_run",
                "repository": "owner/repo",
                "commit_sha": "a" * 40,
            }
        )

        assert "Verified lessons" not in prompt
