"""
Import UID mappings from a simple text file into coins.yaml.

Expected line format:
  Name of coin <uid>

Example:
  Never gonna give you up 04b0aaa21f1d90
"""

import argparse
import re
from pathlib import Path

import yaml


LINE_RE = re.compile(r"^(?P<name>.+?)\s+(?P<uid>[0-9a-fA-F]{8,32})$")


def normalize_uid(uid: str) -> str:
    uid = uid.strip().upper().replace(":", "")
    return ":".join(uid[i:i + 2] for i in range(0, len(uid), 2))


def parse_lines(path: Path) -> dict:
    entries = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("-"):
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name").strip()
        uid = normalize_uid(m.group("uid"))
        entries[uid] = name
    return entries


def main():
    parser = argparse.ArgumentParser(description="Import NFC UIDs from text file")
    parser.add_argument("--input", required=True, help="Input text file path")
    parser.add_argument("--coins", default="coins.yaml", help="coins.yaml path")
    parser.add_argument("--volume", type=int, default=25, help="Default Sonos volume")
    args = parser.parse_args()

    input_path = Path(args.input)
    coins_path = Path(args.coins)

    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")
    if not coins_path.exists():
        raise SystemExit(f"Coins file not found: {coins_path}")

    rows = parse_lines(input_path)
    if not rows:
        raise SystemExit("No valid 'name uid' rows found in input file")

    with open(coins_path) as f:
        data = yaml.safe_load(f) or {}
    coins = data.setdefault("coins", {})

    for uid, name in rows.items():
        coins[uid] = {
            "name": name,
            "expires": None,
            "actions": [
                {
                    "type": "sonos_play",
                    "favorite": name,
                    "volume": args.volume,
                }
            ],
        }

    with open(coins_path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=False)

    print(f"Imported {len(rows)} coin(s) into {coins_path}")


if __name__ == "__main__":
    main()

