import base64
from datetime import date

from pkm_workflow.module_collection import github_candidates
from pkm_workflow.user_context_v75 import UserContextBundle


def test_github_selection_uses_project_fit_before_date_rotation_or_stars():
    readmes = []
    metadata_calls = []
    def github(endpoint):
        if endpoint.endswith("/readme"):
            readmes.append(endpoint)
            return {"content": base64.b64encode(b"Documented project usage and limitations. " * 30).decode()}
        repo = endpoint.removeprefix("repos/")
        metadata_calls.append(repo)
        description = {
            "stanfordnlp/dspy": "Retrieval pipeline optimization and search evaluation",
            "langchain-ai/langgraph": "Workflow orchestration with persistent state",
        }.get(repo, "Software engineering benchmark")
        return {"html_url": f"https://github.com/{repo}", "full_name": repo,
                "description": description, "topics": [], "private": False, "archived": False,
                "license": {"spdx_id": "MIT"}, "stargazers_count": 100 if "dspy" in repo else 99999,
                "pushed_at": "2026-09-08"}

    context = UserContextBundle("hash", {"projects": ["Retrieval pipeline optimization"]}, (), (), ())
    candidates, _ = github_candidates(date(2026, 9, 9), set(), github, user_context=context)
    assert candidates[0].source == "stanfordnlp/dspy"
    assert len(candidates) == 2
    assert len(readmes) == 2
    assert len(metadata_calls) <= 6
    readmes.clear()
    metadata_calls.clear()
    context = UserContextBundle("hash", {"projects": ["Workflow orchestration persistent state"]}, (), (), ())
    candidates, _ = github_candidates(date(2026, 9, 9), {"https://github.com/stanfordnlp/dspy"}, github,
                                      user_context=context)
    assert candidates[0].source == "langchain-ai/langgraph"
    assert all(candidate.source != "stanfordnlp/dspy" for candidate in candidates)
    assert len(readmes) == 2
    assert len(metadata_calls) <= 6
