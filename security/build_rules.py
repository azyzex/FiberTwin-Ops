"""Sigma -> Loki ruler alerts (Slice 3).

The detections are written ONCE, as vendor-neutral Sigma rules (security/sigma/*.yml). pySigma's Loki
backend turns each correlation rule into a LogQL query; this script wraps those queries into Loki ruler
alerts (the backend's own "ruler" output nests correlation rules incorrectly, measured with
pySigma-backend-loki 0.16), adding the ATT&CK technique and severity as labels for the response playbook.

    python security/build_rules.py   ->  monitoring/loki/rules/fake/fibertwin-sigma.yml
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from sigma.backends.loki import LogQLBackend
from sigma.collection import SigmaCollection
from sigma.processing.pipeline import ProcessingPipeline

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "monitoring" / "loki" / "rules" / "fake" / "fibertwin-sigma.yml"


def alert_name(title: str) -> str:
    return "Ft" + "".join(w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", title.split(" - ", 1)[-1]))[:60]


def build() -> dict:
    texts = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "security" / "sigma").glob("*.yml"))]
    coll = SigmaCollection.from_yaml("\n---\n".join(texts))
    pipeline = ProcessingPipeline.from_yaml((ROOT / "security" / "pipelines" / "pipeline-fibertwin.yml").read_text())
    queries = LogQLBackend(processing_pipeline=pipeline).convert(coll)
    correlations = [r for r in coll.rules if r.__class__.__name__ == "SigmaCorrelationRule"]
    assert len(queries) == len(correlations), (len(queries), len(correlations))
    rules = []
    for rule, q in zip(correlations, queries):
        technique = next((t.name.upper() for t in rule.tags if t.name.lower().startswith("t1")), "")
        groups = list(rule.group_by or [])
        rules.append({
            "alert": alert_name(rule.title.split(" (")[0]),
            "expr": q,
            "for": "0s",
            "labels": {"severity": str(rule.level.name).lower() if rule.level else "high",
                       "attack_technique": technique, "source": "sigma"},
            "annotations": {"summary": rule.title + " from {{ $labels.src_ip }}"
                            + (" on {{ $labels.container }}" if "container" in groups else ""),
                            "description": rule.description or "", "sigma_id": str(rule.id)},
        })
    return {"groups": [{"name": "fibertwin-sigma", "interval": "10s", "rules": rules}]}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    OUT.write_bytes(("# GENERATED from security/sigma by security/build_rules.py. Do not edit.\n"
                     + yaml.safe_dump(data, sort_keys=False, width=200)).encode())
    for r in data["groups"][0]["rules"]:
        print(f"{r['alert']} [{r['labels']['attack_technique']}]: {r['expr']}")


if __name__ == "__main__":
    main()
