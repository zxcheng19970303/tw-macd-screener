import numpy as np
import pandas as pd
import pytest

from screener.indicators import ema, macd, price_series

TOL = 1e-9


def _ema_loop(values, n):
    alpha = 2 / (n + 1)
    out = [values[0]]
    for p in values[1:]:
        out.append(alpha * p + (1 - alpha) * out[-1])
    return out


def test_ema_hand_calculated():
    # n=3 → alpha=0.5，以第一筆為起始值
    got = ema(pd.Series([10.0, 11, 12, 13, 12]), 3)
    assert np.allclose(got, [10, 10.5, 11.25, 12.125, 12.0625], atol=TOL, rtol=0)


@pytest.mark.parametrize("n", [3, 10, 20, 52])
def test_ema_matches_recursive_formula(n):
    values = list(np.random.default_rng(n).normal(100, 5, 300))
    assert np.allclose(ema(pd.Series(values), n), _ema_loop(values, n), atol=TOL, rtol=0)


def test_macd_hand_calculated():
    # fast=1（EMA 即價格）、slow=3、signal=3（alpha 皆 0.5）
    m = macd(pd.Series([1.0, 2, 3, 4]), 1, 3, 3)
    assert np.allclose(m["dif"], [0, 0.5, 0.75, 0.875], atol=TOL, rtol=0)
    assert np.allclose(m["macd"], [0, 0.25, 0.5, 0.6875], atol=TOL, rtol=0)
    assert np.allclose(m["dm"], [0, 0.25, 0.25, 0.1875], atol=TOL, rtol=0)


def test_macd_matches_recursive_formula():
    values = list(np.random.default_rng(7).normal(50, 2, 400).cumsum())
    m = macd(pd.Series(values), 10, 20, 10)
    dif = np.array(_ema_loop(values, 10)) - np.array(_ema_loop(values, 20))
    sig = np.array(_ema_loop(list(dif), 10))
    assert np.allclose(m["dif"], dif, atol=TOL, rtol=0)
    assert np.allclose(m["macd"], sig, atol=TOL, rtol=0)
    assert np.allclose(m["dm"], dif - sig, atol=TOL, rtol=0)


def test_price_series():
    df = pd.DataFrame({"High": [12.0], "Low": [8.0], "Close": [11.0]})
    assert price_series(df, "close").iloc[0] == 11.0
    assert price_series(df, "hlcc4").iloc[0] == pytest.approx((12 + 8 + 2 * 11) / 4)
    with pytest.raises(ValueError):
        price_series(df, "open")
