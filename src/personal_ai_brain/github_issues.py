"""Private GitHub Issues transport. No Contents write or arbitrary URL surface."""
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

TITLE = "pab-control/v0.1"
REPOSITORY = "igormego/personal-project-brain"


class TransportError(RuntimeError):
    def __init__(self, status, retry_after=60):
        self.status = status
        self.retry_after = retry_after
        super().__init__(status)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHubIssues:
    def __init__(self, token, opener=None, sleep=time.sleep):
        self.token = token
        self.opener = opener or build_opener(NoRedirect()).open
        self.sleep = sleep
        self.last_write = 0

    def _request(self, method, path, payload=None):
        allowed = bool(method == "GET" and (path == "" or re.fullmatch(r"/issues\?state=open&sort=created&direction=asc&per_page=100&page=[1-9]\d*", path)
                   or re.fullmatch(r"/issues/[1-9]\d*", path)))
        allowed |= method == "POST" and bool(re.fullmatch(r"/issues/[1-9]\d*/comments", path)) and set(payload or {}) == {"body"}
        allowed |= method == "PATCH" and bool(re.fullmatch(r"/issues/[1-9]\d*", path)) and payload == {"state": "closed"}
        if not allowed:
            raise TransportError("transport_policy_denied")
        if method != "GET":
            self.sleep(max(0, 1 - (time.monotonic() - self.last_write)))
            self.last_write = time.monotonic()
        request = Request("https://api.github.com/repos/" + REPOSITORY + path,
                          method=method, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                                   "User-Agent": "personal-ai-brain-control"})
        try:
            with self.opener(request, timeout=15) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise TransportError("transport_response_too_large")
                return json.loads(raw)
        except HTTPError as error:
            headers = error.headers or {}
            limited = error.code == 429 or headers.get("X-RateLimit-Remaining") == "0" or headers.get("Retry-After")
            delay = 60
            try:
                delay = max(60, float(headers.get("Retry-After", 0)), float(headers.get("X-RateLimit-Reset", 0)) - time.time())
            except ValueError:
                pass
            status = "transport_unavailable" if limited else ("authentication_permission_failure" if error.code in (401, 403, 404) else "transport_unavailable")
            raise TransportError(status, delay) from None
        except (OSError, URLError, ValueError):
            raise TransportError("transport_unavailable") from None

    def verify_private(self):
        data = self._request("GET", "")
        if not isinstance(data, dict) or data.get("private") is not True or data.get("full_name") != REPOSITORY or not data.get("has_issues"):
            raise TransportError("transport_policy_denied")

    def list_page(self, page):
        self.verify_private()
        issues = self._request("GET", f"/issues?state=open&sort=created&direction=asc&per_page=100&page={page}")
        if not isinstance(issues, list):
            raise TransportError("transport_unavailable")
        return issues

    def get_issue(self, number):
        return self._request("GET", f"/issues/{number}")

    def post_result(self, number, result):
        self.verify_private()
        body = "Personal AI Brain control result\n```json\n" + json.dumps(result, ensure_ascii=True, indent=2) + "\n```"
        self._request("POST", f"/issues/{number}/comments", {"body": body})

    def close_issue(self, number):
        self._request("PATCH", f"/issues/{number}", {"state": "closed"})
