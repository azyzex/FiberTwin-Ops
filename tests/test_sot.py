"""The source of truth must be self-consistent before anything is generated."""
import copy

import pytest

from netgen import model


@pytest.fixture
def sot():
    return model.load()


def test_current_sot_is_valid(sot):
    model.validate(sot)


def test_unknown_fibre_rejected(sot):
    bad = copy.deepcopy(sot); bad["links"]["L12"]["fibres"] = ["F99"]
    with pytest.raises(model.SotError, match="unknown fibre"):
        model.validate(bad)


def test_lightpath_must_be_continuous(sot):
    bad = copy.deepcopy(sot); bad["links"]["L13"]["fibres"] = ["F12", "F34"]   # r1-r2 then r3-r4: a gap
    with pytest.raises(model.SotError, match="does not continue"):
        model.validate(bad)


def test_lightpath_must_end_at_b(sot):
    bad = copy.deepcopy(sot); bad["links"]["L13"]["fibres"] = ["F12"]          # stops at r2, not r3
    with pytest.raises(model.SotError, match="ends at r2"):
        model.validate(bad)


def test_interface_used_twice_rejected(sot):
    bad = copy.deepcopy(sot); bad["customers"]["h1"]["router_if"] = "eth1"     # r2:eth1 is L12
    with pytest.raises(model.SotError, match="used twice"):
        model.validate(bad)


def test_overlapping_subnet_rejected(sot):
    bad = copy.deepcopy(sot); bad["links"]["L23"]["subnet"] = "10.1.12.0/30"
    with pytest.raises(model.SotError, match="overlaps"):
        model.validate(bad)


def test_every_core_link_has_an_alternate_path(sot):
    """Redundancy is a design rule: removing any single link must not split the core."""
    import networkx as nx
    from netgen.whatif import graph
    g = graph(sot).to_undirected()
    assert not list(nx.bridges(g)), "a core link without an alternate path"
