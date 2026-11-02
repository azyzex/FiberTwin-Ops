"""The generator must reproduce exactly the configs that were proven on the lab in Slice 1a.

It renders a FROZEN copy of the 1a source of truth (tests/fixtures/sot-1a.yaml), so the real
sot/network.yaml can change while the generator itself stays proven against lab-tested output.
"""
import json
import sys
from pathlib import Path

import pytest

from netgen import model, render

ROOT = model.ROOT
GOLDEN = ROOT / "labs" / "core-1a" / "configs"
SOT_1A = Path(__file__).parent / "fixtures" / "sot-1a.yaml"


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    return render.render(sot_path=SOT_1A, out=tmp_path_factory.mktemp("build"))


@pytest.mark.parametrize("node", ["r1", "r2", "r3", "r4", "up1"])
@pytest.mark.parametrize("name", ["frr.conf", "daemons", "vtysh.conf"])
def test_generated_equals_lab_proven_config(out, node, name):
    assert (out / "configs" / node / name).read_bytes() == (GOLDEN / node / name).read_bytes()


def test_no_crlf_anywhere(out):
    for f in out.rglob("*"):
        if f.is_file():
            assert b"\r\n" not in f.read_bytes(), f


def test_topology_json_matches_data_contract(out):
    sys.path.insert(0, str(ROOT / "contracts"))
    from validate import registry
    from jsonschema import Draft202012Validator
    schema = json.loads((ROOT / "contracts" / "topology.schema.json").read_text())
    errors = list(Draft202012Validator(schema, registry=registry()).iter_errors(json.loads((out / "topology.json").read_text())))
    assert not errors, [e.message for e in errors]


def test_clab_topology_lists_every_link(out):
    text = (out / "ft-core.clab.yml").read_text()
    for lid in model.load(SOT_1A)["links"]:
        assert f"# {lid}," in text
