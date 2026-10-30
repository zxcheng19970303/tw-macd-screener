import copy

import pytest

from screener.main import ROOT, load_config

_CFG = load_config(ROOT / "config.yaml")


@pytest.fixture
def cfg():
    """每個測試拿到獨立副本，可放心修改開關。"""
    return copy.deepcopy(_CFG)
