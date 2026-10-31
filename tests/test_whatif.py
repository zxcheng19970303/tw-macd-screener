from datetime import date

import pytest

from screener import demo, universe, whatif

DAY = date(2026, 10, 30)
RO = "{fast: 10, slow: 20, signal: 10}"


def test_parse_and_apply_overrides(cfg):
    sets = whatif.parse_sets([f"macd.mi={RO}; switches.d2_mode=shrink_2d", "thresholds.volume_lots=500"])
    out = whatif.apply_overrides(cfg, sets)
    assert out["macd"]["mi"] == {"fast": 10, "slow": 20, "signal": 10}
    assert out["switches"]["d2_mode"] == "shrink_2d"
    assert out["thresholds"]["volume_lots"] == 500
    assert cfg["macd"]["mi"]["fast"] == 26  # 原設定不被修改


@pytest.mark.parametrize("item, err", [("macd.xx=1", KeyError), ("switches.d2_mode=shrink_9d", ValueError), ("no_equals", ValueError)])
def test_bad_overrides(cfg, item, err):
    with pytest.raises(err):
        whatif.apply_overrides(cfg, whatif.parse_sets([item]))


def test_mi_equal_ro_makes_d3_d4_redundant(cfg, monkeypatch, capsys):
    names, histories = demo.demo_dataset(DAY, n=60)
    markets, intraday = demo.demo_markets(names), demo.demo_intraday(histories)
    uc = cfg["universe"]
    univ = universe.combine(
        [universe.parse_quotes(demo.quotes(DAY, names, histories, markets, m), m, uc["code_pattern"], uc["exclude_name_patterns"]) for m in cfg["markets"]]
    )
    monkeypatch.setattr(whatif.universe, "load_universe", lambda m, c, d: univ)
    monkeypatch.setattr(whatif.prices, "fetch_history", lambda codes, *a, **k: histories)
    monkeypatch.setattr(whatif.prices, "fetch_intraday", lambda codes, *a, **k: intraday)
    captured = {}
    real_summary = whatif.summary
    monkeypatch.setattr(whatif, "summary", lambda d, s, b, v, m: captured.update(base=b, variant=v) or real_summary(d, s, b, v, m))

    assert whatif.main(["--date", "2026-10-30", "--set", f"macd.mi={RO}; macd.bo={RO}"]) == 0
    base, variant = captured["base"], captured["variant"]
    # 蜜＝羅時 D3 等同 D1、D4 被 D2 涵蓋：兩組的入選都只會增加，且包含原本全部入選
    for gid in ("g1", "g2"):
        assert {h["code"] for h in base[gid]["hits"]} <= {h["code"] for h in variant[gid]["hits"]}
        assert variant[gid]["passed"]["D3"] == variant[gid]["passed"]["D1"]
    out = capsys.readouterr().out
    assert "## 參數試算 2026-10-30" in out and "### 第 1 組（60 分／日／週）" in out and "### 觀察清單" in out
