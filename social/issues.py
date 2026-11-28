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
        """Issue body and comments, each with the author's numeric ID and whether anyone edited it.

        REST comments expose no editor, so moderators' edits are read from GraphQL. A comment counts
        as unedited only when GitHub reports no edit time, no editor and no creation edit.
        """
        owner, name = self.base.split("/")[1:3]
        comments, cursor, body = [], None, ""
        for _ in range(50):
            data = self.api(
                "graphql",
                {"query": READ, "variables": dict(owner=owner, name=name, number=int(number), after=cursor)},
            )
            if data.get("errors"):
                raise RuntimeError("GitHub operation failed")
            issue = data["data"]["repository"]["issue"]
            if (issue.get("author") or {}).get("databaseId") != self.actor or issue["state"] != "OPEN":
                return "", []
            body = issue["body"]
            block = issue["comments"]
            for c in block["nodes"]:
                comments.append(
                    {
                        "user": {"id": (c.get("author") or {}).get("databaseId")},
                        "body": c.get("body") or "",
                        "createdAt": c.get("createdAt"),
                        "edited": not (
                            c.get("lastEditedAt") is None
                            and c.get("editor") is None
                            and c.get("includesCreatedEdit") is False
                        ),
                    }
                )
            if not block["pageInfo"]["hasNextPage"]:
                return body, comments
            cursor = block["pageInfo"]["endCursor"]
        raise RuntimeError("GitHub operation failed")


ACTOR = "...on User{databaseId} ...on Bot{databaseId}"
READ = (
    "query($owner:String!,$name:String!,$number:Int!,$after:String){repository(owner:$owner,name:$name)"
    "{issue(number:$number){body state author{" + ACTOR + "} comments(first:100,after:$after)"
    "{pageInfo{hasNextPage endCursor} nodes{body createdAt lastEditedAt includesCreatedEdit"
    " editor{login} author{" + ACTOR + "}}}}}}"
)
