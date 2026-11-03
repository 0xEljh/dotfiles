import io
import base64
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from pr_maintenance.common import Blocked
from pr_maintenance.github import GitHub, GitHubError, IntegrationRemoved, RequestBudgetExceeded


class Response:
    def __init__(self, data, headers=None, status=200):
        self.data = json.dumps(data).encode()
        self.headers = headers or {}
        self.status = status

    def read(self, size=-1):
        return self.data if size < 0 else self.data[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(*responses, budget=30):
    transport = Transport(*responses)
    github = GitHub(
        {"github_app_id": 12, "github_installation_id": 34, "api_budget": budget},
        opener=transport,
        clock=lambda: 1000.0,
    )
    github._token = "installation-token"
    github._token_expires_at = 5000
    return github, transport


def http_error(status, headers=None):
    return HTTPError("https://api.github.com/test", status, "private failure", headers or {}, io.BytesIO(b"private body"))


def personal_config(**changes):
    return {
        "github_auth": "gh", "personal_auth_acknowledged": True,
        "organization_wide": False, "organization_id": 97932676, "author_id": 34589654,
        "allow_repositories": ["EigenformAI/test"], "allow_repository_ids": [77],
        "allow_pr_ids": [100],
    } | changes


def personal_client(*responses, **changes):
    transport = Transport(*responses)
    return GitHub(personal_config(**changes), opener=transport, clock=lambda: 1000), transport


class PersonalGitHubTests(unittest.TestCase):
    def setUp(self):
        self.secret = "ghp_" + "x" * 30
        self.token_command = patch("pr_maintenance.github.subprocess.run", return_value=SimpleNamespace(
            returncode=0, stdout=(self.secret + "\n").encode(), stderr=b"private stderr"))
        self.run = self.token_command.start()
        self.addCleanup(self.token_command.stop)

    def test_personal_auth_uses_host_local_token_and_verifies_account_once_before_reads(self):
        repository = {"id": 77, "full_name": "EigenformAI/test", "owner": {"id": 97932676}}
        github, transport = personal_client(Response({"id": 34589654}), Response(repository), Response({"id": 100}))
        self.assertEqual(list(github.repositories()), [repository])
        self.assertEqual(github.get_pr("EigenformAI/test", 1), {"id": 100})
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.run.call_args.args[0], ["gh", "auth", "token", "--hostname", "github.com"])
        self.assertTrue(self.run.call_args.kwargs["capture_output"])
        self.assertEqual([request.full_url for request in transport.requests], [
            "https://api.github.com/user", "https://api.github.com/repos/EigenformAI/test",
            "https://api.github.com/repos/EigenformAI/test/pulls/1"])
        self.assertTrue(all(request.get_method() == "GET" and request.data is None for request in transport.requests))
        self.assertTrue(all(request.get_header("Authorization") == "Bearer " + self.secret for request in transport.requests))
        self.assertNotIn(self.secret, json.dumps(github.config))

    def test_personal_account_mismatch_denies_repository_and_direct_content_reads(self):
        for operation in (lambda github: list(github.repositories()), lambda github: github.feedback("EigenformAI/test", 1)):
            with self.subTest(operation=operation):
                github, transport = personal_client(Response({"id": 42, "login": self.secret}))
                with self.assertRaises(GitHubError) as error:
                    operation(github)
                self.assertNotIn(self.secret, str(error.exception))
                self.assertEqual([request.full_url for request in transport.requests], ["https://api.github.com/user"])

    def test_personal_mode_forbids_token_minting_before_any_http_or_credential_read(self):
        github, transport = personal_client()
        for operation in (
            lambda: github._send("https://api.github.com/app/installations/34/access_tokens", token=self.secret, token_request=True),
            github._installation_token,
            github._app_jwt,
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(GitHubError):
                    operation()
        self.assertEqual(transport.requests, [])
        self.run.assert_not_called()

    def test_personal_repository_identity_and_owner_drift_are_errors_not_empty_inventory(self):
        for repository in (
            {"id": 78, "owner": {"id": 97932676}},
            {"id": 77, "owner": {"id": 42}},
            {"id": "77", "owner": {"id": 97932676}},
        ):
            with self.subTest(repository=repository):
                github, transport = personal_client(Response({"id": 34589654}), Response(repository | {"full_name": "EigenformAI/test"}))
                with self.assertRaises(GitHubError):
                    list(github.repositories())
                self.assertEqual(len(transport.requests), 2)

    def test_personal_repository_permission_failure_propagates_without_private_body(self):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                github, transport = personal_client(Response({"id": 34589654}), http_error(status))
                with self.assertRaises(GitHubError) as error:
                    list(github.repositories())
                self.assertNotIn("private", str(error.exception))
                self.assertNotIn(self.secret, str(error.exception))
                self.assertEqual(len(transport.requests), 2)

    def test_personal_token_command_failures_never_echo_output_or_exception(self):
        for failure in (
            SimpleNamespace(returncode=1, stdout=self.secret.encode(), stderr=self.secret.encode()),
            SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
            SimpleNamespace(returncode=0, stdout=(self.secret + "\nInjected: header").encode(), stderr=b""),
            subprocess.TimeoutExpired(["gh", self.secret], 8, output=self.secret),
        ):
            with self.subTest(failure=type(failure)):
                self.run.side_effect = failure if isinstance(failure, Exception) else None
                self.run.return_value = failure
                github, transport = personal_client()
                with self.assertRaises(GitHubError) as error:
                    list(github.repositories())
                self.assertNotIn(self.secret, str(error.exception))
                self.assertEqual(transport.requests, [])

    def test_personal_feedback_preserves_pagination_budget_and_rate_protection(self):
        github, transport = personal_client(
            Response({"id": 34589654}),
            Response([{"id": 1}], {"Link": '</repos/EigenformAI/test/issues/1/comments?page=2>; rel="next"'}),
            api_budget=2,
        )
        with self.assertRaises(RequestBudgetExceeded):
            github.feedback("EigenformAI/test", 1)
        self.assertEqual((len(transport.requests), github.remaining), (2, 0))
        github, transport = personal_client(Response({"id": 34589654}), http_error(429, {"Retry-After": "90"}))
        with self.assertRaises(RequestBudgetExceeded):
            github.get_pr("EigenformAI/test", 1)
        with self.assertRaises(RequestBudgetExceeded):
            github.get_pr("EigenformAI/test", 1)
        self.assertEqual((len(transport.requests), github.retry_at), (2, 1090))

    def test_personal_feedback_rejects_cross_origin_pagination_before_sending_token(self):
        github, transport = personal_client(Response({"id": 34589654}), Response(
            [{"id": 1}], {"Link": '<https://evil.test/private>; rel="next"'}))
        with self.assertRaises(GitHubError):
            github.feedback("EigenformAI/test", 1)
        self.assertEqual(len(transport.requests), 2)

    def test_personal_mode_rejects_unlisted_repository_or_inventory_before_reading_credentials(self):
        for path in ("/repos/Other/private/pulls/1", "/orgs/EigenformAI/repos", "/user/repos"):
            with self.subTest(path=path):
                github, transport = personal_client()
                with self.assertRaises(GitHubError):
                    github.get(path)
                self.assertEqual(transport.requests, [])
        self.run.assert_not_called()

    def test_personal_zero_budget_never_reads_credentials(self):
        github, transport = personal_client(api_budget=0)
        with self.assertRaises(RequestBudgetExceeded):
            list(github.repositories())
        self.run.assert_not_called()
        self.assertEqual(transport.requests, [])

    def private_client(self, *responses, **changes):
        return personal_client(*responses, **({
            "organization_wide": True, "private_repositories_only": True,
            "allow_repositories": [], "allow_repository_ids": [], "allow_pr_ids": [],
        } | changes))

    def test_private_organization_inventory_validates_identity_filters_and_paginates(self):
        private = {"id": 77, "full_name": "EigenformAI/test", "private": True, "owner": {"id": 97932676}}
        other = private | {"id": 78, "full_name": "EigenformAI/next"}
        github, transport = self.private_client(
            Response({"id": 34589654}), Response({"id": 97932676}),
            Response([private, private | {"private": False}, private | {"owner": {"id": 42}},
                      private | {"full_name": "Other/test"}, private | {"private": 1}],
                     {"Link": '</orgs/EigenformAI/repos?type=private&page=2>; rel="next"'}),
            Response([other]), Response({"id": 100}))
        self.assertEqual(list(github.repositories()), [private, other])
        self.assertEqual(github.get_pr("EigenformAI/test", 1), {"id": 100})
        self.assertEqual([request.full_url for request in transport.requests], [
            "https://api.github.com/user", "https://api.github.com/orgs/EigenformAI",
            "https://api.github.com/orgs/EigenformAI/repos?per_page=100&type=private",
            "https://api.github.com/orgs/EigenformAI/repos?type=private&page=2",
            "https://api.github.com/repos/EigenformAI/test/pulls/1"])
        self.assertTrue(all(request.get_method() == "GET" for request in transport.requests))
        self.assertNotIn(self.secret, json.dumps(github.config))

    def test_private_organization_wrong_id_prevents_inventory(self):
        github, transport = self.private_client(Response({"id": 34589654}), Response({"id": 42}))
        with self.assertRaises(GitHubError):
            list(github.repositories())
        self.assertEqual(len(transport.requests), 2)

    def test_private_organization_inventory_accepts_pinned_numeric_pagination_alias(self):
        private = {"id": 77, "full_name": "EigenformAI/test", "private": True, "owner": {"id": 97932676}}
        other = private | {"id": 78, "full_name": "EigenformAI/next"}
        canonical = "https://api.github.com/organizations/97932676/repos?type=private&per_page=100&page=2"
        github, transport = self.private_client(
            Response({"id": 34589654}), Response({"id": 97932676}),
            Response([private], {"Link": f'<{canonical}>; rel=next'}), Response([other]))
        self.assertEqual(list(github.repositories()), [private, other])
        self.assertEqual([request.full_url for request in transport.requests], [
            "https://api.github.com/user", "https://api.github.com/orgs/EigenformAI",
            "https://api.github.com/orgs/EigenformAI/repos?per_page=100&type=private", canonical])
        self.assertTrue(all(request.get_method() == "GET" for request in transport.requests))

    def test_private_organization_numeric_inventory_verifies_account_and_named_org_first(self):
        github, transport = self.private_client(
            Response({"id": 34589654}), Response({"id": 97932676}), Response([]))
        self.assertEqual(github.get("/organizations/97932676/repos", {"type": "private"}), [])
        self.assertEqual([request.full_url for request in transport.requests], [
            "https://api.github.com/user", "https://api.github.com/orgs/EigenformAI",
            "https://api.github.com/organizations/97932676/repos?type=private"])

    def test_private_organization_numeric_pagination_alias_rejects_scope_and_filter_drift(self):
        for target in (
            "/organizations/42/repos?type=private&page=2",
            "/orgs/Other/repos?type=private&page=2",
            "/organizations/97932676/repos?type=public&page=2",
            "/organizations/97932676/repos?page=2",
            "/organizations/97932676/repos?type=private&type=public&page=2",
            "/organizations/97932676/repos?type=private&visibility=all&page=2",
            "/organizations/97932676/repos/other?type=private&page=2",
            "/repos/EigenformAI/test/pulls?page=2",
            "https://evil.test/organizations/97932676/repos?type=private&page=2",
        ):
            with self.subTest(target=target):
                github, transport = self.private_client(
                    Response({"id": 34589654}), Response({"id": 97932676}),
                    Response([], {"Link": f'<{target}>; rel=next'}))
                with self.assertRaises(GitHubError):
                    list(github.repositories())
                self.assertEqual(len(transport.requests), 3)

    def test_private_organization_fresh_pr_and_feedback_clients_validate_repository_first(self):
        repository = {"id": 77, "full_name": "EigenformAI/test", "private": True, "owner": {"id": 97932676}}
        for operation, responses in (
            (lambda github: github.get_pr("EigenformAI/test", 1), [Response({"id": 100})]),
            (lambda github: github.feedback("EigenformAI/test", 1), [Response([]), Response([]), Response([])]),
        ):
            github, transport = self.private_client(
                Response({"id": 34589654}), Response({"id": 97932676}), Response(repository), *responses)
            operation(github)
            self.assertEqual([request.full_url for request in transport.requests[:3]], [
                "https://api.github.com/user", "https://api.github.com/orgs/EigenformAI",
                "https://api.github.com/repos/EigenformAI/test"])

    def test_private_organization_fresh_client_denies_public_or_wrong_owner_repository(self):
        private = {"id": 77, "full_name": "EigenformAI/test", "private": True, "owner": {"id": 97932676}}
        for changed in ({"private": False}, {"private": None}, {"owner": {"id": 42}}, {"full_name": "Other/test"}):
            github, transport = self.private_client(
                Response({"id": 34589654}), Response({"id": 97932676}), Response(private | changed))
            with self.assertRaises(GitHubError):
                github.get_pr("EigenformAI/test", 1)
            self.assertEqual(len(transport.requests), 3)

    def test_private_organization_pagination_cannot_change_scope_or_inventory_type(self):
        for target in ("/orgs/Other/repos?type=private", "/orgs/EigenformAI/repos?type=public",
                       "/repos/EigenformAI/public/pulls", "https://evil.test/repos?type=private"):
            github, transport = self.private_client(
                Response({"id": 34589654}), Response({"id": 97932676}),
                Response([], {"Link": f'<{target}>; rel="next"'}))
            with self.assertRaises(GitHubError):
                list(github.repositories())
            self.assertEqual(len(transport.requests), 3)

    def test_private_organization_optional_repository_narrowing_is_honored(self):
        private = {"id": 77, "full_name": "EigenformAI/test", "private": True, "owner": {"id": 97932676}}
        github, transport = self.private_client(
            Response({"id": 34589654}), Response({"id": 97932676}),
            Response([private, private | {"id": 78, "full_name": "EigenformAI/other"}]),
            allow_repository_ids=[77], allow_repositories=["EigenformAI/test"])
        self.assertEqual(list(github.repositories()), [private])
        with self.assertRaises(GitHubError):
            github.get_pr("EigenformAI/other", 1)
        self.assertEqual(len(transport.requests), 3)

    def test_private_organization_budget_and_rate_limits_never_fall_back(self):
        github, transport = self.private_client(
            Response({"id": 34589654}), Response({"id": 97932676}), api_budget=2)
        with self.assertRaises(RequestBudgetExceeded):
            list(github.repositories())
        self.assertEqual((len(transport.requests), github.remaining), (2, 0))
        github, transport = self.private_client(
            Response({"id": 34589654}), http_error(429, {"Retry-After": "90"}))
        with self.assertRaises(RequestBudgetExceeded):
            list(github.repositories())
        with self.assertRaises(RequestBudgetExceeded):
            github.get_pr("EigenformAI/test", 1)
        self.assertEqual((len(transport.requests), github.retry_at), (2, 1090))

    def test_private_organization_pr_pagination_cannot_switch_to_another_validated_repository(self):
        private = {"id": 77, "full_name": "EigenformAI/test", "private": True, "owner": {"id": 97932676}}
        github, transport = self.private_client(
            Response({"id": 34589654}), Response({"id": 97932676}),
            Response([private, private | {"id": 78, "full_name": "EigenformAI/other"}]),
            Response([], {"Link": '</repos/EigenformAI/other/pulls?page=2>; rel="next"'}))
        list(github.repositories())
        with self.assertRaises(GitHubError):
            list(github.open_prs("EigenformAI/test"))
        self.assertEqual(len(transport.requests), 4)


class GitHubTests(unittest.TestCase):
    def test_app_private_inventory_filter_is_opt_in(self):
        private = {"id": 77, "full_name": "EigenformAI/test", "private": True, "owner": {"id": 97932676}}
        github, _ = client(Response({"repositories": [private, private | {"private": False},
                                                    private | {"owner": {"id": 42}}]}))
        github.config["private_repositories_only"] = True
        self.assertEqual(list(github.repositories()), [private])

    def test_default_app_missing_credentials_never_falls_back_to_gh(self):
        with patch.dict("pr_maintenance.github.os.environ", {}, clear=True), patch("pr_maintenance.github.subprocess.run") as run:
            with self.assertRaises(ValueError):
                GitHub({})
            github = GitHub({"github_app_id": 12, "github_installation_id": 34}, opener=Transport())
            with self.assertRaises(GitHubError):
                list(github.repositories())
        run.assert_not_called()

    def test_feedback_reads_every_page_and_preserves_raw_metadata(self):
        comment = {"id": 1, "body": "fix this", "user": {"id": 9}, "html_url": "https://github.com/x/y/1"}
        reply = {"id": 2, "body": "context", "in_reply_to_id": 1, "original_commit_id": "abc", "path": "x.py"}
        github, transport = client(
            Response([comment], {"Link": '<https://api.github.com/repos/x/y/issues/1/comments?page=2>; rel="next"'}),
            Response([{"id": 3, "body": "another page"}]),
            Response([{"id": 4, "body": "review"}], {"Link": '</repos/x/y/pulls/1/reviews?page=2>; rel="next"'}),
            Response([{"id": 5, "body": "second review"}]),
            Response([reply], {"Link": '</repos/x/y/pulls/1/comments?page=2>; rel="next"'}),
            Response([{"id": 6, "body": "second inline"}]),
        )
        snapshot = github.feedback("x/y", 1)
        self.assertEqual(snapshot["conversation"], [comment, {"id": 3, "body": "another page"}])
        self.assertEqual(snapshot["reviews"], [{"id": 4, "body": "review"}, {"id": 5, "body": "second review"}])
        self.assertEqual(snapshot["inline"], [reply, {"id": 6, "body": "second inline"}])
        self.assertEqual((snapshot["started_at"], snapshot["finished_at"]), (1000, 1000))
        self.assertTrue(all(request.get_method() == "GET" for request in transport.requests))
        self.assertEqual(len(transport.requests), 6)

    def test_feedback_withholds_known_secret_without_echoing_it(self):
        secret = "ghp_" + "x" * 30
        github, _ = client(Response([{"id": 1, "body": secret}]), Response([]), Response([]))
        with self.assertRaises(Blocked) as error:
            github.feedback("x/y", 1)
        self.assertNotIn(secret, str(error.exception))

    def test_startup_open_inventory_paginates_without_recency_cutoff(self):
        github, transport = client(
            Response({"repositories": [{"id": 100, "full_name": "x/y"}]}, {"Link": '</installation/repositories?page=2>; rel="next"'}),
            Response({"repositories": [{"id": 101, "full_name": "x/z"}]}),
            Response([{"id": 1, "updated_at": "2001-01-01T00:00:00Z"}], {"Link": '</repos/x/y/pulls?page=2>; rel="next"'}),
            Response([{"id": 2}]),
        )
        self.assertEqual([item["id"] for item in github.repositories()], [100, 101])
        self.assertEqual([item["id"] for item in github.open_prs("x/y")], [1, 2])
        self.assertIn("state=open", transport.requests[2].full_url)

    def test_recovery_scans_all_states_in_updated_order_and_stops_at_cutoff(self):
        github, transport = client(
            Response([{"id": 1, "updated_at": "1970-01-01T00:20:00Z", "state": "closed"}], {"Link": '</repos/x/y/pulls?page=2>; rel="next"'}),
            Response([{"id": 2, "updated_at": "1970-01-01T00:16:40Z"}, {"id": 3, "updated_at": "1970-01-01T00:10:00Z"}], {"Link": '</repos/x/y/pulls?page=3>; rel="next"'}),
        )
        self.assertEqual([item["id"] for item in github.updated_prs("x/y", 1000)], [1, 2])
        self.assertIn("state=all", transport.requests[0].full_url)
        self.assertIn("sort=updated", transport.requests[0].full_url)
        self.assertIn("direction=desc", transport.requests[0].full_url)
        self.assertEqual(len(transport.requests), 2)

    def test_mid_inventory_budget_failure_is_not_a_successful_end(self):
        github, transport = client(Response([{"id": 1}], {"Link": '</repos/x/y/pulls?page=2>; rel="next"'}), budget=1)
        inventory = github.open_prs("x/y")
        self.assertEqual(next(inventory)["id"], 1)
        with self.assertRaises(RequestBudgetExceeded):
            next(inventory)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(github.remaining, 0)

    def test_pagination_cannot_send_app_credential_to_another_origin(self):
        for url in ("https://evil.test/page", "http://api.github.com/page", "https://api.github.com:444/page", "https://user@api.github.com/page"):
            with self.subTest(url=url):
                github, transport = client(Response([{"id": 1}], {"Link": f'<{url}>; rel="next"'}))
                with self.assertRaises(GitHubError):
                    list(github.open_prs("x/y"))
                self.assertEqual(len(transport.requests), 1)

    def test_repeated_pagination_cursor_fails_instead_of_marking_complete(self):
        url = "https://api.github.com/installation/repositories?per_page=100"
        github, _ = client(Response({"repositories": []}, {"Link": f'<{url}>; rel="next"'}))
        with self.assertRaises(GitHubError):
            list(github.repositories())

    def test_rate_limit_defers_later_requests_without_spending_more_budget(self):
        github, transport = client(http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1500"}))
        with self.assertRaises(RequestBudgetExceeded):
            github.get_pr("x/y", 1)
        remaining = github.remaining
        with self.assertRaises(RequestBudgetExceeded):
            github.get_pr("x/y", 1)
        self.assertEqual((len(transport.requests), github.remaining, github.retry_at), (1, remaining, 1500))

    def test_secondary_limit_retry_after_and_transient_backoff_are_exposed(self):
        for status, headers in ((429, {"Retry-After": "90"}), (503, {})):
            with self.subTest(status=status):
                github, transport = client(http_error(status, headers))
                with self.assertRaises((RequestBudgetExceeded, GitHubError)) as error:
                    github.get_pr("x/y", 1)
                self.assertGreater(github.retry_at, 1000)
                self.assertNotIn("private", str(error.exception))
                with self.assertRaises(RequestBudgetExceeded):
                    github.get_pr("x/y", 1)
                self.assertEqual(len(transport.requests), 1)

    def test_secondary_rate_limit_without_retry_after_also_defers(self):
        failure = HTTPError("https://api.github.com/test", 403, "Forbidden", {}, io.BytesIO(b'{"message":"You have exceeded a secondary rate limit."}'))
        github, _ = client(failure)
        with self.assertRaises(RequestBudgetExceeded):
            github.get_pr("x/y", 1)
        self.assertGreaterEqual(github.retry_at, 1060)

    def test_jwt_signing_uses_short_lived_rs256_claims_without_reading_key_in_python(self):
        github, _ = client()
        github.config["github_private_key_file"] = "/configured/private-key.pem"
        result = SimpleNamespace(returncode=0, stdout=b"signature", stderr=b"")
        with patch("pr_maintenance.github.subprocess.run", return_value=result) as run:
            jwt = github._app_jwt()
        head, claims, signature = jwt.split(".")
        def decode(value):
            return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        self.assertEqual(decode(head), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(decode(claims), {"iat": 940, "exp": 1540, "iss": "12"})
        self.assertEqual(run.call_args.args[0], ["openssl", "dgst", "-sha256", "-sign", "/configured/private-key.pem"])
        self.assertEqual(run.call_args.kwargs["input"], (head + "." + claims).encode())

    def test_returned_write_permission_token_is_never_used(self):
        transport = Transport(Response({"token": "must-not-use", "expires_at": "1970-01-01T02:00:00Z", "permissions": {"metadata": "read", "contents": "write", "pull_requests": "read"}}))
        github = GitHub({"github_app_id": 12, "github_installation_id": 34}, opener=transport, clock=lambda: 1000)
        with patch.object(github, "_app_jwt", return_value="signed-app-jwt"):
            with self.assertRaises(GitHubError):
                github.get_pr("x/y", 1)
        self.assertEqual(len(transport.requests), 1)
        self.assertIsNone(github._token)

    def test_token_creation_requests_only_read_permissions_and_is_cached(self):
        transport = Transport(
            Response({"token": "app-token", "expires_at": "1970-01-01T02:00:00Z", "permissions": {"metadata": "read", "contents": "read", "pull_requests": "read"}}),
            Response({"id": 123}),
            Response({"id": 123}),
        )
        github = GitHub({"github_app_id": 12, "github_installation_id": 34, "github_private_key_file": "/not-read/key"}, opener=transport, clock=lambda: 1000)
        with patch.object(github, "_app_jwt", return_value="signed-app-jwt"):
            self.assertEqual(github.get_pr("x/y", 1)["id"], 123)
            github.get_pr("x/y", 1)
        self.assertEqual([request.get_method() for request in transport.requests], ["POST", "GET", "GET"])
        self.assertEqual(transport.requests[0].full_url, "https://api.github.com/app/installations/34/access_tokens")
        self.assertEqual(json.loads(transport.requests[0].data), {"permissions": {"metadata": "read", "contents": "read", "pull_requests": "read"}})
        self.assertEqual(transport.requests[1].get_header("Authorization"), "Bearer app-token")

    def test_inaccessible_installation_fails_closed(self):
        transport = Transport(http_error(404))
        github = GitHub({"github_app_id": 12, "github_installation_id": 34}, opener=transport, clock=lambda: 1000)
        with patch.object(github, "_app_jwt", return_value="signed-app-jwt"):
            with self.assertRaises(IntegrationRemoved):
                list(github.repositories())

    def test_conditional_get_reuses_data_and_pagination_on_304(self):
        headers = {"ETag": '"one"', "Link": '</repos/x/y/pulls?page=2>; rel="next"'}
        github, transport = client(Response([{"id": 1}], headers), Response([{"id": 2}]), http_error(304), Response([{"id": 2}]))
        self.assertEqual([item["id"] for item in github.open_prs("x/y")], [1, 2])
        self.assertEqual([item["id"] for item in github.open_prs("x/y")], [1, 2])
        self.assertEqual(transport.requests[2].get_header("If-none-match"), '"one"')


if __name__ == "__main__":
    unittest.main()
