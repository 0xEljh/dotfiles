import copy
from pathlib import Path

import pytest

from pr_maintenance.common import Blocked
from pr_maintenance.prompts import coding_prompt
from pr_maintenance.worker import Worker
from test_worker import specification


def test_coding_contract_authorizes_locked_setup_without_changing_developer_checkout(tmp_path):
    spec = specification()
    spec["dependency_source"] = "/home/developer/prepared-ui"
    prompt = coding_prompt(spec, "diff", "skill", tmp_path, ["/trusted/pr-worker"])

    assert "Dependency preparation is authorized" in prompt
    assert "bun install --frozen-lockfile" in prompt
    assert "Registry downloads through that package manager are authorized" in prompt
    assert "git check-ignore" in prompt
    assert "compare package.json and the selected lockfile byte-for-byte" in prompt
    assert "Never install through a dependency symlink" in prompt
    assert "Do not modify the source checkout" in prompt
    assert "If the linked dependencies are incomplete" in prompt
    assert "Record the setup method" in prompt
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


def test_missing_dependency_hint_still_authorizes_frozen_install(tmp_path):
    prompt = coding_prompt(specification(), "diff", "skill", Path(tmp_path), ["/trusted/pr-worker"])
    assert "No approved dependency source is configured" in prompt
    assert "otherwise install into this isolated checkout" in prompt
