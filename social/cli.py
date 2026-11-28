import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from .api import Blotato, secret
from .claude import Claude, RunLimit
from .issues import Issues
from .planner import BRANDS, CT, plan, preview
from .runtime import State, tick_brand, validate_config
from .writing import write_week

ROOT = Path(__file__).resolve().parents[1]
DATA = Path.home() / "Library/Application Support/social-autopilot"
CONFIG = DATA / "config.json"


def load_example():
    return json.loads((ROOT / "config.example.json").read_text())


class DemoWriter:
    """Simulated Claude output, used only by the demo and offline tests."""

    def generate(self, model, system, prompt, max_tokens):
        brief = json.loads(prompt)
        captions = []
        for i, day in enumerate(brief["days"]):
            terms = day["topics"]
            pattern = brief["patterns"][i % len(brief["patterns"])]
            hook = pattern.format(a=terms[0], b=terms[-1])
            parts = (hook, day["fact"]) if i % 2 == 0 else (day["fact"], hook)
            captions.append(" ".join(parts))
        return json.dumps(captions)


DEMO_DESTINATIONS = {
    "eden": {"instagram": "@fixture_stay_eden", "facebook": "Stay Eden (fixture Page)"},
    "acacia": {
        "instagram": "@fixture_acacia_roofing",
        "facebook": "Acacia Roofing (fixture Page)",
        "googlebusiness": "Acacia Roofing (fixture location)",
    },
}


def demo(start):
    blocks = ["# Mock week · offline preview", "All media and destinations in this example are fixtures."]
    targets = load_example()["brands"]
    for brand in BRANDS:
        images = iter(range(1, 8))
        batch = plan(
            brand,
            start,
            lambda _, brand=brand, images=images: (
                f"https://media.example.invalid/mock-{brand}-{next(images)}.jpg"
            ),
            targets[brand]["targets"],
            compose=lambda b, w, cards: write_week(b, w, cards, DemoWriter()),
            destinations={
                p: dict(targets[brand]["targets"][p], name=name) for p, name in DEMO_DESTINATIONS[brand].items()
            },
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
    with State(DATA / "ledger") as store:
        writer = Claude(cfg["claude"], store)
        try:
            writer.check_limit()
            api = Blotato(secret("BLOTATO_API_KEY"))
            issues = Issues(cfg["operationsRepo"])
            if issues.actor == cfg["approverId"]:
                raise ValueError("Use a distinct bot identity for the Mini")
            failed = False
            for brand in BRANDS:
                writer.check_limit()
                try:
                    tick_brand(brand, cfg, store, datetime.now(CT), api, issues, writer)
                except RunLimit:
                    raise
                except Exception as exc:
                    failed = True
                    print(
                        f"{brand}: cycle incomplete ({type(exc).__name__}, HTTP {getattr(exc, 'status', 0)}).",
                        file=sys.stderr,
                    )
        except RunLimit as exc:
            print(f"Cycle stopped: {exc}.", file=sys.stderr)
            return 1
    return int(failed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BlockingIOError:
        sys.exit(0)  # another tick holds the lock
    except Exception as exc:
        print(
            f"Cycle unavailable ({type(exc).__name__}). Check setup and the local ledger.", file=sys.stderr
        )
        sys.exit(1)
