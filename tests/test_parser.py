from mini_codex.parser import parse_plan


def test_parse_plan_from_json_fence():
    plan = parse_plan(
        """```json
        {
          "summary": "ok",
          "steps": [{"title": "inspect", "detail": "read files"}],
          "edits": [{"path": "app.py", "action": "create", "content": "print('hi')\\n"}],
          "commands": [{"cmd": "python app.py", "why": "verify", "timeout": 10}],
          "done": true,
          "notes": []
        }
        ```"""
    )

    assert plan.summary == "ok"
    assert plan.steps[0].title == "inspect"
    assert plan.steps[0].detail == "read files"
    assert plan.edits[0].path == "app.py"
    assert plan.commands[0].timeout == 10
    assert plan.done is True


def test_parse_plan_allows_missing_steps_for_older_responses():
    plan = parse_plan(
        """
        {
          "summary": "ok",
          "edits": [],
          "commands": [],
          "done": true,
          "notes": []
        }
        """
    )

    assert plan.steps == []
