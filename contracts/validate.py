"""Validate JSON files against the FiberTwin data contract.

Usage:  python contracts/validate.py <schema-name> <file.json> [...]
        python contracts/validate.py replay contracts/examples/synthetic-f1.json
Exit code 0 = all valid (CI uses this).
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

HERE = Path(__file__).parent


def registry() -> Registry:
    reg = Registry()
    for p in HERE.glob("*.schema.json"):
        schema = json.loads(p.read_text(encoding="utf-8"))
        res = Resource.from_contents(schema)
        reg = reg.with_resource(schema["$id"], res).with_resource(p.name, res)
    return reg


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    name, files = argv[0], argv[1:]
    schema = json.loads((HERE / f"{name}.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=registry(), format_checker=FormatChecker())
    bad = 0
    for f in files:
        errors = sorted(validator.iter_errors(json.loads(Path(f).read_text(encoding="utf-8"))), key=lambda e: list(e.path))
        if errors:
            bad += 1
            print(f"FAIL {f}")
            for e in errors[:20]:
                print(f"   at /{'/'.join(map(str, e.path))}: {e.message}")
        else:
            print(f"OK   {f}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
