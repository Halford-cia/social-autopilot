import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from .api import Blotato, secret
from .issues import Issues
from .planner import BRANDS, CT, plan, preview
from .runtime import State, tick_brand, validate_config

ROOT = Path(__file__).resolve().parents[1]
DATA = Path.home() / "Library/Application Support/social-autopilot"
CONFIG = DATA / "config.json"


def load_example():
    return json.loads((ROOT / "config.example.json").read_text())


def demo(start):
    blocks = ["# Mock week · offline preview", "All media and destinations in this example are fixtures."]
    for brand in BRANDS:
        images = iter(range(1, 8))
        batch = plan(
            brand,
            start,
            lambda _, brand=brand, images=images: (
                f"https://media.example.invalid/mock-{brand}-{next(images)}.jpg"
            ),
        )
        blocks.append(preview(batch))
    return "\n\n".join(blocks)


def main():
    parser = argparse.ArgumentParser(description="Weekly social publishing for the Mini")
    subs = parser.add_subparsers(dest="command", required=True)
    demo_cmd = subs.add_parser("demo", help="render both previews, offline")
    demo_cmd.add_argument("--week", type=date.fromisoformat, default=date(2026, 11, 30))
    tick = subs.add_parser("tick", help="run one production cycle with configured destinations")
    tick.add_argument("--config", type=Path, default=CONFIG)
    subs.add_parser("accounts", help="read connected destination IDs for setup")
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "demo":
        print(demo(args.week))
        return 0
    if args.command == "accounts":
        from urllib.parse import quote

        api = Blotato(secret("BLOTATO_API_KEY"))
        for item in api.get("/users/me/accounts")["items"]:
            platform = item["platform"]
            if platform not in ("instagram", "facebook", "googlebusiness"):
                continue
            print(platform, item["id"], item.get("username", ""))
            if platform != "instagram":
                for sub in api.get("/users/me/accounts/" + quote(item["id"], safe="") + "/subaccounts")[
                    "items"
                ]:
                    print("  ", sub["id"], sub["name"])
        return 0
    cfg = json.loads(args.config.expanduser().read_text())
    validate_config(cfg)
    api = Blotato(secret("BLOTATO_API_KEY"))
    issues = Issues(cfg["operationsRepo"])
    if issues.actor == cfg["approverId"]:
        raise ValueError("Use a distinct bot identity for the Mini")
    failed = False
    with State(DATA / "ledger") as store:
        for brand in BRANDS:
            try:
                tick_brand(brand, cfg, store, datetime.now(CT), api, issues)
            except Exception as exc:
                failed = True
                # Transport failures may carry signed URLs or private data. Only emit class/status.
                print(
                    f"{brand}: cycle incomplete ({type(exc).__name__}, HTTP {getattr(exc, 'status', 0)}).",
                    file=sys.stderr,
                )
    return int(failed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BlockingIOError:
        sys.exit(0)  # another tick holds the lock
    except Exception as exc:
        print(f"Cycle unavailable ({type(exc).__name__}). Check setup and the local ledger.", file=sys.stderr)
        sys.exit(1)
