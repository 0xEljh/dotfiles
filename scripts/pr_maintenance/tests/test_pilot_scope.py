import json
from unittest.mock import patch

import pytest

from pr_maintenance.common import Blocked
from pr_maintenance.config import load
from pr_maintenance.github import GitHub


def pilot_config(**changes):
    return {
        "github_auth": "gh", "personal_auth_acknowledged": True,
        "organization_wide": False, "organization_id": 97932676, "author_id": 34589654,
        "allow_repositories": ["EigenformAI/test"], "allow_repository_ids": [77],
        "allow_pr_ids": [100], "execution_enabled": False,
    } | changes


def test_default_app_config_stays_unscoped_without_personal_risk_ack(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}")
    config = load(path)
    assert config["github_auth"] == "app"
    assert config["personal_auth_acknowledged"] is False
    assert config["private_repositories_only"] is False
    assert config["allow_pr_ids"] == config["allow_repository_ids"] == []


def test_bounded_personal_baseline_config_does_not_require_app_credentials(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(pilot_config()))
    config = load(path)
    with patch("pr_maintenance.github.subprocess.run") as run:
        GitHub(config)
    run.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"github_auth": "automatic"}, {"personal_auth_acknowledged": False},
    {"personal_auth_acknowledged": "true"}, {"organization_wide": True},
    {"organization_wide": "false"}, {"allow_repositories": []},
    {"allow_repositories": "EigenformAI/test"}, {"allow_repositories": ["../test"]},
    {"allow_repository_ids": []}, {"allow_pr_ids": []},
    {"author_id": True}, {"organization_id": "97932676"},
] + [{field: value} for field in ("allow_repository_ids", "allow_pr_ids") for value in (
    None, "77", ["77"], [True], [0], [-1], [77.0], [float("inf")], [float("nan")],
)])
def test_personal_validation_applies_even_to_disabled_baseline_and_direct_clients(tmp_path, changes):
    config = pilot_config(**changes)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises((Blocked, ValueError)):
        load(path)
    with patch("pr_maintenance.github.subprocess.run") as run:
        with pytest.raises((Blocked, ValueError)):
            # Valid App IDs ensure rejection is the personal-mode validation,
            # not an unrelated missing-App-credentials error.
            GitHub(config | {"github_app_id": 12, "github_installation_id": 34})
    run.assert_not_called()


@pytest.mark.parametrize("field", ["allow_repository_ids", "allow_pr_ids"])
def test_app_optional_id_scopes_still_require_typed_positive_ids(tmp_path, field):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({field: [True]}))
    with pytest.raises(ValueError):
        load(path)


def test_private_organization_personal_mode_needs_no_exact_pilot_lists(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(pilot_config(
        organization_wide=True, private_repositories_only=True,
        allow_repositories=[], allow_repository_ids=[], allow_pr_ids=[])))
    config = load(path)
    with patch("pr_maintenance.github.subprocess.run") as run:
        GitHub(config)
    run.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"private_repositories_only": False}, {"private_repositories_only": "true"},
    {"personal_auth_acknowledged": False}, {"organization": "../EigenformAI"},
    {"organization": "EigenformAI/other"}, {"organization": ""},
    {"organization_id": True}, {"allow_repositories": ["../test"]},
])
def test_private_organization_mode_rejects_unbounded_or_malformed_scope(tmp_path, changes):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(pilot_config(
        **({"organization_wide": True, "private_repositories_only": True} | changes))))
    with pytest.raises((Blocked, ValueError)):
        load(path)
