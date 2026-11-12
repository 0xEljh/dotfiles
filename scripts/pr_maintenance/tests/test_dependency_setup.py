import copy
import json

import pytest

from pr_maintenance.common import Blocked
from pr_maintenance.prompts import coding_prompt
from pr_maintenance.worker import Worker
from test_worker import specification


def test_coding_prompt_supplies_candidate_handoff_and_optional_dependencies(tmp_path):
    spec = specification()
    spec["dependency_source"] = "/home/developer/prepared-ui"
    prompt = coding_prompt(spec, "diff", "skill", tmp_path, ["/trusted/pr-worker"])

    candidate = next(line.split(": ", 1)[1] for line in prompt.splitlines() if line.startswith("Candidate command ("))
    assert json.loads(candidate) == ["/trusted/pr-worker", "publish", spec["attempt_id"]]
    assert f"Result file: {tmp_path / 'result.json'}" in prompt
    assert prompt.index("/home/developer/prepared-ui") < prompt.index("PR TITLE AND DESCRIPTION:")


def test_dependency_hint_is_selected_by_exact_head_repository_without_mutating_spec(tmp_path):
    spec = specification()
    repository = spec["pr"]["head"]["repo"]["full_name"]
    original = copy.deepcopy(spec)
    worker = Worker({"state_dir": str(tmp_path / "state"), "checkout_root": str(tmp_path / "checkouts"),
                     "dependency_sources": {repository: "/home/developer/prepared-ui"}})
    try:
        assert worker._prompt_spec(spec)["dependency_source"] == "/home/developer/prepared-ui"
        assert spec == original
        other = copy.deepcopy(spec)
        other["pr"]["head"]["repo"]["full_name"] = "Other/repo"
        assert "dependency_source" not in worker._prompt_spec(other)
    finally:
        worker.close()


@pytest.mark.parametrize("source", ["relative/path", "", 42, "/invalid\0path"])
def test_invalid_dependency_source_is_an_explicit_configuration_error(tmp_path, source):
    spec = specification()
    repository = spec["pr"]["head"]["repo"]["full_name"]
    worker = Worker({"state_dir": str(tmp_path / "state"), "checkout_root": str(tmp_path / "checkouts"),
                     "dependency_sources": {repository: source}})
    try:
        with pytest.raises(Blocked, match="dependency source.*absolute"):
            worker._prompt_spec(spec)
    finally:
        worker.close()
