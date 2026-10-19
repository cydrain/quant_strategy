# 05-PTrend-turtle-v3.2 TBQuant 公式版 使用说明

> 适用文件：`tbquant/05-PTrend-turtle-v3.2-TB.txt`（公式名 `PTrendTurtle_v32`，TBQuant 2.x）
> 移植基准：`joinquant/05-PTrend-turtle-v3.2.py`（聚宽 · 棕榈油 P · STALL 版）
> 前版对照：`tbquant/05-PTrend-turtle-v3.1-TB.txt`（双图层 · 无 STALL）
>
> v3.2 = v3.1 的海龟交易逻辑（N/四通道/手数/unit/止损/加仓/出场/台账自愈/移仓）原样保留，
> 新增 **STALL 极值停滞并行入场**；同时补齐模拟/实盘运行硬化：主连挂图校验、挂单门禁、
> OnOrder/OnFill 回报日志、`SlipTicks` 命名。`StallPeriod=0` 时**回测交易逻辑**应与 v3.1 一致
> （模拟/实盘另有主连校验与挂单门禁）。

---

## 1. 文件清单

| 文件 | 用途 |
|---|---|
| `tbquant/05-PTrend-turtle-v3.2-TB.txt` | **主策略公式**（用户应用），双图层挂载运行 |
| `tbquant/05-PTrend-turtle-v3.1-TB.txt` | 前版（无 STALL），回归对照用 |
| `tbquant/05-PTrend-turtle-v3.1-TB-说明.md` | 前版完整说明（回测/换月/预热细节仍适用） |
| `tbquant/PTrendTurtle_MultiSrc_Probe.txt` | 双图层运行时假设探针（非交易） |
| `tbquant/PTrendTurtle_EventProbe.txt` | 事件驱动探针（回测事件为 0 次的实测依据） |
| `joinquant/05-PTrend-results.md` | 聚宽 v3.1/v3.2 六切片结果与 STALL 触发明细 |
| `tbquant/tools/README.md` | 日志导出/基线工具说明 |

---

## 2. 与 v3.1 的差别（先看这里）

| 项 | v3.1 | v3.2 |
|---|---|---|
| STALL 并行入场 | 无 | 新增，`StallPeriod>0` 启用 |
| 滑点参数名 | `SlippageTicks` | `SlipTicks`（语义不变；旧参数模板需重设） |
| `MapToMain` 默认值 | False | **True**（模拟/实盘安全口径；单合约切片回测必须显式改 False） |
| `MainContSymbol` | 无 | 新增，默认 `p9888.DCE` |
| 模拟/实盘挂图校验 | 无 | data0/data1 必须同一主连 + `MapToMain=True`，否则禁止开新仓 |
| 挂单门禁 | 无 | OnOrder 未完成委托集合非空时拒发任何新委托 |
| 回报日志 | 无 | `ORDER`/`FILL`/`ORDER_GATE`/`PEND_ADD`/`PEND_DEL`/`EXIT` |

---

## 3. 三种模式总览

| | 回测 | 模拟 | 实盘 |
|---|---|---|---|
| 运行位置 | 策略研究 · 回测 | 图表 + 模拟账户（策略交易模式） | 图表 + 实盘账户（策略交易模式） |
| data0 | 1 分钟（单合约或主连） | **主连 P9888 1 分钟** | **主连 P9888 1 分钟** |
| data1 | 同合约日线（标准） | 同合约日线（标准） | 同合约日线（标准） |
| MapToMain | 主连图 True / 单合约切片 False | **必须 True** | **必须 True** |
| 下单价 | 触发价；跳空越过时按开盘价 | 盘口对手价（买吃卖一、卖吃买一） | 同模拟 |
| 资金口径 | `Portfolio_*` | `A_*` | `A_*` |
| 挂单门禁 | 不生效（事件不回调） | 生效（OnOrder） | 生效（OnOrder） |
| 换月 | 主连图后复权；单合约图无 | 平台原生自动换仓 + 委托映射主力 | 同模拟 |

注意：v3.2 的模拟/实盘**不再允许单合约图 + MapToMain=False**。该组合会在 OnInit 被判为
`MappingOk=False`，`CanTradeNow()` 恒 False，不会开新仓。单合约只用于回测切片。

---

## 4. 挂载与通用前置条件

1. **双图层（缺一不可）**
   - `data0` = 目标合约 **1 分钟**：全部信号/风控/下单在此层执行；
   - `data1` = **同合约日线（1d）**：纯只读指标源，提供 N/四通道与 STALL 计数。
   - 追加：策略研究中选中策略单元 → 右键数据源 → 周期选 `1d` → 追加。
   - 两图层都右键「同步为主商品数据范围」，避免回测起止错位。
2. **运行模式选「K线收盘模型」**。逐笔模型每 tick 触发 OnBar，不适合本公式。
3. **数据历史深度**
   - N 计算硬要求：`data1` 在首个目标信号日前已有 **≥61 根日线**（WinSize=61，约 3 个月）；
   - STALL 扫描要求：`2*StallPeriod+5` 根。默认 `StallPeriod=10` → 25 根，61 根已覆盖；
   - 历史不足的后果见 v3.1 说明第 3/9 节（N 静默偏高、假突破）。
4. **data1 周期**
   - 标准 = 日线；也支持 30 分钟等日内周期（实验）。
   - N/四通道与 STALL 计数都**随 data1 周期推进**：挂 30 分钟 data1 就是 30 分钟口径，
     与聚宽 v3.2（固定日线）不可比；30 分钟下 `Data1[0]/[1]` 盘中对齐尚未经探针复验。
5. **模拟/实盘专用校验**
   - `data0.Symbol` 必须与参数 `MainContSymbol` **完全一致**（含大小写与交易所后缀）；
   - `data1.Symbol` 必须与 `data0.Symbol` 相同；
   - `MapToMain=True`；
   - OnInit 会打印 `MAPPING: ... ok=True/False`；不满足时记 `MAP_FAIL` ERROR 并禁止开仓；
   - 若 OnInit 时 data1 尚未就绪，首个 data1 齐备的 bar 会再复核一次，日志关键字
     `MAPPING_RECHECK`；仍不符则保持锁定（合约/图层是静态配置，不会运行中变化）。
   - 该检查是**名称精确匹配**，不是平台级“是否主连”查询；不要为了绕过校验把 `MainContSymbol`
     改成单合约代码——`MapToMain=True` 会把委托映射到真实主力。

---

## 5. 回测怎么用

### 5.1 步骤

1. 新建公式，粘贴 `05-PTrend-turtle-v3.2-TB.txt` 全文，命名 `PTrendTurtle_v32`，编译。
2. 挂图：目标合约 1 分钟；**单合约切片回测必须把 `MapToMain` 改为 False**。
3. 追加同合约日线图层，两图层同步数据范围。
4. 1 分钟 K 线加载数量建议 ≥16 万根（分段约 2.7~5 万，整段 18~60 万）。
5. 回测区间 = 预热起点 ~ 切片终点；`TradeStartDate` = 实际开仓日；
   日线历史须在 `TradeStartDate` 前 ≥61 个交易日。
6. 回测设置：初始资金 100 万；保证金/手续费/滑点由 OnInit 设置（12% / 0.000023 按金额 / 1 跳）。
   `SetMarginRate/SetCommissionRate/SetSlippage` 显示 False 属正常，设置仍生效。
7. 跑完读回测报告与日志。

### 5.2 六切片参数与聚宽基准

> 每个切片独立回测；资金 100 万，其余参数默认；切片 3 为只做空（FixedDir=-1）。
> 下表的 v3.2 列是**聚宽 v3.2 基准**，不是 TB 实测值；TB 尚未跑 v3.2 六切片。

| # | 预热起点 | TradeStartDate | 回测终点 | FixedDir | v3.1 聚宽基准 | v3.2 聚宽基准(stall=10) | STALL 次数 |
|---|---|---|---|---|---|---|---|
| 1 | 2019-11-01 | 20200313 | 2021-01-31 | 1 | +259,220 | +315,600 | 3 |
| 2 | 2021-02-01 | 20210601 | 2022-03-31 | 1 | +371,940 | +373,360 | 2 |
| 3 | 2022-01-01 | 20220501 | 2022-10-31 | -1 | +163,160 | +154,400 | 1 |
| 4 | 2023-10-01 | 20240201 | 2024-04-26 | 1 | +20,640 | +44,760 | 1 |
| 5 | 2024-04-01 | 20240801 | 2024-12-30 | 1 | +116,340 | +116,340 | 0 |
| 6 | 2024-09-01 | 20250101 | 2025-03-10 | 1 | +8,240 | +8,240 | 0 |
| 合计 | | | | | +939,540 | +1,012,700 | 7 |

### 5.3 v3.2 回测验收建议

1. **回归**：`StallPeriod=0` 跑切片 4/5/6，trades/DAILY 应与 v3.1 一致（仅头部参数行不同）。
2. **STALL 触发日**：见第 6.3 节；单合约切片只核对信号日与方向，不要求与聚宽逐笔同价同时。
3. **判定标准**：常规海龟交易的方向、手数、时间（±1 分钟）一致；价格允许 1~数跳差；
   非换月段平仓盈亏偏差 ≤2%；STALL 交易按第 6.3 节的时点差异单独核对。
4. **注意**：TB 的 STALL 触发时点比聚宽早：完成日线当晚 21:01（周一至周四对应次日 09:01，
   约早 12 小时；周五对应下周一 09:01，跨周末约早 60 小时），成交价与后续加仓档位可能不同；
   不要把“逐笔完全一致”作为 v3.2 的验收标准。

---

## 6. STALL 规则详解

### 6.1 事件与计数

- **多头事件**：某根 data1 bar 的 `Low(j)` 严格小于其前 `StallPeriod` 根的最低价
  （`Low(j) < min(Low(j+1..j+n))`）；
- **空头事件**：某根 data1 bar 的 `High(j)` 严格大于其前 `StallPeriod` 根的最高价
  （`High(j) > max(High(j+1..j+n))`）；
- **计数**：从最新完成 bar 往前扫，最近一次事件距最新 bar 的根数（事件当根=0）；
  扫描窗口 `2*StallPeriod+5` 根，窗口内无事件记 -1；
- **触发**：计数**恰等于** `StallPeriod` 才入场；多空同时达成时长优先；
- **方向**：受 `FixedDir` 约束（1=只多 / -1=只空 / 0=双向）。

### 6.2 边沿与入场语义

- 边沿 = `Data1[1]` 的 OHLC 签名变化；**每根 data1 bar 只评判一次**；
- 首次读取只建立基线，不触发（启动/重启前已达成的不补）；
- 边沿出现后，若空仓且方向允许 → 市价开 1 unit（`reason=entry_stall`）；
- 若持仓中达成 → `STALL_MISS reason=holding`，**错过不补**；
- 若已有未完成委托 → `STALL_MISS reason=pending`，本边沿放弃；
- 若预热/数据无效期达成 → `STALL_MISS reason=gated`（DEBUG 级），本边沿放弃；
- STALL 与突破同 bar 时 **STALL 优先**；STALL 分支一旦选中，本 bar 不再跑常规海龟检查，
  即使随后手数不足/委托被拒也不改跑突破（与聚宽分支语义对齐）。

### 6.3 触发时点差异（重要）

聚宽在 `09:01` 看到上一交易日日线并评判；TB 在 data1 推进后的**首根 1 分钟 bar** 评判。
对 P（有夜盘），这通常是完成日线当天的 **21:01**（该夜盘属于下一交易日）。

| 聚宽触发（09:01） | 使用的日线 | TB 预期触发（未实测） |
|---|---|---|
| 2020-03-30 | 2020-03-27 | 2020-03-27 21:01 |
| 2020-05-15 | 2020-05-14 | 2020-05-14 21:01 |
| 2020-10-23 | 2020-10-22 | 2020-10-22 21:01 |
| 2021-06-08 | 2021-06-07 | 2021-06-07 21:01 |
| 2021-07-05 | 2021-07-02 | 2021-07-02 21:01 |
| 2022-05-19 | 2022-05-18 | 2022-05-18 21:01 |
| 2024-02-28 | 2024-02-27 | 2024-02-27 21:01 |

若某夜盘因节假日不存在，应在下一根 1m bar 触发（如 09:01）。
表内 7 个触发日已用 `local_data/export_p/daily_*.csv` 按聚宽计数口径复算，全部命中。

### 6.4 窗口上限

`2*StallPeriod+5` 必须 ≤ `WinSize()`：

- 默认 `NPeriod=20` → `WinSize=61` → `StallPeriod` 最大 **28**；
- 超限时 OnInit 自动停用 STALL 并记 ERROR，海龟部分照常运行；
- 不要把 `StallPeriod` 调大后指望公式自动扩窗（扩窗会改变 N 递归窗口，破坏对拍）。

---

## 7. 模拟怎么用

1. **挂图**：主连 `P9888` 1 分钟 + 同合约日线；`MapToMain=True`；
   `MainContSymbol` 与日志 `data0.Symbol` 完全一致（默认 `p9888.DCE`）。
2. **数据深度**：日线图层加载 ≥100 根（硬要求 ≥61；STALL 默认只需 25）。
3. **运行模式**：K线收盘模型；把图表/策略单元挂接模拟期货账户。
4. **启动核对**（日志头部）：
   - `data0.Symbol=...` / `data1.Symbol=...(1d)`；
   - `MAPPING: ... ok=True`（若 OnInit 时 data1 未就绪，看后续 `MAPPING_RECHECK ok=True`）；
   - `GATE: 挂单门禁=...`；
   - `STALL: enabled=True StallPeriod=10 win=61 need=25 data1周期=1d`；
   - `IsLiveTrade=True`。
5. **启动回放**：平台会先回放历史 bar（不发单），恢复到实时 bar 后才交易；
   若账户已有持仓而台账为空，首个持仓 bar 出现 `STATE_REBUILD` 自动重建。
6. **首单链路验收**：
   - `ORDER #n ... PEND_ADD` → 成交/撤单后 `PEND_DEL`、`pending=0`；
   - 成交时 `FILL` 行与 `OPEN`/`CLOSE` 行的方向、手数、价格能对上；
   - 只有挂单期间出现 `ORDER_GATE`（每日首条）；持续不解除 = 终态事件缺失；
   - 成交跨 bar/重启/部分成交时可能出现 `LEDGER_CLEAN`/`STATE_REBUILD`，出现后核对手数与止损。
7. **STALL 验收**：模拟从当前时间开始，只能等下一个日线边沿（通常 21:01）验证实时链路；
   历史 7 个触发日请用回测验证，不要在模拟里等历史重演。
8. **建议模拟 ≥1 个月**，逐日核对 DAILY 快照、委托与持仓后再考虑实盘。

### 7.1 挂单门禁规则与限制

- OnOrder 收到 `Declare/Declared/FillPart/Canceling` → 加入未完成集合；
- 收到 `Filled/Canceled/Deleted`（或未知状态）→ 移出集合；
- 集合非空时 `DoSendOrder` **拒绝任何新委托**，包括止损/反向突破平仓/加仓；
- 回测不触发 OnOrder → 集合恒空，门禁不生效；
- **只拦不撤**：当前没有撤单、重试或超时。若柜台漏报终态，策略会持续禁发；
- 启动前已经在场的委托不会入集合；重启时 OnInit 清空集合（首 bar 再兜底清一次），
  旧委托若随后成交由 `STATE_REBUILD` 按平台持仓自愈；
- 模拟盘已实测回调（RateCheckProbe 2026-10-19）；若实盘柜台完全不回调，门禁 fail-open
  （不会死锁），但也没有保护；
- 因此：**模拟可先跑；实盘前必须先验证 PEND_ADD/PEND_DEL 全链路，并补撤单/超时兜底。**

### 7.2 已知风险（按严重度）

1. 门禁连止损一起拦，且无撤单/超时 —— 实盘最大风险；
2. OnOrder/OnFill 回测确认不回调（EventProbe）；模拟盘已由 RateCheckProbe 实测回调正常
   （2026-10-19，p2701，Declare→Declared→Filled + OnFill 字段完整）；实盘柜台是否回调仍未验证；
3. 乐观台账 + 成交跨 bar：先 `LEDGER_CLEAN` 后 `STATE_REBUILD`，数量可恢复；
   UnitCount 按平台建仓次数恢复，止损按重建时的 N 重推，可能与入场时略有偏差；
4. `MAPPING ok=False` 若由 data1 晚就绪导致，首 bar 会复核；若仍 False，检查合约/图层/参数；
5. ~~OnFill `FillRef.volume` 字段未验证~~ **已消除**：2026-10-19 模拟实测
   `volume/price/fillPrice` 均有效。

---

## 8. 实盘怎么用

挂载步骤与模拟相同，另注意：

1. **不建议在门禁事件链验证前上实盘**：先完成第 7 节的模拟清单；实盘前补
   stale-pending 超时或撤单（当前版本没有）。
2. **账户与下单**：资金用 `A_CurrentEquity / A_FreeMargin`；下单吃盘口对手价
   （买吃卖一、卖吃买一），盘口无效退回收盘价/理论价。
3. **换月（主连图 P9888 + MapToMain=True）**：委托自动映射真实主力；平台原生自动换仓，
   公式检测 `MainSymbol()` 变化后重建台账并记 `ROLLOVER`。
   回测下 `MainSymbol()` 恒为当前实盘主力，回测日志看不到换月行是正常的。
4. **风控注意**：
   - data1 刷新失败 → 记 `N/四通道刷新失败` ERROR，拒绝开新仓与常规管理（含出场/加仓）；
     实盘/模拟仍会用最后有效止损线尝试平仓（`STOP_ONLY`）；
   - 委托被拒（停板等）→ `OPEN_FAIL/CLOSE_FAIL`，下一 bar 自动重试（但可能被门禁拦）；
   - 海龟是趋势跟踪策略，历史切片属于“已知行情”的参数验证，实盘前务必先模拟。
5. **日志**：模拟/实盘按物理日分文件，同日续写不清空；回测 OnInit 会删除当日文件。
   文件名格式 `公式名_合约_日志_yyyyMMdd.tbf`（合约取 data0 的 `Symbol`，如
   `PTrendTurtle_v32_p9888.DCE_日志_20261018.tbf`），同目录跑 P/RU 两个实例也不会混。
   `.tbf` 不是纯文本，用 TB 的「数据中心 → 文件查看器」打开，或
   `python tbquant/tools/read_tbf.py <日志.tbf> <导出.txt>` 导出。
   OnInit 会写一行 `日志: LogDir=[...] 实际目录=... 文件=...`；`LogDir` 非空但末位漏写
   `/` 时代码自动补齐，可先用该行确认实际落盘位置。
   同一台机器同日跑回测与实盘、且合约相同时，仍请设置不同的 `LogDir` 或公式名。
6. **无图表数据源**：`Print` 只在 K 线图控制台可见，无图表策略单元没有界面输出
   （右下角消息中心只显示委托/账户事件，不含 `Print`）；本策略的日志主体走 `FileAppend`，
   无图表照样写入。默认 `LogDir` 指向仓库内 `tbquant/logs/`（本机绝对路径，换机器/云端需改）；
   留空才落在平台运行目录（本机实测为 `C:\Users\MI\AppData\Roaming\TBQuant\`）。
   末位 `/` 可省略，代码自动补；已挂图表实例仍沿用各自保存的参数，需手动改成同一路径。

---

## 9. 参数速查

| 参数 | 默认 | 含义 |
|---|---|---|
| FixedDir | 1 | 1=只做多 / -1=只做空 / 0=双向 |
| NPeriod | 20 | N（ATR）周期 |
| NSmooth | 20 | N 平滑系数（经典 N=((s-1)*N+TR)/s） |
| EntryN | 20 | 突破入场通道周期（系统1=20） |
| ExitN | 10 | 反向突破出场通道周期（系统1=10） |
| **StallPeriod** | **10** | 连续 N 根 data1 极值停滞 → 入场；0=关闭 STALL |
| UnitRisk | 0.01 | 1 unit 风险预算（占权益比例） |
| MarginCapPct | 0.10 | 保证金占用上限（占权益比例，0=不限） |
| MaxUnits | 4 | 最大 unit 数（含首仓） |
| AddStepN | 0.5 | 顺向每 N×该值 加 1 unit |
| StopDistN | 2.0 | 止损距离（N 倍数） |
| S1Skip | 0 | 1=上次突破盈利则跳过下一次同类突破 |
| TradeStartDate | 20200313 | 开始交易日期 yyyymmdd；之前仅预热不交易 |
| InitCapital | 1000000 | 初始资金（回测） |
| MultiplierP | 10 | 合约乘数（元/点/手，P=10） |
| MarginRateP | 0.12 | 保证金率 |
| CommissionRateP | 0.000023 | 手续费率（按成交金额） |
| SlipTicks | 1 | 回测撮合滑点（跳；P 一跳=2 元/吨）；模拟/实盘实际滑点由盘口/柜台决定 |
| MapToMain | True | True=主连/模拟/实盘（换月全套+委托映射主力）；False=单合约切片（不复权、不映射） |
| MainContSymbol | p9888.DCE | 模拟/实盘唯一允许的主连合约；须与日志 data0.Symbol 完全一致 |
| LogLevel | 2 | 日志阈值：0=仅ERROR 1=+WARNING 2=+INFO 3=+DEBUG |
| PrintToUI | True | True=同步 Print 到 K 线图控制台；无图表单元无界面输出（文件日志不受影响）；长回测/批量优化建议 False |
| LogDir | C:/Users/MI/work/quant_strategy/tbquant/logs/ | 日志目录（本机绝对路径，换机器/云需改）；留空/相对路径=平台运行目录；末位缺 / 自动补；OnInit 日志打印实际路径 |
| LogHour / LogMinute | 14 / 45 | 每日定时日志时刻 |

---

## 10. 日志关键行速查

| 关键字 | 含义 |
|---|---|
| `entry OPEN` / `add OPEN` | 开仓/加仓成功（dir/lots/price/N/stop/units/equity） |
| `entry_stall OPEN` | STALL 并行入场成功（reason=entry_stall） |
| `stop CLOSE` / `exit_breakout CLOSE` | 2N 共享止损 / 反向突破出场平仓（含 pnl） |
| `OPEN_SKIP` | 手数不足跳过（按日节流） |
| `ORDER_SKIP` | 无效委托价（InvalidNumeric/≤0）拒发，按日节流（盘口未就绪/历史回放） |
| `OPEN_FAIL` / `CLOSE_FAIL` | 委托被拒，下一 bar 重试（可能被门禁拦） |
| `S1_SKIP` / `BREAKOUT_SKIP` | 系统1 跳过规则触发 |
| `STALL_FIRE` | STALL 边沿命中并尝试开仓（side/stall/long/short/N） |
| `STALL_MISS` | 边沿命中但未开仓：`reason=holding/pending/gated` |
| `STALL_EDGE` | DEBUG：边沿 OHLC 签名与 long/short 计数 |
| `ROLLOVER` | 主力合约切换（实盘；随后可能伴随台账重建） |
| `STATE_REBUILD` | 台账按平台持仓重建（重启/不一致/部分成交） |
| `LEDGER_CLEAN` | 空仓残留台账清理 |
| `DAILY` | 每日 14:45 快照（N/EL/XL/ES/XS/units/stop/equity/d1；有挂单时附 pend/ids） |
| `ORDER` / `FILL` | OnOrder/OnFill 回报（模拟/实盘；回测不触发） |
| `PEND_ADD` / `PEND_DEL` | 未完成委托集合增加/移除 |
| `ORDER_GATE` | 有未完成委托，拒发新单（按交易日首条） |
| `MAPPING` / `MAPPING_RECHECK` | 模拟/实盘挂图合法性（data0/data1/主连/MapToMain） |
| `日志:` | OnInit 打印 `LogDir` 原值、实际目录与文件名（确认路径是否生效） |
| `N/四通道刷新失败` | ERROR：data1 缺失/合约错位/历史不足/参数越界 → 拒绝开新仓与常规管理（实盘仍走 STOP_ONLY 止损兜底），逐 bar 重试 |
| `TRADE_GATE` | 未到 TradeStartDate，不会产生交易动作 |
| `EXIT fill=... order=... pending=...` | 停止策略单元时的事件计数汇总 |
| `=== 运行结束` | 回测末根 bar 期末对账（BarCnt/pos/contracts/lots/equity） |

---

## 11. 常见问题（FAQ）

- **Q：为什么 TB 的 STALL 比聚宽早？** A：聚宽在 09:01 才看到上一交易日日线；TB 在 data1 推进后的
  首根 1m（完成日线当晚 21:01）就评判。两者评判的是同一交易日的日线（价格口径可能因复权不同），
  但成交价/后续档位会不同。
- **Q：data1 挂 30 分钟，STALL 怎么算？** A：按 30 分钟 bar 计数，属于“30 分钟海龟 + 30 分钟 STALL”
  实验口径，与聚宽 v3.2（日线）不可比；上实盘前先复验 30m 的 Data1[0]/[1] 对齐。
- **Q：StallPeriod 能设多大？** A：默认 `NPeriod=20` 时最大 28；超限时 STALL 自动停用并记 ERROR，
  海龟部分仍运行。公式不会为 STALL 扩大 WinSize（会破坏 N 对拍）。
- **Q：新日线的 OHLC 与上一根完全相同，会漏触发吗？** A：会。签名不变 = 不认为出现新边沿；
  日线概率极低，30 分钟略高。可换 data1 周期或接受该边界。
- **Q：换月/复权跳变会误触发 STALL 吗？** A：`DailySym()|MainSymbol()` 变化时会重设基线，
  新基准的第一根只建立签名，不触发；换月后需等下一个日线边沿。
- **Q：重启/回放会补触发吗？** A：不会。首根只建立基线；持仓中达成也只记 MISS，不补。
- **Q：STALL 与突破同 bar，先跑哪个？** A：STALL 先；STALL 选中后本 bar 不再跑突破，
  即使 STALL 最终手数不足/被拒也不改跑突破。STALL 未命中时才走常规海龟检查。
- **Q：`DoOpenUnitMarket` 是交易所市价单吗？** A：不是。回测按当前 1m 收盘价，模拟/实盘按盘口
  对手价（无效回退收盘价），是“可成交限价”语义。若 OnOrder 正常回调，未成交会留在挂单集合，
  门禁会拦后续委托；当前没有撤单/超时。
- **Q：`ORDER_GATE` 一直出现、不再下单？** A：说明有委托未进入终态（OnOrder 漏报或柜台延迟）。
  先看 `PEND_ADD` 后是否有对应 `PEND_DEL`；当前需重启策略清空集合（OnInit/首 bar），
  实盘前应补撤单/超时。
- **Q：`MAPPING ok=False`，但图表看起来没问题？** A：逐项核对 `data0.Symbol` 与 `MainContSymbol`
  的大小写/后缀、data1 是否同合约、`MapToMain=True`；若 OnInit 时 data1 未就绪，看
  `MAPPING_RECHECK`；仍 False 时按 `MAP_FAIL` 行修正。
- **Q：日志里 SetMarginRate 等返回 False，设置没生效？** A：生效；v3.1 切片终值与聚宽按
  12%/0.000023/1 跳对拍一致可证。返回值语义未明，不代表设置失败。
- **Q：回测日志里 MainSymbol 显示“未来合约”？** A：平台行为——回测下 `MainSymbol()` 恒为当前
  实盘主力；换月检测/重建仅实盘生效。

---

## 12. 验证状态

- 代码：静态复查（花括号平衡、Global 声明、Data1 访问仅限访问器段、日志关键字、UTF-8+CRLF）；
  7 个聚宽 STALL 触发日已用 awk 按同口径复算全部命中。
- TB 侧：**编译已通过（2026-10-18）；v3.2 本体尚未实跑**（回测/模拟未执行）；第 6.3 节 TB 触发时点为预期值，不是实测值；
  第 5.2 节 v3.2 收益为聚宽实测基准，不是 TB 结果。
- 2026-10-19 日志路径自愈：新增 `EffectiveLogDir()`（末位缺 `/` 自动补）与 OnInit `日志:` 行；
  其中 `Right()` 来自官方函数摘要，**待下次 TB 编译确认**。
- 2026-10-19 无效价守卫：`CalcOrderPrice()` 先排除 `InvalidNumeric`；`DoSendOrder()` 入口对
  `InvalidNumeric`/`<=0` 拒发并记 `ORDER_SKIP`（每交易日首条）。修复模拟历史回放时
  `OPEN_FAIL price=N/A`、TBCore `trade Error Buy Price=1.79e308` 刷屏；**待编译/模拟复验**。
- 事件链探针（RateCheckProbe，2026-10-19，TB 期货模拟 p2701，1 手开平）：
  OnOrder 回调 1→2→5，OnFill 的 side/combOffset/volume/price/fillPrice 有效；
  MarketPosition 下单即翻转、账户持仓成交后更新。结论仅限模拟盘，实盘柜台未验证。
- 首轮 TB 验收顺序：
  1. 跑首轮回测时检查 `data1.Symbol`、`MAPPING ok=True`、`STALL: enabled=True need=25`；
  2. `StallPeriod=0` 跑切片 4/5/6，与 v3.1 的 trades/DAILY 对齐；
  3. 跑第 6.3 节 7 个触发日（单合约切片），核对信号日/方向；触发时点应落在预期边沿 ±1 根 1m；
  4. 边界：持仓中边沿 → `STALL_MISS reason=holding`；有挂单 → `reason=pending`；
     STALL 与突破同 bar → STALL 优先；重启/回放 → 首边沿只建基线；
  5. 模拟：按第 7 节核对 PEND_ADD/PEND_DEL、FILL、DAILY、ROLLOVER。
- 首轮跑通后如需回归基线，再按 `tbquant/tools/README.md` 流程落 `baselines/slice4-6`；
  本次不预造基线。

---

*版本：v3.2-TB（2026-10-17 · 2026-10-18 编译通过 · 2026-10-19 回调实证 + 日志路径自愈）· 对应 `tbquant/05-PTrend-turtle-v3.2-TB.txt` · 聚宽 v3.2 · 前版 v3.1-TB*
