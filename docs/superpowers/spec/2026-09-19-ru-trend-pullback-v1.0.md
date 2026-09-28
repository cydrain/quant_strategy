# 橡胶 RU 日线趋势回踩策略 v1.0 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按设计文档实现 `joinquant/04-RubberTrendPullback-v1.0.py`：单品种 ru 日线趋势回踩策略（聚宽分钟回测），并在聚宽平台完成 2018-01-01 至今的回测验证。

**Architecture:** 单文件函数式策略（对齐 `joinquant/03-守正出奇4.0.py` 风格）：纯信号函数（MA/ATR、入场/出场判定、止损推进、手数公式）与平台依赖层（取数、下单、移仓）分离。纯函数用本地 pytest 验证；平台行为（下单成交、停板、移仓）通过在聚宽逐区间回测验证。分钟单入口 `run_daily(every_bar)`：一致性校验 → 止损 → 14:45 信号流程（信号反转 → 移仓 → 加仓 → 开仓）。

**Tech Stack:** 聚宽（JoinQuant）回测平台，Python3；本地测试用 pytest + pandas + numpy（venv）。

**规格来源:** `docs/superpowers/specs/2026-09-17-ru-trend-pullback-design.md`（已获批）

## Global Constraints

- 策略文件固定为 `joinquant/04-RubberTrendPullback-v1.0.py`，单文件；中文注释、英文日志（`[RU]` 前缀）、`initialize` 用 try/except 包裹——对齐 `joinquant/03-守正出奇4.0.py`。
- 参数集中 `PARAMS` 常量（15 项，默认值必须与规格参数总表逐字一致），`initialize` 时 `g.params = dict(PARAMS)` 并打印参数快照。
- 只允许 pandas/numpy，不引入 talib。不额外引入新依赖。
- 平台：聚宽，期货账户，初始资金 50 万，回测频率必须选"分钟"，区间 2018-01-01 至今。
- 参数纪律：只做逻辑驱动的参数调整；禁止绩效驱动的网格寻优；敏感性粗扫按规格 §5 清单逐个重跑。
- 本地测试命令统一为 `.venv/bin/python -m pytest tests/ -v`。
- git 提交由用户自行执行：计划中每个 Commit 步骤只给出建议的提交信息，执行者不得代提交。
- 日志字段要求（规格 §5）：开仓/加仓/止损/信号出场/移仓日志必须含 合约、价格、手数、ATR_lock、止损线、触发原因。

**File Structure**

- `joinquant/04-RubberTrendPullback-v1.0.py` — 策略单文件（新建）：参数常量、纯信号函数、平台取数封装、订单封装、分钟流程、14:45 流程、移仓。
- `tests/conftest.py` — 本地测试装载器（新建）：stub `jqdata` 后按路径载入策略模块，暴露 `ru` fixture。
- `tests/test_ru_signals.py` — 纯函数单元测试（新建）。
- `.gitignore` — 新建：忽略 `.venv/`、`__pycache__/`、`.pytest_cache/`。
- `docs/superpowers/plans/2026-09-19-ru-trend-pullback-v1.0-backtest-record.md` — 回测验收记录（Task 7 新建）。

**任务一览（依赖顺序）：** Task 1 环境与骨架 → Task 2/3/4 纯函数（可并行）→ Task 5 策略主干（依赖 1-4）→ Task 6 移仓（依赖 5）→ Task 7 全区间验收（依赖 5/6）。

---

### Task 1: 本地测试环境 + 策略骨架 + 参数常量

**Files:**
- Create: `joinquant/04-RubberTrendPullback-v1.0.py`
- Create: `tests/conftest.py`
- Create: `tests/test_ru_signals.py`
- Create: `.gitignore`

**Interfaces:**
- Consumes: 无
- Produces: 模块级 `PARAMS: dict`（15 项默认值）；pytest fixture `ru`（策略模块对象，供 Task 2-4 使用）

- [ ] **Step 1: 创建本地 venv 并安装依赖**

```bash
cd /home/caiyd/work/others/quant_strategy
~/miniconda3/bin/python -m venv .venv
.venv/bin/pip install pytest pandas numpy
```

Expected: 最后一行 `Successfully installed ...`；`.venv/bin/python -V` 输出 `Python 3.x`。
（若 pip 因网络失败：改用 `~/miniconda3/bin/pip install pytest pandas numpy`，后续命令中的 `.venv/bin/python` 全部替换为 `~/miniconda3/bin/python`）

- [ ] **Step 2: 写 `.gitignore`**

```gitignore
.venv/
__pycache__/
.pytest_cache/
```

- [ ] **Step 3: 写 `tests/conftest.py`（装载器）**

```python
import importlib.util
import pathlib
import sys
import types

import pytest

STRATEGY_PATH = pathlib.Path(__file__).resolve().parents[1] / 'joinquant' / '04-RubberTrendPullback-v1.0.py'


@pytest.fixture(scope='session')
def ru():
    """载入聚宽策略模块（stub jqdata；纯函数不依赖平台对象）"""
    sys.modules.setdefault('jqdata', types.ModuleType('jqdata'))
    spec = importlib.util.spec_from_file_location('ru_strategy', STRATEGY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 4: 写 `tests/test_ru_signals.py`（仅参数测试）**

```python
def test_params_defaults(ru):
    assert ru.PARAMS == {
        'ma_period': 20,
        'ma_slope_days': 3,
        'pullback_window': 3,
        'entry_max_dist_atr': 1.0,
        'pullback_max_break_atr': 0,
        'cooldown_days': 0,
        'atr_n': 20,
        'stop_loss_dist_atr': 2.0,
        'signal_exit_dist_atr': 0.5,
        'risk_pct': 0.02,
        'max_adds': 2,
        'add_min_dist_atr': 0.5,
        'signal_time': '14:45',
        'multiplier': 10,
        'margin_rate': 0.12,
    }
```

- [ ] **Step 5: 运行测试，确认失败**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: FAIL/ERROR — `FileNotFoundError`（策略文件尚不存在）

- [ ] **Step 6: 创建策略文件骨架**

```python
# ============================================================
# 橡胶 RU 日线趋势回踩策略 v1.0（聚宽回测）
# 设计文档: docs/superpowers/specs/2026-09-17-ru-trend-pullback-design.md
#   - 信号: 日线 MA20 方向 + 回踩窗口站回，每交易日 14:45 判定执行
#   - 出场: 信号反转(MA20 失守缓冲) / 初始止损 / 移动止损（每分钟，含夜盘）
#   - 加仓: 金字塔最多 2 次
#   - 移仓: 主力切换连续 2 日确认 + 先开新后平旧 + 止损价差平移
# 回测设置: 期货账户 / 50 万 / 分钟频率 / 2018-01-01 起
# 本地单元测试: tests/test_ru_signals.py；运行: .venv/bin/python -m pytest tests/ -v
# ============================================================
from jqdata import *
import datetime
import pandas as pd

# ==================== 参数（集中，initialize 时拷入 g.params） ====================
PARAMS = {
    'ma_period': 20,              # MA20 周期（方向/回踩/出场共用）
    'ma_slope_days': 3,           # MA20 上行判定跨度（交易日）
    'pullback_window': 3,         # 回踩窗口交易日数，1 = 同根 K 线规则
    'entry_max_dist_atr': 1.0,    # 站回日距 MA20 的距离上限（ATR 倍数），0 = 不限
    'pullback_max_break_atr': 0,  # 回踩窗口内单日跌破深度上限（ATR 倍数），0 = 不限
    'cooldown_days': 0,           # 全平后禁止开新仓的交易日数
    'atr_n': 20,                  # ATR 周期
    'stop_loss_dist_atr': 2.0,    # 止损距离（ATR 倍数），初始与移动共用
    'signal_exit_dist_atr': 0.5,  # 信号反转失守缓冲（ATR 倍数）
    'risk_pct': 0.02,             # 单笔风险预算
    'max_adds': 2,                # 最大加仓次数
    'add_min_dist_atr': 0.5,      # 加仓趋势推进：MA20 较上笔入场的最小推进（ATR 倍数），0 = 不检查
    'signal_time': '14:45',       # 信号判定时刻
    'multiplier': 10,             # RU 合约乘数
    'margin_rate': 0.12,          # 保证金率（回测设定值）
}
```

- [ ] **Step 7: 再跑测试，确认通过**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: `1 passed`

- [ ] **Step 8: Commit（建议，由用户执行）**

```bash
git add .gitignore joinquant/04-RubberTrendPullback-v1.0.py tests/conftest.py tests/test_ru_signals.py
git commit -m "feat(ru): 策略骨架与参数常量 + 本地测试装载器"
```

---

### Task 2: 纯函数 — MA/ATR 计算与方向判定

**Files:**
- Modify: `joinquant/04-RubberTrendPullback-v1.0.py`（文件末尾追加）
- Modify: `tests/test_ru_signals.py`（追加测试）

**Interfaces:**
- Consumes: 无
- Produces:
  - `calc_ma_atr(highs, lows, closes, ma_period, atr_n) -> (numpy.ndarray, numpy.ndarray)`（与输入等长的 ma/atr 序列）
  - `check_direction(ma_series, ma_slope_days) -> int`（1 多头 / -1 空头 / 0 无）

- [ ] **Step 1: 在测试文件头部加 import**

`tests/test_ru_signals.py` 顶部追加：

```python
import math

import numpy as np
import pandas as pd
import pytest
```

- [ ] **Step 2: 写失败测试（追加到测试文件末尾）**

```python
def test_calc_ma_atr(ru):
    highs = [10, 11, 12]
    lows = [9, 9.5, 10.5]
    closes = [9.5, 10, 11]
    ma, atr = ru.calc_ma_atr(highs, lows, closes, ma_period=2, atr_n=2)
    assert math.isnan(ma[0])
    assert ma[1] == pytest.approx(9.75)
    assert ma[2] == pytest.approx(10.5)
    # TR = max(H-L, |H-Cprev|, |L-Cprev|)：t1=max(1.5,1.5,0)=1.5；t2=max(1.5,2,0.5)=2 → ATR=(1.5+2)/2
    assert atr[2] == pytest.approx(1.75)


def test_check_direction(ru):
    assert ru.check_direction(np.array([1.0, 1.1, 1.2, 1.3]), 3) == 1
    assert ru.check_direction(np.array([1.3, 1.2, 1.1, 1.0]), 3) == -1
    assert ru.check_direction(np.array([1.0, 1.0, 1.0, 1.0]), 3) == 0
    assert ru.check_direction(np.array([float('nan')] * 4), 3) == 0
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `.venv/bin/python -m pytest tests/test_ru_signals.py::test_calc_ma_atr -v`
Expected: FAIL — `AttributeError: module 'ru_strategy' has no attribute 'calc_ma_atr'`

- [ ] **Step 4: 实现（追加到策略文件末尾）**

```python
# ==================== 纯函数（不依赖聚宽环境，可本地测试） ====================

def calc_ma_atr(highs, lows, closes, ma_period, atr_n):
    """输入完整日线序列（末位为准当日），返回等长的 (ma_series, atr_series)"""
    closes = pd.Series(closes, dtype=float)
    highs = pd.Series(highs, dtype=float)
    lows = pd.Series(lows, dtype=float)
    ma_series = closes.rolling(ma_period).mean().values
    prev_close = closes.shift(1)
    tr = pd.concat([highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()], axis=1).max(axis=1)
    atr_series = tr.rolling(atr_n).mean().values
    return ma_series, atr_series


def check_direction(ma_series, ma_slope_days):
    """方向：1 多头（MA 上行）/ -1 空头（MA 下行）/ 0 无（含数据不足与 NaN）"""
    if len(ma_series) <= ma_slope_days:
        return 0
    now, past = ma_series[-1], ma_series[-1 - ma_slope_days]
    if now > past:
        return 1
    if now < past:
        return -1
    return 0
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: `3 passed`

- [ ] **Step 6: Commit（建议，由用户执行）**

```bash
git add joinquant/04-RubberTrendPullback-v1.0.py tests/test_ru_signals.py
git commit -m "feat(ru): MA/ATR 计算与方向判定纯函数"
```

---

### Task 3: 纯函数 — 入场条件与信号反转出场

**Files:**
- Modify: `joinquant/04-RubberTrendPullback-v1.0.py`（追加）
- Modify: `tests/test_ru_signals.py`（追加）

**Interfaces:**
- Consumes: `calc_ma_atr` 产出的 `ma_series`（numpy 数组）
- Produces:
  - `check_entry(direction, highs, lows, ma_series, price, atr, pullback_window, entry_max_dist_atr, pullback_max_break_atr) -> bool`
  - `check_signal_exit(direction, price, ma_last, atr, signal_exit_dist_atr) -> bool`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_check_entry_same_bar_rule(ru):
    # N=1 且 d_max=0 → 精确还原同根 K 线规则：low(D)<=MA20(D) 且 P1445>=MA20(D)
    ma = np.array([9.0, 10.0, 10.0])
    highs = [9.5, 10.5, 10.4]
    lows = [8.9, 9.8, 9.5]
    assert ru.check_entry(1, highs, lows, ma, 10.2, 1.0, 1, 0, 0) is True
    assert ru.check_entry(1, highs, lows, ma, 9.9, 1.0, 1, 0, 0) is False   # 未站回
    assert ru.check_entry(1, [9.5, 10.5, 10.8], [9.8, 9.9, 10.3], ma, 10.9, 1.0, 1, 0, 0) is False  # 未回踩


def test_check_entry_window_and_filters(ru):
    ma = np.array([9.0, 10.0, 10.0, 10.0])
    highs = [9.5, 10.5, 10.5, 10.6]
    lows = [9.8, 9.4, 10.05, 10.1]   # 仅 D-2 日回踩（9.4 <= 10.0），D-1/D 未触及
    assert ru.check_entry(1, highs, lows, ma, 10.3, 1.0, 3, 0, 0) is True
    assert ru.check_entry(1, highs, lows, ma, 10.3, 1.0, 1, 0, 0) is False       # 窗口=1 时 D-2 不算
    assert ru.check_entry(1, highs, lows, ma, 11.01, 1.0, 3, 1.0, 0) is False    # 距离上限 1.0×ATR
    assert ru.check_entry(1, highs, lows, ma, 10.9, 1.0, 3, 1.0, 0) is True
    assert ru.check_entry(1, highs, lows, ma, 10.3, 1.0, 3, 0, 0.5) is False     # 深失守 0.6 > 0.5×ATR
    assert ru.check_entry(1, highs, lows, ma, 10.3, 1.0, 3, 0, 0) is True        # 0 = 不限


def test_check_entry_short_mirror(ru):
    ma = np.array([11.0, 10.0, 10.0, 10.0])
    highs = [10.5, 10.6, 10.1, 10.05]    # D-2 日触及（10.6 >= 10.0）
    lows = [9.9, 9.5, 9.7, 9.8]
    assert ru.check_entry(-1, highs, lows, ma, 9.8, 1.0, 3, 0, 0) is True
    assert ru.check_entry(-1, highs, lows, ma, 10.1, 1.0, 3, 0, 0) is False      # 未跌破
    assert ru.check_entry(-1, highs, lows, ma, 8.7, 1.0, 3, 1.0, 0) is False     # 距离 1.3 > 1.0×ATR
    assert ru.check_entry(-1, highs, lows, ma, 8.7, 1.0, 3, 0, 0.5) is False     # 深反弹 0.6 > 0.5×ATR


def test_check_signal_exit(ru):
    assert ru.check_signal_exit(1, 9.4, 10.0, 1.0, 0.5) is True     # 9.4 < 10 - 0.5
    assert ru.check_signal_exit(1, 9.6, 10.0, 1.0, 0.5) is False    # 仍在缓冲内
    assert ru.check_signal_exit(-1, 10.6, 10.0, 1.0, 0.5) is True
    assert ru.check_signal_exit(-1, 10.4, 10.0, 1.0, 0.5) is False
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `.venv/bin/python -m pytest tests/test_ru_signals.py::test_check_entry_same_bar_rule -v`
Expected: FAIL — `AttributeError: ... has no attribute 'check_entry'`

- [ ] **Step 3: 实现（追加到策略文件"纯函数"段之后）**

```python
def check_entry(direction, highs, lows, ma_series, price, atr, pullback_window,
                entry_max_dist_atr, pullback_max_break_atr):
    """入场条件 2-5（方向已确认）：回踩窗口 + 站回 + 距离上限 + 深失守过滤
    highs/lows/ma_series 为完整序列（末位为准当日 D）；0 = 该过滤项不限"""
    win_h = highs[-pullback_window:]
    win_l = lows[-pullback_window:]
    win_ma = ma_series[-pullback_window:]
    if direction == 1:
        if entry_max_dist_atr > 0 and price - win_ma[-1] > entry_max_dist_atr * atr:
            return False
        if price < win_ma[-1]:
            return False
        if not any(win_l[i] <= win_ma[i] for i in range(len(win_l))):
            return False
        if pullback_max_break_atr > 0 and any(win_ma[i] - win_l[i] > pullback_max_break_atr * atr
                                              for i in range(len(win_l))):
            return False
    else:
        if entry_max_dist_atr > 0 and win_ma[-1] - price > entry_max_dist_atr * atr:
            return False
        if price > win_ma[-1]:
            return False
        if not any(win_h[i] >= win_ma[i] for i in range(len(win_h))):
            return False
        if pullback_max_break_atr > 0 and any(win_h[i] - win_ma[i] > pullback_max_break_atr * atr
                                              for i in range(len(win_h))):
            return False
    return True


def check_signal_exit(direction, price, ma_last, atr, signal_exit_dist_atr):
    """信号反转：多头 P1445 < MA20 − k×ATR（空头镜像）"""
    if direction == 1:
        return price < ma_last - signal_exit_dist_atr * atr
    return price > ma_last + signal_exit_dist_atr * atr
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: `7 passed`

- [ ] **Step 5: Commit（建议，由用户执行）**

```bash
git add joinquant/04-RubberTrendPullback-v1.0.py tests/test_ru_signals.py
git commit -m "feat(ru): 入场条件与信号反转纯函数"
```

---

### Task 4: 纯函数 — 止损推进 / 手数 / 保证金 / 准当日合成

**Files:**
- Modify: `joinquant/04-RubberTrendPullback-v1.0.py`（追加）
- Modify: `tests/test_ru_signals.py`（追加）

**Interfaces:**
- Consumes: 无
- Produces:
  - `update_stop(side, extreme, stop_line, atr_lock, bar_high, bar_low, stop_loss_dist_atr) -> (extreme, stop_line)`
  - `check_stop_hit(side, stop_line, bar_high, bar_low) -> bool`
  - `calc_lots(equity, risk_pct, stop_loss_dist_atr, atr_lock, multiplier) -> int`
  - `fit_lots_to_margin(lots, price, multiplier, margin_rate, available_cash) -> int`
  - `dir_to_side(direction) -> str`、`side_to_dir(side) -> int`
  - `quasi_daily_from_bars(bar_dates, bar_highs, bar_lows, bar_closes, session_start) -> dict|None`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_stop_logic(ru):
    # 多头：入场 100，ATR 5，k=2 → 初始止损 90
    extreme, stop = ru.update_stop('long', 100.0, 90.0, 5.0, 110.0, 99.0, 2.0)
    assert (extreme, stop) == (110.0, 100.0)      # 极值 110 → 止损上移到 100
    extreme, stop = ru.update_stop('long', extreme, stop, 5.0, 105.0, 101.0, 2.0)
    assert (extreme, stop) == (110.0, 100.0)      # 只上移，不回落
    assert ru.check_stop_hit('long', 100.0, 101.0, 99.9) is True
    assert ru.check_stop_hit('long', 100.0, 101.0, 100.1) is False
    # 空头镜像
    extreme, stop = ru.update_stop('short', 100.0, 110.0, 5.0, 101.0, 92.0, 2.0)
    assert (extreme, stop) == (92.0, 102.0)
    assert ru.check_stop_hit('short', 102.0, 102.1, 101.0) is True


def test_position_sizing(ru):
    # 50 万，R=2%，k=2，ATR=180，乘数 10 → 10000/(3600)=2.77 → 2 手
    assert ru.calc_lots(500000, 0.02, 2.0, 180.0, 10) == 2
    # 极端高波动 → 最低 1 手
    assert ru.calc_lots(500000, 0.02, 2.0, 2000.0, 10) == 1
    # 保证金约束：每手 13500×10×0.12=16200；可用 50000 → 3 手；可用 10000 → 0 手
    assert ru.fit_lots_to_margin(5, 13500.0, 10, 0.12, 50000.0) == 3
    assert ru.fit_lots_to_margin(5, 13500.0, 10, 0.12, 10000.0) == 0


def test_side_helpers(ru):
    assert ru.dir_to_side(1) == 'long'
    assert ru.dir_to_side(-1) == 'short'
    assert ru.side_to_dir('long') == 1
    assert ru.side_to_dir('short') == -1


def test_quasi_daily_from_bars(ru):
    # 昨日日盘 bar（排除）+ 昨夜 21:00 起 bar（纳入）
    s0 = pd.Timestamp('2018-01-08 20:00:00')
    dates = [pd.Timestamp('2018-01-08 14:00:00'),
             pd.Timestamp('2018-01-08 21:00:00'),
             pd.Timestamp('2018-01-09 09:00:00')]
    q = ru.quasi_daily_from_bars(dates, [100.0, 105.0, 103.0], [98.0, 99.0, 101.0],
                                 [99.0, 104.0, 102.0], s0)
    assert q == {'high': 105.0, 'low': 99.0, 'close': 102.0}
    # session 内无 bar → None
    assert ru.quasi_daily_from_bars([pd.Timestamp('2018-01-08 14:00:00')], [100.0], [98.0],
                                    [99.0], s0) is None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `.venv/bin/python -m pytest tests/test_ru_signals.py::test_stop_logic -v`
Expected: FAIL — `AttributeError: ... has no attribute 'update_stop'`

- [ ] **Step 3: 实现（追加到策略文件"纯函数"段之后）**

```python
def update_stop(side, extreme, stop_line, atr_lock, bar_high, bar_low, stop_loss_dist_atr):
    """移动止损推进（吊灯式，只朝有利方向）：返回 (extreme, stop_line)"""
    if side == 'long':
        extreme = max(extreme, bar_high)
        return extreme, max(stop_line, extreme - stop_loss_dist_atr * atr_lock)
    extreme = min(extreme, bar_low)
    return extreme, min(stop_line, extreme + stop_loss_dist_atr * atr_lock)


def check_stop_hit(side, stop_line, bar_high, bar_low):
    """分钟 bar 是否触达止损线"""
    if side == 'long':
        return bar_low <= stop_line
    return bar_high >= stop_line


def calc_lots(equity, risk_pct, stop_loss_dist_atr, atr_lock, multiplier):
    """风险预算法手数：lots = max(1, floor(权益×R / (k×ATR×乘数)))"""
    denom = stop_loss_dist_atr * atr_lock * multiplier
    if denom <= 0:
        return 1
    return max(1, int(equity * risk_pct / denom))


def fit_lots_to_margin(lots, price, multiplier, margin_rate, available_cash):
    """保证金约束：按可用资金逐手减仓；1 手都开不出返回 0"""
    margin_per_lot = price * multiplier * margin_rate
    if margin_per_lot <= 0:
        return lots
    return min(lots, int(available_cash / margin_per_lot))


def dir_to_side(direction):
    return 'long' if direction == 1 else 'short'


def side_to_dir(side):
    return 1 if side == 'long' else -1


def quasi_daily_from_bars(bar_dates, bar_highs, bar_lows, bar_closes, session_start):
    """从分钟 bar 过滤出当前 session（前一交易日 20:00 起，含夜盘），合成准当日 high/low/close"""
    hi = lo = close = None
    for ts, h, l, c in zip(bar_dates, bar_highs, bar_lows, bar_closes):
        if ts >= session_start:
            hi = h if hi is None else max(hi, h)
            lo = l if lo is None else min(lo, l)
            close = c
    if hi is None:
        return None
    return {'high': hi, 'low': lo, 'close': close}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: `11 passed`

- [ ] **Step 5: Commit（建议，由用户执行）**

```bash
git add joinquant/04-RubberTrendPullback-v1.0.py tests/test_ru_signals.py
git commit -m "feat(ru): 止损推进/手数/保证金/准当日合成纯函数"
```

---

### Task 5: 策略主干（订单封装 / 止损 / 信号流程 / 单入口，主力固定）

**Files:**
- Modify: `joinquant/04-RubberTrendPullback-v1.0.py`（追加平台层与主干）
- Test: `tests/`（回归，无新增用例）

**Interfaces:**
- Consumes: Task 1-4 的全部纯函数
- Produces:
  - `current_minute_bar(contract, context) -> dict|None`
  - `build_quasi_daily(contract, context) -> dict|None`
  - `compute_signal_data(contract, context) -> dict|None`（键：contract/ma/atr/price/highs/lows/direction）
  - `has_open_order(contract) -> bool`、`warn_once(key, msg)`
  - `open_leg(context, direction, lots, atr_lock, reason) -> bool`（reason ∈ {'entry','add'}）
  - `close_leg(context, leg, reason) -> bool`、`close_all_legs(context, reason)`
  - `check_state_consistency(context)`
  - `minute_risk_checks(context, bar)`
  - `entry_ok(d, p) -> bool`、`try_add(context, d)`、`try_open(context, d)`、`cooldown_ok(context) -> bool`
  - `signal_routine(context)`、`risk_and_signal_routine(context)`
  - 状态：`g.params / g.current_contract / g.legs / g.adds / g.last_signal_date / g.last_flat_date / g.warned`

- [ ] **Step 1: 写平台取数封装（追加到策略文件末尾）**

```python
# ==================== 平台取数 ====================

def current_minute_bar(contract, context):
    """当前分钟 bar（合约不在交易时段、数据过期时返回 None），用于止损判定"""
    if contract is None:
        return None
    bars = get_bars(contract, 1, '1m', ['date', 'high', 'low', 'close'], include_now=True)
    if bars is None or len(bars) == 0:
        return None
    row = bars.iloc[-1]
    if abs((pd.Timestamp(context.current_dt) - pd.Timestamp(row['date'])).total_seconds()) > 60:
        return None
    return {'high': row['high'], 'low': row['low'], 'close': row['close']}


def build_quasi_daily(contract, context):
    """准当日日线：当前 session（前一交易日 20:00 起，含夜盘）分钟 bar 合成"""
    bars = get_bars(contract, 400, '1m', ['date', 'high', 'low', 'close'], include_now=True)
    if bars is None or len(bars) == 0:
        return None
    session_start = pd.Timestamp(datetime.datetime.combine(context.previous_date, datetime.time(20, 0)))
    return quasi_daily_from_bars([pd.Timestamp(t) for t in bars['date']],
                                 bars['high'], bars['low'], bars['close'], session_start)


def compute_signal_data(contract, context):
    """日线（前 29 根完整 + 准当日）→ MA/ATR 序列、方向、现价；数据不可用返回 None
    说明：attribute_history(unit='1d') 取 30 根，仅用后 29 根完整日线，末位拼接准当日"""
    p = g.params
    if contract is None:
        return None
    hist = attribute_history(contract, 30, '1d', ['high', 'low', 'close'])
    q = build_quasi_daily(contract, context)
    if hist is None or len(hist) == 0 or q is None:
        return None
    hist = hist.iloc[-29:]
    highs = list(hist['high'].values) + [q['high']]
    lows = list(hist['low'].values) + [q['low']]
    closes = list(hist['close'].values) + [q['close']]
    ma_series, atr_series = calc_ma_atr(highs, lows, closes, p['ma_period'], p['atr_n'])
    return {'contract': contract, 'ma': ma_series, 'atr': atr_series[-1], 'price': q['close'],
            'highs': highs, 'lows': lows,
            'direction': check_direction(ma_series, p['ma_slope_days'])}
```

- [ ] **Step 2: 写订单封装与状态一致性校验（继续追加）**

```python
# ==================== 订单封装 ====================

def has_open_order(contract):
    """该合约是否已有未成交委托（停板等待，不重复下单）"""
    for o in get_open_orders().values():
        if o.security == contract:
            return True
    return False


def warn_once(key, msg):
    """按 key 去重告警：仅状态变化时打印，避免每分钟刷屏"""
    if g.warned.get(key) != msg:
        log.warn(msg)
        g.warned[key] = msg


def open_leg(context, direction, lots, atr_lock, reason):
    """市价开一笔 leg；成交后登记 g.legs，未成交（停板）告警不重试"""
    p = g.params
    c = g.current_contract
    if has_open_order(c):
        warn_once('open_' + c, f"[RU] WARN open pending exists {c}")
        return False
    price = get_current_data()[c].last_price
    lots = fit_lots_to_margin(lots, price, p['multiplier'], p['margin_rate'],
                              context.subportfolios[0].available_cash)
    if lots < 1:
        log.warn(f"[RU] WARN open skip margin insufficient {c} price={price:.1f}")
        return False
    side = dir_to_side(direction)
    o = order(c, lots, side=side)
    if o is None or o.filled < lots:
        warn_once('open_' + c, f"[RU] WARN open unfilled (limit board?) {c} lots={lots}")
        return False
    stop_line = o.price - p['stop_loss_dist_atr'] * atr_lock if direction == 1 \
        else o.price + p['stop_loss_dist_atr'] * atr_lock
    g.legs.append({'contract': c, 'side': side, 'lots': lots, 'entry_price': o.price,
                   'entry_date': context.current_dt.date(), 'atr_lock': atr_lock,
                   'extreme': o.price, 'stop_line': stop_line})
    log.info(f"[RU] {reason.upper()} {side} {c} {lots}@{o.price:.1f} atr_lock={atr_lock:.1f} "
             f"stop={stop_line:.1f} equity={context.portfolio.total_value:.0f}")
    g.warned.pop('open_' + c, None)
    if reason == 'add':
        g.adds += 1
    return True


def close_leg(context, leg, reason):
    """市价平掉单笔 leg；成交后移除并维护全平状态，未成交（停板）告警等待"""
    c = leg['contract']
    if has_open_order(c):
        warn_once('close_' + c, f"[RU] WARN close pending exists {c}")
        return False
    amt = -leg['lots']                        # 平台实测：order(数量>0, side='short') 是卖出开空，平仓（多/空）统一用负数量
    o = order(c, amt, side=leg['side'])
    if o is None or o.filled < leg['lots']:
        warn_once('close_' + c, f"[RU] WARN close unfilled (limit board?) {c} {leg['side']} "
                                f"lots={leg['lots']} reason={reason}")
        return False
    pnl = (o.price - leg['entry_price']) * leg['lots'] * g.params['multiplier'] * side_to_dir(leg['side'])
    log.info(f"[RU] CLOSE {leg['side']} {c} {leg['lots']}@{o.price:.1f} pnl={pnl:.0f} reason={reason} "
             f"entry={leg['entry_price']:.1f} stop={leg['stop_line']:.1f} atr_lock={leg['atr_lock']:.1f}")
    g.legs.remove(leg)
    g.warned.pop('close_' + c, None)
    if not g.legs:
        g.adds = 0
        g.last_flat_date = context.current_dt.date()
        log.info("[RU] FLAT")
    return True


def close_all_legs(context, reason):
    """全部平仓（信号反转）；未成交的 leg 留待下一 bar 继续"""
    for leg in list(g.legs):
        close_leg(context, leg, reason)


def check_state_consistency(context):
    """g.legs 与账户实际持仓每 bar 比对；不一致→告警并按实际重建（设计文档 §4）"""
    sp = context.subportfolios[0]
    actual = {}
    for side, pos_map in (('long', sp.long_positions), ('short', sp.short_positions)):
        for c, pos in pos_map.items():
            if pos.total_amount > 0:
                actual[(c, side)] = actual.get((c, side), 0) + pos.total_amount
    tracked = {}
    for leg in g.legs:
        key = (leg['contract'], leg['side'])
        tracked[key] = tracked.get(key, 0) + leg['lots']
    if actual == tracked:
        return
    log.warn(f"[RU] STATE_MISMATCH tracked={tracked} actual={actual} -> rebuild from actual")
    g.legs = []
    for (c, side), amt in actual.items():
        d = compute_signal_data(c, context)
        atr = d['atr'] if d is not None else 0.0
        price = get_current_data()[c].last_price
        stop_line = price - g.params['stop_loss_dist_atr'] * atr if side == 'long' \
            else price + g.params['stop_loss_dist_atr'] * atr
        g.legs.append({'contract': c, 'side': side, 'lots': amt, 'entry_price': price,
                       'entry_date': context.current_dt.date(), 'atr_lock': atr,
                       'extreme': price, 'stop_line': stop_line})
    if not g.legs:
        g.adds = 0
        g.last_flat_date = context.current_dt.date()
```

- [ ] **Step 3: 写分钟止损与 14:45 信号流程（继续追加）**

```python
# ==================== 分钟止损 ====================

def minute_risk_checks(context, bar):
    """每笔移动止损推进与触发；停板未成交由 close_leg 告警等待"""
    if bar is None or not g.legs:
        return
    p = g.params
    for leg in list(g.legs):
        leg['extreme'], leg['stop_line'] = update_stop(
            leg['side'], leg['extreme'], leg['stop_line'], leg['atr_lock'],
            bar['high'], bar['low'], p['stop_loss_dist_atr'])
        if check_stop_hit(leg['side'], leg['stop_line'], bar['high'], bar['low']):
            log.info(f"[RU] STOP_HIT {leg['contract']} {leg['side']} stop={leg['stop_line']:.1f} "
                     f"bar_low={bar['low']:.1f} bar_high={bar['high']:.1f}")
            close_leg(context, leg, 'stop')


# ==================== 14:45 信号流程 ====================

def entry_ok(d, p):
    """入场条件 2-5 的统一入口（方向非 0 + 回踩/站回/距离/深失守）"""
    if d is None or d['direction'] == 0:
        return False
    return check_entry(d['direction'], d['highs'], d['lows'], d['ma'], d['price'], d['atr'],
                       p['pullback_window'], p['entry_max_dist_atr'], p['pullback_max_break_atr'])


def cooldown_ok(context):
    """冷却期：全平后 cooldown_days 个交易日内禁止开新仓（0 = 关闭）"""
    p = g.params
    if g.last_flat_date is None or p['cooldown_days'] <= 0:
        return True
    days = get_trade_days(start_date=g.last_flat_date, end_date=context.current_dt.date())
    elapsed = len(days) - 1
    return not (1 <= elapsed <= p['cooldown_days'])


def try_add(context, d):
    """加仓：入场形态 + 趋势推进确认（MA20 较上笔入场推进）+ 次数限制（设计文档 2.5）"""
    p = g.params
    if not g.legs or g.migrating is not None or g.adds >= p['max_adds']:
        return
    side = g.legs[0]['side']
    if d is None or d['direction'] == 0 or dir_to_side(d['direction']) != side:
        return
    if not entry_ok(d, p):
        return
    last = g.legs[-1]
    gap = d['price'] - last['entry_price'] if side == 'long' else last['entry_price'] - d['price']
    if gap <= 0:                                      # 固定规则：金字塔不允许向下加仓
        return
    if p['add_min_dist_atr'] > 0:                     # MA20 较上笔入场推进 ≥ 阈值（原"价差"条件的设计本意）
        ma_entry = last.get('ma_at_entry')
        if ma_entry is None:                          # 状态缺失（如重建补记），保守跳过
            return
        ma_advance = d['ma'][-1] - ma_entry if side == 'long' else ma_entry - d['ma'][-1]
        if ma_advance < p['add_min_dist_atr'] * d['atr']:
            return
    lots = calc_lots(context.portfolio.total_value, p['risk_pct'], p['stop_loss_dist_atr'],
                     d['atr'], p['multiplier'])
    open_leg(context, d['direction'], lots, d['atr'], 'add', d['ma'][-1])


def try_open(context, d):
    """开仓：空仓 + 冷却期已过 + 入场信号（设计文档 2.3/2.6）"""
    p = g.params
    if g.legs:
        return
    if not cooldown_ok(context):
        return
    if not entry_ok(d, p):
        return
    lots = calc_lots(context.portfolio.total_value, p['risk_pct'], p['stop_loss_dist_atr'],
                     d['atr'], p['multiplier'])
    open_leg(context, d['direction'], lots, d['atr'], 'entry')


def signal_routine(context):
    """14:45 信号流程：信号反转 → 加仓 → 开仓"""
    p = g.params
    if g.current_contract is None:                       # 首个 14:45 解析主力
        g.current_contract = get_dominant_future('RU')
        log.info(f"[RU] dominant init {g.current_contract}")
    d = compute_signal_data(g.current_contract, context)
    if d is not None:
        log.info(f"[RU] DAILY {g.current_contract} price={d['price']:.1f} ma={d['ma'][-1]:.1f} "
                 f"atr={d['atr']:.1f} dir={d['direction']} hi={d['highs'][-1]:.1f} "
                 f"lo={d['lows'][-1]:.1f} legs={len(g.legs)} adds={g.adds}")
    if g.legs and d is not None:
        direction = side_to_dir(g.legs[0]['side'])
        if check_signal_exit(direction, d['price'], d['ma'][-1], d['atr'], p['signal_exit_dist_atr']):
            line = d['ma'][-1] - direction * p['signal_exit_dist_atr'] * d['atr']
            log.info(f"[RU] SIGNAL_EXIT {g.current_contract} price={d['price']:.1f} line={line:.1f}")
            close_all_legs(context, 'signal_exit')
    try_add(context, d)
    try_open(context, d)


def risk_and_signal_routine(context):
    """分钟单入口：一致性校验 → 止损 → 14:45 信号流程（设计文档 §4）"""
    try:
        p = g.params
        check_state_consistency(context)
        bar = current_minute_bar(g.current_contract, context)
        minute_risk_checks(context, bar)
        if context.current_dt.strftime('%H:%M') != p['signal_time']:
            return
        if g.last_signal_date == context.current_dt.date():
            return
        signal_routine(context)
        g.last_signal_date = context.current_dt.date()
    except Exception as e:
        log.error(f"[RU] minute routine error: {e}")
```

- [ ] **Step 4: 写 initialize（追加到策略文件末尾）**

```python
# ==================== 初始化 ====================

def initialize(context):
    """初始化（设计文档 §4）"""
    try:
        init_cash = context.portfolio.starting_cash
        set_subportfolios([SubPortfolioConfig(cash=init_cash, type='futures')])
        set_option('use_real_price', True)
        set_option('avoid_future_data', True)
        set_option('futures_margin_rate', PARAMS['margin_rate'])
        set_order_cost(OrderCost(open_commission=0.000023, close_commission=0.000023,
                                 close_today_commission=0.000023, open_tax=0, close_tax=0,
                                 min_commission=0), type='futures', ref='RU')
        set_slippage(PriceRelatedSlippage(0.0003), type='futures')
        set_benchmark('RU9999.XSGE')
        log.set_level('order', 'error')

        g.params = dict(PARAMS)
        g.current_contract = None          # 首个 14:45 解析主力
        g.legs = []
        g.adds = 0
        g.last_signal_date = None
        g.last_flat_date = None
        g.warned = {}

        log.info('PARAMS: %s' % sorted(g.params.items()))
        run_daily(risk_and_signal_routine, time='every_bar')    # 回测频率需选"分钟"
    except Exception as e:
        log.error(f"[RU] init error: {e}")
```

- [ ] **Step 5: 本地回归**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: `11 passed`（模块仍可正常载入）

- [ ] **Step 6: 聚宽平台编译 + 冒烟回测（2018-01-01 ~ 2018-02-28）**

操作：聚宽 → 我的策略 → 新建策略 → 粘贴整个 `04-RubberTrendPullback-v1.0.py` → 点"编译" → 回测设置：初始资金 `500000`、频率"**分钟**"、区间 `2018-01-01` ~ `2018-02-28` → 运行。

Expected（日志区）：
- 首行 `PARAMS: [...]`（15 个参数）
- 14:45 首次出现 `[RU] dominant init RU1805.XSGE`（合约月份以平台返回为准）
- 每交易日 14:45 一行 `[RU] DAILY ...`
- 无 `[RU] init error` / `[RU] minute routine error`

已知平台差异的处置（本步同时核对规格 §6-2、§6-3、§6-4）：
- 若 `set_slippage(..., type='futures')` 报 TypeError → 改为 `set_slippage(PriceRelatedSlippage(0.0003))`（全局）
- 若 `set_benchmark('RU9999.XSGE')` 不被接受 → 删除该行（用默认基准）
- 若 14:45 未出现 `dominant init` → 记录现象（`get_dominant_future` 在回测中行为待查），把 `g.current_contract` 临时硬编码为一个当年的主力合约验证其余流程，并在 Task 7 记录

- [ ] **Step 7: Commit（建议，由用户执行）**

```bash
git add joinquant/04-RubberTrendPullback-v1.0.py
git commit -m "feat(ru): 策略主干（取数/订单/止损/信号流程/单入口）"
```

---

### Task 6: 主力切换与移仓（2 日防抖 + 先开新后平旧 + 价差平移）

**Files:**
- Modify: `joinquant/04-RubberTrendPullback-v1.0.py`（新增移仓函数 + 对 initialize / signal_routine / 分钟流程的精确插入与替换）

**Interfaces:**
- Consumes: Task 5 的 `g.legs`、`has_open_order`、`warn_once`、`order`、`compute_signal_data`
- Produces:
  - `check_dominant(context)`、`start_migration(context, new_contract)`、`retry_migration(context)`
  - 新状态：`g.dominant_counter`、`g.migrating`

- [ ] **Step 1: initialize 增加两个状态（精确插入）**

在 `g.warned = {}` 之前插入：

```python
        g.dominant_counter = {'contract': None, 'days': 0}
        g.migrating = None
```

- [ ] **Step 2: 追加移仓函数（追加到策略文件末尾）**

```python
# ==================== 移仓 ====================

def check_dominant(context):
    """主力切换检查：连续 2 个交易日确认；有持仓启动移仓，空仓仅更新记录（设计文档 2.8）"""
    if g.migrating is not None:                          # 移仓进行中：由 retry_migration 推进
        return
    dom = get_dominant_future('RU')
    if dom == g.current_contract:
        g.dominant_counter = {'contract': None, 'days': 0}
        return
    if g.dominant_counter['contract'] == dom:
        g.dominant_counter['days'] += 1
    else:
        g.dominant_counter = {'contract': dom, 'days': 1}
    if g.dominant_counter['days'] < 2:
        return
    g.dominant_counter = {'contract': None, 'days': 0}
    if not g.legs:
        log.info(f"[RU] dominant switch {g.current_contract} -> {dom} (flat)")
        g.current_contract = dom
        return
    start_migration(context, dom)


def start_migration(context, new_contract):
    """启动移仓：先开新后平旧"""
    side = g.legs[0]['side']
    total = sum(leg['lots'] for leg in g.legs)
    g.migrating = {'old': g.current_contract, 'new': new_contract, 'side': side, 'total': total,
                   'new_price': None, 'old_price': None}
    log.info(f"[RU] MIGRATE start {g.current_contract}->{new_contract} side={side} lots={total}")
    retry_migration(context)


def retry_migration(context):
    """推进移仓：每 bar 重试未成交腿；两腿全成交后统一更新 legs 并做价差平移"""
    m = g.migrating
    if m is None:
        return
    if has_open_order(m['new']) or has_open_order(m['old']):
        return
    if m['new_price'] is None:
        o = order(m['new'], m['total'], side=m['side'])
        if o is None or o.filled < m['total']:
            warn_once('mig_new', f"[RU] WARN migrate open unfilled {m['new']} lots={m['total']}")
            return
        m['new_price'] = o.price
        g.warned.pop('mig_new', None)
        log.info(f"[RU] MIGRATE open {m['new']} {m['side']} {m['total']}@{o.price:.1f}")
    if m['old_price'] is None:
        amt = -m['total']   # 平台实测：平仓（多/空）统一用负数量（正数量对空头是加仓）
        o = order(m['old'], amt, side=m['side'])
        if o is None or o.filled < m['total']:
            warn_once('mig_old', f"[RU] WARN migrate close unfilled {m['old']} lots={m['total']}")
            return
        m['old_price'] = o.price
        g.warned.pop('mig_old', None)
        log.info(f"[RU] MIGRATE close {m['old']} {m['side']} {m['total']}@{o.price:.1f}")
    delta = m['new_price'] - m['old_price']
    d = compute_signal_data(m['new'], context)
    if d is None:
        warn_once('mig_atr', f"[RU] WARN migrate new contract data missing {m['new']}")
    for leg in g.legs:
        leg['contract'] = m['new']
        leg['entry_price'] = m['new_price']
        if d is not None:
            leg['atr_lock'] = d['atr']
        leg['stop_line'] += delta
        leg['extreme'] += delta
    g.current_contract = m['new']
    g.migrating = None
    atr_str = f"{d['atr']:.1f}" if d is not None else "n/a"
    log.info(f"[RU] MIGRATE done {m['old']}->{m['new']} delta={delta:.1f} atr_lock={atr_str}")
```

- [ ] **Step 3: 在 `signal_routine` 中接入切换检查（精确替换）**

把 Task 5 中这三行：

```python
    if g.current_contract is None:                       # 首个 14:45 解析主力
        g.current_contract = get_dominant_future('RU')
        log.info(f"[RU] dominant init {g.current_contract}")
    d = compute_signal_data(g.current_contract, context)
```

替换为：

```python
    if g.current_contract is None:                       # 首个 14:45 解析主力
        g.current_contract = get_dominant_future('RU')
        log.info(f"[RU] dominant init {g.current_contract}")
    d = compute_signal_data(g.current_contract, context)
    prev_contract = g.current_contract
```

并在 `try_add(context, d)` 之前插入：

```python
    check_dominant(context)                              # 移仓（设计文档 2.8 执行顺序）
    if g.current_contract != prev_contract:
        d = compute_signal_data(g.current_contract, context)   # 换约后按新合约重取
```

同时把函数 docstring 改为：`"""14:45 信号流程：信号反转 → 移仓 → 加仓 → 开仓"""`

另把信号反转块的守卫行改为（移仓中间态先完成移仓，避免 `g.legs` 与实盘脱节）：把 Task 5 的 `if g.legs and d is not None:` 替换为

```python
    if g.legs and d is not None and g.migrating is None:  # 移仓中间态：仅推进移仓
```

- [ ] **Step 4: 分钟流程接入移仓重试（精确插入）**

在 `risk_and_signal_routine` 的 `minute_risk_checks(context, bar)` 之后插入一行：

```python
        retry_migration(context)
```

- [ ] **Step 5: 加移仓中间态保护（4 处精确插入）**

1) `minute_risk_checks` 开头：

```python
    if g.migrating is not None:                          # 移仓中间态：暂停止损（两腿未定）
        return
```

2) `check_state_consistency` 的 `if actual == tracked: return` 之后：

```python
    if g.migrating is not None:                          # 移仓中间态：两腿成交后统一修正
        return
```

3) `try_add` 的 `if not g.legs or g.adds >= p['max_adds']:` 改为：

```python
    if not g.legs or g.migrating is not None or g.adds >= p['max_adds']:
```

4) `try_open` 的 `if g.legs:` 改为：

```python
    if g.legs or g.migrating is not None:
```

- [ ] **Step 6: 本地回归**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: `11 passed`

- [ ] **Step 7: 聚宽移仓专项回测**

操作：回测设置区间改为 `2019-08-01` ~ `2020-02-29`（跨 ru 主力切换窗口，例如 RU2001→RU2005），其余不变 → 运行。

Expected（日志区）：
- `[RU] MIGRATE start RUxxxx->RUxxxx side=... lots=...`
- 同一次 14:45 内出现 `MIGRATE open ...` 与 `MIGRATE close ...`、`MIGRATE done ... delta=...`
- 次日 `DAILY` 行的合约代码已变为新合约（移仓当日的 DAILY 行在移仓前打印，仍是旧合约，属正常）
- 无 `STATE_MISMATCH` 告警（正常路径不应触发重建）
- 抽查：`MIGRATE done` 前后各 1 行 `STOP_HIT/CLOSE`（若有）的 stop 值差应等于 delta

- [ ] **Step 8: Commit（建议，由用户执行）**

```bash
git add joinquant/04-RubberTrendPullback-v1.0.py
git commit -m "feat(ru): 主力切换防抖与移仓（先开新后平旧 + 价差平移）"
```

---

### Task 7: 全区间回测 + 规格 §6 九项核对 + §1 判据验收

**Files:**
- Create: `docs/superpowers/plans/2026-09-19-ru-trend-pullback-v1.0-backtest-record.md`

**Interfaces:**
- Consumes: 完整策略文件
- Produces: 验收记录（绩效指标、九项假设结论、五项判据结论、敏感性粗扫结果）

- [ ] **Step 1: 全区间回测**

操作：区间 `2018-01-01` ~ `2026-09-19`（至今），初始资金 500000，分钟频率 → 运行 → 等待完成。

Expected: 回测完成无中断；日志无 `[RU] ... error`；无 `STATE_MISMATCH`。

- [ ] **Step 2: 建立验收记录文件并填绩效指标（§1 判据 4）**

创建 `docs/superpowers/plans/2026-09-19-ru-trend-pullback-v1.0-backtest-record.md`，写入下表并填入平台数据（年化、最大回撤、胜率、盈亏比、手续费占比从回测结果页抄录；多空拆解从交易记录按 side 统计）：

```markdown
# 橡胶 RU 趋势回踩 v1.0 回测验收记录

回测区间：2018-01-01 ~ 2026-09-19 ｜ 初始资金 50 万 ｜ 分钟频率 ｜ 成本：手续费万0.23 ×2 + 滑点 0.0003

## 绩效指标
| 指标 | 数值 |
|---|---|
| 年化收益 | |
| 最大回撤 | |
| 胜率 | |
| 盈亏比 | |
| 手续费占比 | |

## 分多空拆解（交易记录按 side 统计）
| 方向 | 交易次数 | 收益 | 胜率 | 盈亏比 |
|---|---|---|---|---|
| 多头 | | | | |
| 空头 | | | | |

## 出场→重进分布
| 指标 | 数值 |
|---|---|
| 全平→下次开仓 间隔（交易日）中位数 | |
| 间隔 ≤3 日的次数 | |
| 总全平次数 | |
```

- [ ] **Step 3: 逐笔抽查（§1 判据 2 + 5）**

从日志/交易记录抽 3-5 笔完整交易，逐笔核对并记录到验收记录：
- 开仓合约 = 当日 `get_dominant_future('RU')` 返回的合约
- 每轮加仓次数 ≤ 2
- 单笔风险抽查：`(entry=… − stop=…) × lots × 10 ÷ equity=…` ≈ 0.02（日志 `ENTRY/ADD` 行含 entry/stop/lots/equity，直接可算；因最低 1 手与取整通常略低）
- 最坏一轮止损时同方向持仓笔数 ≤ 3，亏损 ≈ 3 笔之和

- [ ] **Step 4: 成本敏感性（§1 判据 3）**

把 `initialize` 中 `open_commission/close_commission/close_today_commission` 改为 `0.000046`（×2）、`set_slippage(PriceRelatedSlippage(0.0006))`（×2），重跑全区间，记录净值与年化；恢复原值。判据：×2 后仍为正收益。
（可选：×3 用 `0.000069` / `0.0009`。）

- [ ] **Step 5: 规格 §6 九项假设逐项核对（写入验收记录）**

| # | 假设 | 验证方法 |
|---|---|---|
| 1 | `attribute_history('1d')` 盘中不含当日 | 对比 `DAILY` 行 ma 值与"截至前一交易日的 30 根日线"重算值（聚宽研究环境复算一次即可） |
| 2 | `set_slippage(type='futures')` | Task 5 冒烟已确认；记录结果 |
| 3 | `set_benchmark('RU9999.XSGE')` | 回测结果页基准曲线是否为 RU9999；不显示则记录 |
| 4 | `get_dominant_future('RU')` 逐日可用 | `dominant init` 与 `dominant switch` 日志出现且合约代码符合当时主力 |
| 5 | `order()` 的 side 语义与平今手续费 | 抽查交易记录方向与 `OPEN/CLOSE` 日志一致；手续费栏目核对 |
| 6 | 分钟取数与夜盘归属 | `DAILY` 行的 hi/lo 与平台 K 线"前夜 21:00 至今 14:45"极值抽查对照 2 日 |
| 7 | 合约乘数来源 | `value/(price×amount)` 与 `PARAMS['multiplier']=10` 对照一次（用持仓期间任一时刻） |
| 8 | 保证金不足行为 | 观察是否出现 `WARN open skip margin insufficient`；有则记录当时的可用资金 |
| 9 | 停板时未成交委托状态 | 若出现 `WARN ... unfilled (limit board?)`，记录后续几 bar 是否重复告警、次日是否自然重试成交 |

- [ ] **Step 6: 敏感性粗扫（规格 §5，逐个改参重跑，记录形态）**

对下列 4 个参数在 `PARAMS` 中逐一改值、各重跑全区间一次（每次只改一个），把年化/最大回撤记入验收记录，确认无"盈利翻亏"断崖后恢复默认：

- `signal_exit_dist_atr`：0 / 0.5 / 1.0
- `cooldown_days`：0 / 2 / 3
- `pullback_max_break_atr`：0 / 1 / 1.5 / 2
- `add_min_dist_atr`：0 / 0.5 / 1.0

- [ ] **Step 7: 判据总结与结论**

在验收记录末尾写结论：五项判据（§1）逐条 通过/不通过 + 现象；若有不通过项，列出证据与下一步建议（不擅自调参）。

- [ ] **Step 8: Commit（建议，由用户执行）**

```bash
git add docs/superpowers/plans/2026-09-19-ru-trend-pullback-v1.0-backtest-record.md joinquant/04-RubberTrendPullback-v1.0.py
git commit -m "docs(ru): v1.0 回测验收记录"
```

---

## 验收标准

1. `tests/` 全部通过（11 用例）且策略文件在聚宽可编译、全区间回测跑通无异常、无状态不一致告警。
2. 日志逐条含规格 §5 要求字段；逐笔抽查符合设计（主力正确、加仓 ≤2、止损线与公式吻合、单笔风险 ≈2%）。
3. 成本 ×2 后仍为正收益；§6 九项假设逐项有结论；§5 敏感性粗扫四项完成并记录。
4. 全部验收数据落入 `docs/superpowers/plans/2026-09-19-ru-trend-pullback-v1.0-backtest-record.md`。
