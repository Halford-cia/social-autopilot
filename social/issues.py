"""GitHub Issues is the phone UI. The Mini only uses the existing gh login."""

import json
import re
import subprocess


class Issues:
    def __init__(self, repo):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise ValueError("Set the private operations repository")
        self.base = "repos/" + repo
        if self.api(self.base)["private"] is not True:
            raise ValueError("Use a private repository for approvals and handoffs")
        self.actor = self.api("user")["id"]
        self.cache = None

    def api(self, endpoint, body=None, paginate=False):
        args = ["gh", "api", endpoint]
        if paginate:
            args.extend(["--paginate", "--slurp"])
        if body is not None:
            args.extend(["--method", "POST", "--input", "-"])
        result = subprocess.run(
            args,
            input=json.dumps(body) if body is not None else None,
            text=True,
            capture_output=True,
            check=False,
            timeout=90,
        )
        if result.returncode:
            raise RuntimeError("GitHub operation failed")
        return json.loads(result.stdout)

    def ensure(self, title, body):
        if self.cache is None:
            blocks = self.api(self.base + "/issues?state=all&per_page=100", paginate=True)
            self.cache = {
                i["title"]: i["number"]
                for block in blocks
                for i in block
                if "pull_request" not in i and i["user"]["id"] == self.actor
            }
        if title not in self.cache:
            issue = self.api(self.base + "/issues", {"title": title, "body": body})
            self.cache[title] = issue["number"]
        return self.cache[title]

    def open(self, number):
        return self.api(f"{self.base}/issues/{int(number)}")["state"] == "open"

    def read(self, number):
        issue = self.api(f"{self.base}/issues/{int(number)}")
        if issue["user"]["id"] != self.actor or issue["state"] != "open":
            return "", []
        pages = self.api(f"{self.base}/issues/{int(number)}/comments?per_page=100", paginate=True)
        return issue["body"], [c for page in pages for c in page]
