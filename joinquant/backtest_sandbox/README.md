# backtest_sandbox 沙盒

聚宽策略的**本地逐分钟回放引擎**：加载 `joinquant/` 下的策略文件（**只读、零修改**），
用本地模拟层复现聚宽期货回测语义。跑完同一份策略文件可直接贴回聚宽平台。

## 文件

| 文件 | 说明 |
|---|---|
| `jqdata.py` | 聚宽 API 模拟层：`attribute_history` / `get_bars` / `order` / `run_daily` / `log` / `g` / `context` 等，数据读自 `local_data/` 的 CSV |
| `runner.py` | 逐分钟回放引擎：日盘 every_bar + 夜盘注册任务的事件循环，输出与聚宽导出格式一致的日志 |

## 用法

环境：需 pandas / numpy（本地惯例用 `jq` conda 环境）。在**仓库根目录**执行：

```bash
# RU 2020~2026（--strategy / --data 有默认值，可省略）
python joinquant/backtest_sandbox/runner.py \
    --start 2020-01-01 --end 2026-09-28 \
    --out joinquant/backtest_sandbox/out_ru.log

# 棕榈油（P）
python joinquant/backtest_sandbox/runner.py \
    --data local_data/export_p --tick 2 \
    --param symbol=P --param exchange=XDCE \
    --out joinquant/backtest_sandbox/out_p.log

# 参数试验（运行时覆盖 PARAMS，策略文件保持零修改）
python joinquant/backtest_sandbox/runner.py \
    --param entry_max_dist_atr=1.0 --param stop_loss_dist_atr=2.5 --out /tmp/try.log

# 查看入场判定细节（ENTRY_PASS / ENTRY_REJECT 原因）
python joinquant/backtest_sandbox/runner.py \
    --start 2026-08-01 --end 2026-08-31 --debug --out /tmp/dbg.log
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--strategy` | `joinquant/04-RubberTrendPullback-v1.0.py` | 策略文件（任何文件均可，含历史版本/试验版） |
| `--data` | `local_data/export_ru` | 数据根（P 用 `local_data/export_p`） |
| `--start` / `--end` | `2020-01-01` / `2026-09-28` | 回测区间 |
| `--tick` | `5` | 最小变动价位（RU=5；P=2） |
| `--multiplier` | `10` | 合约乘数（RU/P 均 10） |
| `--cash` | `1000000` | 起始资金 |
| `--out` | 本目录 `out.log` | 输出日志（格式与聚宽导出一致，可直接 diff） |
| `--param` | — | 运行时覆盖策略 `PARAMS`（`k=v`，可重复） |
| `--debug` | 关 | 策略日志级别设为 debug |

结束时会打印摘要：`events / fills / errors / final_equity / realized / fees / 未平仓位`。

## 数据

数据来自聚宽研究环境导出（脚本：`joinquant/tools/jq_export_in_jupyter.py`；
本地安装与审计：`joinquant/tools/apply_export.py`），目录：

- `local_data/export_ru/` — RU 2020~2026 全部主力合约（21 个）
- `local_data/export_p/` — 棕榈油 P 2020~2026 全部主力合约（21 个）

每个数据集含：逐合约日线 `daily_*.csv`、分钟线 `min1_*.csv`（含夜盘）、
交易日历 `trade_days.csv`、逐日主力映射 `dominant.csv`。`local_data/` 不入库（见 `.gitignore`）。

## 语义与拟合（与聚宽对齐的要点）

- **调度**：交易日 D 的日盘每个分钟 bar 触发 `every_bar`；夜盘（D 日 21:00~23:00，
  归属次一交易日）仅触发显式注册的 `run_daily` 任务——平台实测 `every_bar` 不覆盖夜盘，
  策略据此按分钟单独注册夜盘任务。
- **成交价**（拟合自平台日志 27/27 成交样本）：买入 = 当前分钟 bar 收盘价；
  卖出 = 收盘价 − 1 个最小变动价位。
- **账户**：权益 = 起始资金 + Σ已实现 − Σ手续费 + Σ浮动盈亏；手续费 = 成交额 × 费率；
  保证金 = 最新价 × 手数 × 乘数 × 保证金率。
- **数据口径**：bar 时间戳为**结束时间**；夜盘 bar 的自然日期为前一晚
  （策略内以"前一交易日 20:00"切会话）。

## 校准记录（已验收）

- 校准A：原始版策略复现 `joinquant/2026_ru_trade.log` —— **逐字节 0 差异**；
- 校准B：优化版（旧 d 逻辑）复现 `joinquant/2026_ru_trade_2.log` —— 仅 1 行 1 个显示字段差异
  （移动止损账面值，成交价/盈亏/权益全部一致）；
- 当前版回归：T13 恢复入场（8/10 3 手 @17840 → 9/11 平仓 +40050）。

## 已知边界

- 数据为**快照**（截至 2026-09-28），之后使用需重跑导出脚本更新；
- 撮合/滑点为**对账拟合**（非平台源码复刻），最终结论仍以聚宽平台为准；
- 导出数据在合约上市前/退市后可能含零成交填充 bar——不影响回放
  （引擎按时间窗取数，用不到的 bar 不会被读到）；
- 本目录 `out*.log` 为运行输出（已 gitignore，可随时重跑生成）。
