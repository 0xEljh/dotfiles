"""GET-only GitHub reads with explicit App or acknowledged personal authentication.

Personal credentials remain powerful; only this client's requests are GET-only.
Neither authentication mode falls back to the other.
"""

from __future__ import annotations

import base64
import copy
import json
import math
import os
import random
import re
import subprocess
import threading
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .common import Blocked, canonical, check_secrets
from .config import DEFAULTS, validate_github
from .git_ops import current_base_snapshot


API_ORIGIN = "https://api.github.com"
READ_PERMISSIONS = {"metadata": "read", "contents": "read", "pull_requests": "read"}


class GitHubError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, retry_at: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_at = retry_at


class RequestBudgetExceeded(GitHubError):
    """Defer discovery; a partially consumed iterator is not a successful scan."""


class IntegrationRemoved(GitHubError):
    """The configured App installation is absent, suspended, or inaccessible."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # urllib otherwise forwards Authorization on redirects, even across hosts.
        return None


def _positive_id(value, field: str) -> int:
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() or int(value) <= 0:
        raise ValueError(f"{field} must be a positive numeric GitHub ID")
    return int(value)


def _timestamp(value: str) -> float:
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError
        return instant.timestamp()
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise GitHubError("GitHub returned an invalid timestamp") from None


def _headers(value) -> dict[str, str]:
    return {str(key).lower(): str(item) for key, item in value.items()}


class GitHub:
    """One bounded polling pass; the coordinator owns durable scan cursors.

    Exhaust an inventory iterator before committing its baseline or scan cursor.
    Budget, pagination, authentication, and transport failures propagate rather
    than looking like an empty final page. Recreate the client for a new request
    budget. ``retry_at`` lets the coordinator persist throttling across passes.
    """

    def __init__(self, config: dict, *, opener=None, clock=None):
        validate_github(config)
        self.config = copy.deepcopy(config)
        self.auth = config.get("github_auth", "app")
        if self.auth == "app":
            self.app_id = _positive_id(config.get("github_app_id") or os.environ.get("GITHUB_APP_ID"), "github_app_id")
            self.installation_id = _positive_id(config.get("github_installation_id") or os.environ.get("GITHUB_INSTALLATION_ID"), "github_installation_id")
        self.remaining = int(config.get("api_budget", 300))
        if self.remaining < 0:
            raise ValueError("api_budget must not be negative")
        self.retry_at = float(config.get("github_retry_at", 0))
        self.rate_remaining: int | None = None
        self.rate_reset_at: float | None = None
        self.timeout = float(config.get("github_timeout", 8))
        self.max_response = int(config.get("github_max_response_bytes", 32 * 1024 * 1024))
        if self.timeout <= 0 or self.max_response <= 0:
            raise ValueError("GitHub timeout and response limit must be positive")
        self._clock = clock or time.time
        self._opener = opener or build_opener(_NoRedirect())
        self._lock = threading.RLock()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._personal_identity_verified = False
        self._personal_organization_verified = False
        self._private_repositories: dict[str, int] = {}
        self._failures = 0
        self._cache: dict[str, tuple[str, object, dict]] = {}

    @staticmethod
    def _url(path: str, params: dict | None = None) -> str:
        if not isinstance(path, str) or any(ord(char) < 33 or ord(char) == 127 for char in path):
            raise GitHubError("invalid GitHub API URL")
        if path.startswith("//"):
            raise GitHubError("GitHub API pagination changed origin")
        url = API_ORIGIN + path if path.startswith("/") else path
        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname == "api.github.com"
                and parsed.port in (None, 443)
                and parsed.username is None
                and parsed.password is None
                and not parsed.fragment
                and not parsed.netloc.endswith(":")
            )
        except ValueError:
            valid = False
        if not valid:
            raise GitHubError("GitHub API pagination changed origin")
        if params:
            url += ("&" if parsed.query else "?") + urlencode(params)
        return url

    @staticmethod
    def _repo(full_name: str) -> str:
        if not isinstance(full_name, str) or len(full_name.split("/")) != 2:
            raise ValueError("repository must be owner/name")
        if any(part in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in full_name.split("/")):
            raise ValueError("invalid GitHub repository name")
        return full_name

    @staticmethod
    def _identity(item: object) -> dict:
        if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0:
            raise GitHubError("GitHub response lacks an immutable numeric ID")
        return item

    def _reserve(self) -> None:
        with self._lock:
            if self._clock() < self.retry_at:
                raise RequestBudgetExceeded("GitHub requests deferred by backoff", retry_at=self.retry_at)
            if self.remaining <= 0:
                raise RequestBudgetExceeded("GitHub request budget exhausted", retry_at=self.retry_at or None)
            self.remaining -= 1

    def _observe_rate(self, headers: dict) -> None:
        try:
            if "x-ratelimit-remaining" in headers:
                self.rate_remaining = int(headers["x-ratelimit-remaining"])
            if "x-ratelimit-reset" in headers:
                self.rate_reset_at = float(headers["x-ratelimit-reset"])
        except (ValueError, OverflowError):
            return
        if self.rate_remaining == 0:
            self.retry_at = max(self.retry_at, self.rate_reset_at or self._clock() + 60)

    def _defer(self, headers: dict, *, limited: bool) -> None:
        now = self._clock()
        self._failures += 1
        retry_at = now + min(300, 2 ** min(self._failures, 8)) + random.random()
        if limited:
            retry_at = now + 60
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                retry_at = now + max(0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after).timestamp()
                except (TypeError, ValueError, OverflowError):
                    pass
        if headers.get("x-ratelimit-remaining") == "0" and self.rate_reset_at:
            retry_at = max(retry_at if retry_after else now, self.rate_reset_at)
        self.retry_at = max(self.retry_at, now + 1, retry_at)

    def _send(self, url: str, *, token: str, token_request: bool = False, etag: str | None = None):
        if token_request and self.auth == "gh":
            raise GitHubError("token minting is forbidden in personal GitHub authentication mode")
        url = self._url(url)
        if self.auth == "gh":
            self._personal_path(url)
        self._reserve()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pr-maintenance-read-only",
        }
        if etag:
            headers["If-None-Match"] = etag
        data = None
        if token_request:
            # This private path is the sole mutation: it mints a read-only token.
            expected = f"{API_ORIGIN}/app/installations/{self.installation_id}/access_tokens"
            if url != expected:
                raise GitHubError("refusing unexpected authentication endpoint")
            data = canonical({"permissions": READ_PERMISSIONS}).encode()
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method="POST" if token_request else "GET")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                response_headers = _headers(response.headers)
                self._observe_rate(response_headers)
                raw = response.read(self.max_response + 1)
                if len(raw) > self.max_response:
                    raise GitHubError("GitHub API response exceeds configured size limit")
                try:
                    result = json.loads(raw)
                except (UnicodeError, ValueError):
                    raise GitHubError("GitHub API returned invalid JSON") from None
                self._failures = 0
                return result, response_headers
        except HTTPError as error:
            status = error.code
            response_headers = _headers(error.headers)
            # Secondary limits need not include Retry-After. Inspect only their
            # bounded error message, never expose the response body to logs.
            rate_message = False
            try:
                if status == 403:
                    failure = json.loads(error.read(min(self.max_response, 65536)))
                    message = failure.get("message", "") if isinstance(failure, dict) else ""
                    rate_message = isinstance(message, str) and any(phrase in message.lower() for phrase in ("rate limit", "abuse detection"))
            except (ValueError, UnicodeError, OSError):
                pass
            finally:
                error.close()
            self._observe_rate(response_headers)
            if status == 304 and not token_request:
                return None, response_headers | {"not-modified": "true"}
            limited = status == 429 or (status == 403 and (rate_message or response_headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response_headers))
            if limited or status >= 500 or status == 408:
                self._defer(response_headers, limited=limited)
                kind = RequestBudgetExceeded if limited else GitHubError
                raise kind("GitHub API request deferred", status=status, retry_at=self.retry_at) from None
            if self.auth == "gh" and status in (401, 403, 404):
                raise GitHubError(f"personal GitHub authentication or access denied (HTTP {status})", status=status) from None
            if status == 401 or (token_request and status in (403, 404)) or (status in (403, 404) and urlsplit(url).path == "/installation/repositories"):
                self._token = None
                raise IntegrationRemoved("GitHub App installation is unavailable", status=status) from None
            raise GitHubError(f"GitHub API request failed (HTTP {status})", status=status) from None
        except (URLError, OSError, TimeoutError):
            self._defer({}, limited=False)
            raise GitHubError("GitHub API transport failed", retry_at=self.retry_at) from None

    def _app_jwt(self) -> str:
        if self.auth != "app":
            raise GitHubError("App signing is forbidden in personal GitHub authentication mode")
        key_file = self.config.get("github_private_key_file") or os.environ.get("GITHUB_APP_PRIVATE_KEY_FILE")
        if not key_file or not isinstance(key_file, str):
            raise GitHubError("configure a GitHub App private key file")

        def encode(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        now = int(self._clock())
        signing_input = ".".join((
            encode(canonical({"alg": "RS256", "typ": "JWT"}).encode()),
            encode(canonical({"iat": now - 60, "exp": now + 540, "iss": str(self.app_id)}).encode()),
        ))
        try:
            signed = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", key_file],
                input=signing_input.encode("ascii"), capture_output=True, timeout=self.timeout, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise GitHubError("GitHub App JWT signing failed") from None
        if signed.returncode or not signed.stdout:
            raise GitHubError("GitHub App JWT signing failed")
        return signing_input + "." + encode(signed.stdout)

    def _installation_token(self) -> str:
        if self.auth != "app":
            raise GitHubError("token minting is forbidden in personal GitHub authentication mode")
        with self._lock:
            if self._token and self._clock() < self._token_expires_at:
                return self._token
            # Check backoff/budget before invoking openssl or reading credentials.
            if self.remaining <= 0 or self._clock() < self.retry_at:
                raise RequestBudgetExceeded("GitHub authentication deferred", retry_at=self.retry_at or None)
            result, _ = self._send(
                f"{API_ORIGIN}/app/installations/{self.installation_id}/access_tokens",
                token=self._app_jwt(), token_request=True,
            )
            if not isinstance(result, dict) or not isinstance(result.get("token"), str) or not result["token"]:
                raise GitHubError("GitHub returned an invalid installation token")
            permissions = result.get("permissions")
            if not isinstance(permissions, dict) or any(value != "read" for value in permissions.values()) or any(permissions.get(key) != value for key, value in READ_PERMISSIONS.items()):
                raise GitHubError("GitHub installation token lacks the required read-only permissions")
            expires_at = _timestamp(result.get("expires_at")) - 60
            if expires_at <= self._clock():
                raise GitHubError("GitHub returned an expired installation token")
            self._token = result["token"]
            self._token_expires_at = expires_at
            return self._token

    def _private_organization_mode(self) -> bool:
        return self.auth == "gh" and self.config.get("organization_wide", False)

    def _private_inventory_paths(self) -> tuple[str, str]:
        organization = self.config.get("organization", DEFAULTS["organization"])
        organization_id = self.config.get("organization_id", DEFAULTS["organization_id"])
        # GitHub canonicalizes named inventory pagination to the immutable ID.
        return (f"/orgs/{organization}/repos", f"/organizations/{organization_id}/repos")

    def _private_repository(self, item: object, expected_name: str | None = None) -> dict:
        item = self._identity(item)
        owner, name = item.get("owner"), item.get("full_name")
        try:
            self._repo(name)
        except ValueError:
            raise GitHubError("GitHub repository lacks a valid scoped name") from None
        names = self.config.get("allow_repositories", [])
        ids = self.config.get("allow_repository_ids", [])
        previous_id = self._private_repositories.get(name.casefold())
        if (item.get("private") is not True or not isinstance(owner, dict)
                or type(owner.get("id")) is not int
                or owner["id"] != self.config.get("organization_id", DEFAULTS["organization_id"])
                or name.split("/")[0].casefold() != self.config.get("organization", DEFAULTS["organization"]).casefold()
                or (expected_name and name.casefold() != expected_name.casefold())
                or (names and name.casefold() not in {value.casefold() for value in names})
                or (ids and item["id"] not in ids)
                or (previous_id is not None and item["id"] != previous_id)):
            raise GitHubError("GitHub repository identity or visibility is outside configured private scope")
        self._private_repositories[name.casefold()] = item["id"]
        return item

    def _personal_path(self, url: str, *, allow_unvalidated_repo: bool = False) -> None:
        parsed = urlsplit(url)
        path = parsed.path
        if path == "/user":
            return
        private_mode = self._private_organization_mode()
        organization = self.config.get("organization", DEFAULTS["organization"])
        if private_mode and path == f"/orgs/{organization}":
            return
        if private_mode and path in self._private_inventory_paths():
            query = parse_qs(parsed.query, keep_blank_values=True)
            if query.get("type") == ["private"] and set(query) <= {"type", "per_page", "page"}:
                return
        parts = path.split("/")
        if len(parts) >= 4 and parts[1] == "repos":
            name = "/".join(parts[2:4]).casefold()
            allowed = {name.casefold() for name in self.config.get("allow_repositories", [])}
            scoped = (parts[2].casefold() == organization.casefold() and (not allowed or name in allowed)) if private_mode else name in allowed
            # Reject dot segments and encoded separators rather than relying on
            # how the remote HTTP server might normalize a pagination URL.
            validated = not private_mode or len(parts) == 4 or name in self._private_repositories or allow_unvalidated_repo
            if scoped and validated and all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in (".", "..") for part in parts[2:]):
                return
        raise GitHubError("GitHub API request is outside configured personal repository scope")

    def _verify_personal_organization(self) -> None:
        if not self._personal_organization_verified:
            organization = self.config.get("organization", DEFAULTS["organization"])
            item = self._identity(self.get(f"/orgs/{organization}"))
            if item["id"] != self.config.get("organization_id", DEFAULTS["organization_id"]):
                raise GitHubError("GitHub organization does not match configured organization_id")
            self._personal_organization_verified = True

    def _personal_token(self) -> str:
        with self._lock:
            if self.remaining <= 0 or self._clock() < self.retry_at:
                raise RequestBudgetExceeded("GitHub authentication deferred", retry_at=self.retry_at or None)
            if self._token is None:
                try:
                    result = subprocess.run(
                        ["gh", "auth", "token", "--hostname", "github.com"],
                        capture_output=True, timeout=self.timeout, check=False,
                    )
                    token = result.stdout.decode("ascii").strip()
                except (OSError, subprocess.SubprocessError, UnicodeError):
                    raise GitHubError("host-local GitHub authentication failed") from None
                if result.returncode or not token or any(ord(char) < 33 or ord(char) > 126 for char in token):
                    raise GitHubError("host-local GitHub authentication failed")
                self._token = token
            if not self._personal_identity_verified:
                user, _ = self._send(API_ORIGIN + "/user", token=self._token)
                if self._identity(user)["id"] != self.config.get("author_id", DEFAULTS["author_id"]):
                    raise GitHubError("GitHub authenticated account does not match configured author_id")
                self._personal_identity_verified = True
            return self._token

    def _get(self, path: str, params: dict | None = None):
        url = self._url(path, params)
        if self.auth == "gh":
            self._personal_path(url, allow_unvalidated_repo=True)
        token = self._personal_token() if self.auth == "gh" else self._installation_token()
        parts = urlsplit(url).path.split("/")
        if self._private_organization_mode() and (parts[1] == "repos" or len(parts) == 4):
            self._verify_personal_organization()
            if parts[1] == "repos" and len(parts) > 4:
                name = "/".join(parts[2:4])
                if name.casefold() not in self._private_repositories:
                    self.get(f"/repos/{name}")
        cached = self._cache.get(url)
        result, headers = self._send(url, token=token, etag=cached[0] if cached else None)
        if "not-modified" in headers:
            if not cached:
                raise GitHubError("GitHub returned 304 without a cached response")
            result, headers = copy.deepcopy(cached[1]), cached[2] | headers
        if self._private_organization_mode() and parts[1] == "repos" and len(parts) == 4:
            name = "/".join(parts[2:4])
            try:
                self._private_repository(result, name)
            except GitHubError:
                self._private_repositories.pop(name.casefold(), None)
                raise
        if headers.get("etag") and self.config.get("github_conditional_requests", True):
            self._cache[url] = (headers["etag"], copy.deepcopy(result), dict(headers))
        else:
            self._cache.pop(url, None)
        return result, headers

    def get(self, path: str, params: dict | None = None):
        """GET JSON from this API origin only; no arbitrary HTTP method exists."""
        return self._get(path, params)[0]

    def request(self, path: str, *, params: dict | None = None):
        return self.get(path, params)

    @staticmethod
    def _next_link(header: str, current_url: str) -> str | None:
        for match in re.finditer(r"<([^>]+)>([^,]*)", header):
            relation = re.search(r';\s*rel\s*=\s*(?:"([^"]*)"|([^;\s,]+))', match[2], re.IGNORECASE)
            if relation and "next" in (relation[1] or relation[2]).split():
                return GitHub._url(urljoin(current_url, match[1]))
        return None

    def pages(self, path: str, *, params: dict | None = None, items_key: str | None = None):
        url = self._url(path, {"per_page": 100} | (params or {}))
        endpoint = urlsplit(url).path
        endpoints = (endpoint,)
        if self._private_organization_mode() and endpoint in self._private_inventory_paths():
            endpoints = self._private_inventory_paths()
        seen = set()
        while url:
            if urlsplit(url).path not in endpoints:
                raise GitHubError("GitHub pagination changed endpoint")
            if url in seen:
                raise GitHubError("GitHub pagination repeated a cursor")
            seen.add(url)
            result, headers = self._get(url)
            items = result.get(items_key) if items_key and isinstance(result, dict) else result
            if not isinstance(items, list):
                raise GitHubError("GitHub paginated response is not a list")
            yield from items
            url = self._next_link(headers.get("link", ""), url)

    def repositories(self):
        if self._private_organization_mode():
            self._verify_personal_organization()
            organization = self.config.get("organization", DEFAULTS["organization"])
            for item in self.pages(f"/orgs/{organization}/repos", params={"type": "private"}):
                try:
                    yield self._private_repository(item)
                except GitHubError:
                    continue
            return
        if self.auth == "gh":
            for name in self.config["allow_repositories"]:
                item = self._identity(self.get(f"/repos/{self._repo(name)}"))
                owner = item.get("owner")
                if (item["id"] not in self.config["allow_repository_ids"] or not isinstance(owner, dict)
                        or type(owner.get("id")) is not int
                        or owner["id"] != self.config.get("organization_id", DEFAULTS["organization_id"])
                        or not isinstance(item.get("full_name"), str)
                        or item["full_name"].casefold() != name.casefold()):
                    raise GitHubError("GitHub repository identity is outside configured personal scope")
                if self.config.get("private_repositories_only", False):
                    item = self._private_repository(item, name)
                yield item
            return
        for item in self.pages("/installation/repositories", items_key="repositories"):
            if self.config.get("private_repositories_only", False):
                try:
                    yield self._private_repository(item)
                except GitHubError:
                    continue
            else:
                yield self._identity(item)

    def get_pr(self, owner_repo: str, number: int) -> dict:
        """Read PR metadata, resolving the execution base for an open ready PR."""
        repo = self._repo(owner_repo)
        number = _positive_id(number, "PR number")
        result = self._identity(self.get(f"/repos/{repo}/pulls/{number}"))
        if result.get("state") != "open" or result.get("draft") is True:
            return result
        try:
            base = result["base"]
            if (result.get("draft") is not False or not isinstance(base, dict)
                    or not isinstance(base.get("repo"), dict)
                    or not isinstance(base["repo"].get("full_name"), str)
                    or base["repo"].get("full_name", "").casefold() != repo.casefold()
                    or not isinstance(base.get("ref"), str) or not base["ref"]):
                raise GitHubError("GitHub PR base identity is incomplete or outside the requested repository")
            evidence = self.get(f"/repos/{repo}/git/ref/heads/" + quote(base["ref"], safe="/"))
            return current_base_snapshot(result, evidence)
        except (KeyError, TypeError, ValueError, Blocked) as error:
            raise GitHubError("GitHub current base-ref snapshot is unavailable or invalid") from error

    def open_prs(self, owner_repo: str):
        repo = self._repo(owner_repo)
        for item in self.pages(f"/repos/{repo}/pulls", params={"state": "open"}):
            yield self._identity(item)

    def updated_prs(self, owner_repo: str, since: float):
        repo = self._repo(owner_repo)
        if not isinstance(since, (int, float)) or not math.isfinite(since):
            raise ValueError("since must be a finite Unix timestamp")
        for item in self.pages(f"/repos/{repo}/pulls", params={"state": "all", "sort": "updated", "direction": "desc"}):
            self._identity(item)
            if _timestamp(item.get("updated_at")) < since:
                return
            yield item

    def feedback(self, owner_repo: str, number: int) -> dict:
        repo = self._repo(owner_repo)
        number = _positive_id(number, "PR number")
        snapshot = {"started_at": self._clock()}
        for key, endpoint in (
            ("conversation", f"/repos/{repo}/issues/{number}/comments"),
            ("reviews", f"/repos/{repo}/pulls/{number}/reviews"),
            ("inline", f"/repos/{repo}/pulls/{number}/comments"),
        ):
            snapshot[key] = [self._identity(item) for item in self.pages(endpoint)]
        snapshot["finished_at"] = self._clock()
        check_secrets(canonical(snapshot))
        return snapshot
