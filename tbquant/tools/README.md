# tbquant 工具与回归基准

TB 回测的**切片回归检查**配套：每次重跑后，把日志与一次"验明正身"的基准逐行比对，
验证代码改动零回归。核心 = 两个脚本（`tools/`）+ 一个基准库（`baselines/`）。

## 目录

| 路径 | 作用 |
|---|---|
| `tools/read_tbf.py` | TBQuant `.tbf` 日志 → 文本（比对前的导出步骤） |
| `tools/check_slice.py` | 文本日志 vs 基准的逐行比对（`make` / `check` 子命令） |
| `baselines/sliceN/` | 各切片基准快照（纯数据，非代码） |

## 使用流程（在仓库根目录执行）

### ① 跑完 TB 回测后，导出 .tbf 为文本

```bash
python tbquant/tools/read_tbf.py "<日志.tbf>" <导出.txt>
```

TBQuant 同日重跑会**覆盖同名日志**——要回归比对就必须跑完立即导出；导出后即可安全重跑。

### ② 回归检查：新跑 vs 基准

```bash
python tbquant/tools/check_slice.py check <导出.txt> -d tbquant/baselines/slice4
```

- exit **0** = 通过；**1** = 不一致（列出明细，每类最多 10 处）；**2** = 找不到基准
- 返回码可直接挂脚本/CI 判断

### ③ 新增切片提基准（仅新切片需要）

```bash
python tbquant/tools/check_slice.py make <导出.txt> -d tbquant/baselines/slice6 [-s '<软豁免正则>']
```

基准必须来自一次"验明正身"的运行（交易与聚宽对拍一致），并写 `provenance.txt` 记录来源。

## 比对规则（check）

| 类别 | 规则 |
|---|---|
| 交易行(OPEN/CLOSE/SKIP)、DAILY、结束行、未识别行 | (时间戳, 消息体) 逐字全等 |
| 参数A/B/C | 只比消息体（时间戳=运行日，跨日会变） |
| 头部行（00:00:00 时间戳） | 豁免：StrategyMode / SetMarginRate 返回 / 排版文案等随版本与环境演进的字段 |
| 软豁免 | 基准目录 `soft_exempt.txt` 正则命中行：值差异仅提示；**命中行数变化仍 FAIL** |

更多细节见 `python tbquant/tools/check_slice.py -h` 或脚本头部 docstring。

## 基准目录文件说明

| 文件 | 含义 |
|---|---|
| `raw_export.txt` | 比对源：.tbf 导出快照 |
| `trades.txt` / `daily.csv` / `summary.txt` | 人读视图：交易清单 / 日线序列 / 参数+结束行 |
| `provenance.txt` | 来源运行、配置与验明正身证据——**复跑前先读它** |
| `soft_exempt.txt` | 可选软豁免正则（如冷启动边界行），仅部分基准有 |

## 已收录基准

| 切片 | 合约 | 回测区间 | TradeStartDate | 期末权益（vs 聚宽，§4.3） |
|---|---|---|---|---|
| slice4 | p2405 | 2023-10-01 ~ 2024-04-26 | 20240201 | 1,019,880（Δ−760） |
| slice5 | p2501 | 2024-04-01 ~ 2024-12-30 | 20240801 | 1,113,820（Δ−2,520） |
| slice6 | p2505 | 2024-09-01 ~ 2025-03-10 | 20250101 | 1,010,400（Δ+2,160） |

> 切片6 注：图表终点设 2025-03-10 时，TB 实际末根 bar=2025-03-07（与验收跑行为一致）。

**复跑时的图表范围（两图层同步）与参数必须与 `provenance.txt` 记录一致**，否则 DAILY 首行与结束行天然对不上。

## 注意事项

- DAILY 首行 = 日线窗口刚满足的"边界日"，取值随图表预热深度/代码版本浮动——已给 slice5 首行设软豁免；新切片若遇同类情况照办（`soft_exempt.txt` + 注释写清理由）。
- 比对不通过时：先看差异落在哪一类——`trade`/`DAILY` 是硬信号，必须逐条解释到零；`header` 类差异先确认是否属豁免范围。
- 公式与对拍背景：`tbquant/05-PTrend-turtle-v4.0-TB-说明.md`（§4.3 已实测对拍结果）。
