"""Read the source of record locally. A change requires a new editorial review."""

import hashlib
import json
import subprocess
from pathlib import Path

PINS = {
    "eden": "2c3b87e9aecb6dbca889ee4844265b9aa85895fbf070dd028954e3445bd6c259",
    "acacia": "0786eb526d52fbeabfbd0f27143a0e8d9f3f469a0bdfec17478ea919dcb5c15d",
    "audit": "d81927f8a607ae32ac685e8cedfafba4a90f0b599f4f9f894cc1b813a6dadc38",
}


def validate_sources(name, data):
    if hashlib.sha256(data).hexdigest() != PINS[name]:
        raise ValueError(f"Review updated {name} source before publishing")


def blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def git_bytes(repo, path, *, missing_ok=False):
    """Read path at origin/main. With missing_ok, an absent file is None; a broken repo still raises."""
    git = ["git", "-C", str(Path(repo).expanduser())]
    quiet = dict(stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if missing_ok:
        subprocess.run([*git, "rev-parse", "--verify", "--quiet", "origin/main^{commit}"], check=True, **quiet)
        listing = subprocess.run([*git, "ls-tree", "-z", "origin/main", "--", path], check=True, **quiet).stdout
        if listing.split(b"\t", 1)[0].split(b" ")[1:2] != [b"blob"]:
            return None
    return subprocess.run([*git, "show", "origin/main:" + path], check=True, **quiet).stdout


def check_remote_blob(path, data):
    endpoint = "repos/Halford-cia/acacia-roofing-systems/contents/" + path + "?ref=main"
    head = subprocess.check_output(
        ["gh", "api", endpoint, "--jq", ".sha"], stderr=subprocess.DEVNULL, timeout=30
    )
    blob = blob_sha(data)
    if head.decode().strip() != blob:
        raise ValueError("Local source differs from current upstream main")


def check(brand, config, captions=(), *, live=False):
    if brand == "eden":
        validate_sources("eden", Path(config["brief"]).expanduser().read_bytes())
        return
    repo = config["acaciaRepo"]
    facts_path = "docs/marketing/FACTS.md"
    audit_path = "scripts/audit/website-facts.test.mjs"
    facts = git_bytes(repo, facts_path)
    validate_sources("acacia", facts)
    audit = git_bytes(repo, audit_path)
    if live:
        check_remote_blob(facts_path, facts)
        check_remote_blob(audit_path, audit)
    validate_sources("audit", audit)
    # Use the pinned upstream scanner verbatim, excluding its website-only test suite.
    code = audit.decode().split("const H132_LINE =", 1)[1].split("function htmlFiles(", 1)[0]
    program = (
        "import path from 'node:path';import fs from 'node:fs';\nconst H132_LINE ="
        + code
        + "\nconst captions=JSON.parse(fs.readFileSync(0,'utf8'));"
        "if(captions.some(s=>findViolations('social.html',s).length)) process.exit(1);"
    )
    subprocess.run(
        ["node", "--input-type=module", "-e", program],
        check=True,
        input=json.dumps(list(captions)).encode(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
