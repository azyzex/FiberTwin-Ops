import json
import subprocess

import pytest

from netgen import model

PREFIX = "clab-ft-core-"


def vtysh(node: str, cmd: str) -> str:
    return subprocess.run(["docker", "exec", PREFIX + node, "vtysh", "-c", cmd],
                          capture_output=True, text=True, check=True).stdout


def vtysh_json(node: str, cmd: str):
    return json.loads(vtysh(node, cmd + " json"))


def sh(node: str, cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", PREFIX + node, "sh", "-c", cmd], capture_output=True, text=True)


@pytest.fixture(scope="session")
def sot():
    return model.load()
