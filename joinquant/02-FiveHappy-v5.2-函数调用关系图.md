# 【五福闹新春】v5.2 函数调用关系图

来源: `joinquant/02-FiveHappy-v5.2.py`

## 1. 整体调度（initialize 中注册的定时任务）

```mermaid
flowchart TD
    INIT["initialize(context)<br/>初始化参数 & 注册定时任务"]

    INIT --> M1["run_daily 09:00<br/>morning_routine"]
    INIT --> M2["run_daily 09:40<br/>check_weak_period_daily"]
    INIT --> M3["run_daily 13:10<br/>afternoon_routine"]
    INIT --> M4["run_daily 13:10<br/>sell_routine"]
    INIT --> M5["run_daily 13:10<br/>buy_routine"]
    INIT --> M6["run_daily 15:10<br/>reset_daily_flags"]
    INIT --> M7["run_daily every_bar<br/>minute_level_stop_loss"]

    subgraph 晨间[09:00 晨间流水线]
        M1 --> A1["check_positions"]
        M1 --> A2["monitor_drawdown"]
        M1 --> A3["calculate_global_etf_threshold"]
    end

    subgraph 早盘[09:40 走弱期判断 + 池子更新]
        M2 --> B1["check_a_share_weak_period"]
        M2 --> B2["midday_routine"]
    end

    subgraph 午盘[13:10 动量计算与排序]
        M3 --> C1["calculate_and_log_ranked_etfs"]
    end

    subgraph 卖出[13:10 卖出流水线]
        M4 --> D1["execute_sell_trades"]
    end

    subgraph 买入[13:10 买入流水线]
        M5 --> E1["execute_buy_trades"]
    end

    subgraph 收盘[15:10 收盘流水线]
        M6 --> F1["reset_daily_flags"]
    end

    subgraph 风控[盘中 每分钟级止损]
        M7 --> G1["smart_order_target_value(security, 0)"]
    end
```

## 2. 池子构建链路（09:40 早盘流水线）

```mermaid
flowchart TD
    MID["midday_routine"] --> WEAK{"is_a_share_weak<br/>走弱期?"}

    WEAK -- "是 🔴" --> FGP["filter_global_pool_by_volume<br/>仅过滤全球/海外池"]
    WEAK -- "否 🟢" --> USP["update_sector_pool<br/>全市场动态池(前300只)"]
    USP -. 阈值未初始化 .-> THRESH["calculate_global_etf_threshold<br/>全市场日均成交额/20000"]
    FGP -. 阈值未初始化 .-> THRESH

    USP --> USP1["遍历全市场ETF<br/>按名称分类: 排除/普通/特别组"]
    USP1 --> USP2["filter_by_liquidity(嵌套)<br/>近3日日均成交额>阈值"]
    USP2 --> USP3["clean_name(嵌套)<br/>去基金公司/噪声词"]
    USP3 --> USP4["按行业取成交额最高1只<br/>合并取前300"]

    WEAK -- "否" --> FFP["filter_fixed_pool_by_volume<br/>固定池(全球+中国)流动性过滤"]
    FFP --> DM["daily_merge_etf_pools<br/>固定池 ∪ 动态池 → merged_etf_pool"]

    FGP --> GOUT["filtered_global_pool"]
    USP --> DOUT["dynamic_etf_pool"]
    FFP --> FOUT["filtered_fixed_pool"]

    GOUT --> DM
    DOUT --> DM
    FOUT --> DM

    DM --> MERGED["g.merged_etf_pool（最终交易池）"]
```

## 3. 选股 + 调仓链路（13:10 午盘/卖出/买入）

```mermaid
flowchart TD
    AFTER["afternoon_routine"] --> CR["calculate_and_log_ranked_etfs"]
    CR --> FR["get_final_ranked_etfs"]

    FR --> FR0["拉取日线/分钟线数据<br/>剔除停牌/无效日"]
    FR0 --> LOOP["遍历每只ETF"]

    LOOP --> METRICS["calculate_all_metrics_for_etf<br/>单只ETF五项指标"]
    METRICS --> MS["calculate_momentum_score<br/>加权对数回归 → 动量分/R²"]
    METRICS --> VR["get_volume_ratio<br/>当日量能外推 → 量比"]
    METRICS --> LOSS["近3日单日跌幅风控"]
    METRICS --> MA["均线位置过滤"]
    METRICS --> R2["R² 趋势质量过滤"]

    LOOP --> ALL["all_metrics（全池指标列表）"]
    ALL --> SORT1["按动量得分降序排序"]
    SORT1 --> LOG1["打印前100只详细指标"]

    SORT1 --> AF["apply_filters<br/>5道过滤(按开关, R²/均线互斥切换)"]
    AF --> TOP10["取前10名"]

    TOP10 --> STEP3["候选池划线<br/>第N名得分×比例(走弱期×1.0)"]
    STEP3 --> STEP4["结合持仓调整<br/>已持有且仍在候选池 → 优先保留"]
    STEP4 --> FINAL["final_result（最终目标列表）"]

    FINAL --> G1["g.ranked_etfs_result"]
    G1 --> SELL["execute_sell_trades"]
    G1 --> BUY["execute_buy_trades"]

    SELL --> TARGET["确定目标: 排名前N<br/>无候选→防御ETF/空仓"]
    SELL --> SELL1["卖: 持仓中不在目标集合的"]
    SELL1 --> SMART["smart_order_target_value<br/>智能下单: 停牌/涨跌停/T+1/最小金额"]

    BUY --> BUY1["买: 目标中未持有的<br/>动态分配剩余现金"]
    BUY1 --> SMART

    SMART --> SUSPEND["is_temporarily_suspended<br/>近10分钟无成交=临时停牌"]
```

## 4. 大盘走弱期状态机（check_a_share_weak_period）

```mermaid
stateDiagram-v2
    [*] --> 正常期: 初始化 is_a_share_weak=False

    正常期 --> 走弱期: ≥3/4指数跌破MA10 (below_count≥3)
    走弱期 --> 正常期: ≥3/4指数站上MA10 (above_count≥3)
    走弱期 --> 走弱期: 持续天数 ≥ max_weak_days(20) → 强制退出(不在此状态, 见下)
    走弱期 --> 正常期: 持续天数≥20 强制退出

    note right of 正常期
        过滤策略: 启用R²过滤(>0.4), 禁用均线过滤
        候选线: 第N名得分×0.9
    end note

    note right of 走弱期
        池子: 仅用全球/海外ETF池
        过滤策略: 禁用R², 启用均线过滤
        候选线: 第N名得分×1.0(严格)
        目标为空 → 防御ETF(511880)或空仓
    end note
```

## 5. 全局状态变量（g.xxx 数据流）

```mermaid
flowchart LR
    subgraph 池子
        GP["global_etf_pool<br/>固定全球池(17只)"]
        CP["china_etf_pool<br/>固定中国池(100+只)"]
        FP["fixed_etf_pool = 全球+中国"]
        FGP2["filtered_global_pool"]
        FFP2["filtered_fixed_pool"]
        DYP["dynamic_etf_pool(≤300)"]
        MP["merged_etf_pool"]
    end

    subgraph 结果
        RR["ranked_etfs_result"]
        TT["target_etfs_list"]
    end

    GP --> FP
    CP --> FP
    FP --> FFP2
    FGP2 --> MP
    FFP2 --> MP
    DYP --> MP
    MP --> RR
    RR --> TT
```

## 6. 函数清单

| 函数 | 行号 | 职责 | 被谁调用 |
|---|---|---|---|
| `initialize` | 12 | 参数/池子/定时任务初始化 | 聚宽入口 |
| `morning_routine` | 241 | 晨间: 持仓检查+回撤+阈值 | run_daily 09:00 |
| `check_weak_period_daily` | 236 | 走弱期判断+池更新 | run_daily 09:40 |
| `midday_routine` | 253 | 池子构建总调度 | check_weak_period_daily |
| `afternoon_routine` | 272 | 午盘: 动量计算 | run_daily 13:10 |
| `sell_routine` | 287 | 卖出流水线 | run_daily 13:10 |
| `buy_routine` | 292 | 买入流水线 | run_daily 13:10 |
| `reset_daily_flags` | 298 | 清缓存 | run_daily 15:10 |
| `check_positions` | 304 | 打印持仓状态 | morning_routine |
| `monitor_drawdown` | 315 | 回撤监控记录 | morning_routine |
| `calculate_global_etf_threshold` | 346 | 全市场流动性阈值 | morning_routine / filter_* |
| `filter_global_pool_by_volume` | 381 | 全球池流动性过滤 | midday_routine(走弱期) |
| `update_sector_pool` | 423 | 动态池构建(分类+行业) | midday_routine(正常期) |
| `filter_fixed_pool_by_volume` | 626 | 固定池流动性过滤 | midday_routine(正常期) |
| `daily_merge_etf_pools` | 667 | 合并池 | midday_routine(正常期) |
| `calculate_and_log_ranked_etfs` | 677 | 动量计算入口 | afternoon_routine |
| `calculate_momentum_score` | 686 | 加权对数回归打分 | calculate_all_metrics_for_etf |
| `calculate_all_metrics_for_etf` | 713 | 单ETF五项指标 | get_final_ranked_etfs |
| `get_volume_ratio` | 765 | 当日量能外推量比 | calculate_all_metrics_for_etf |
| `check_a_share_weak_period` | 788 | 走弱期状态机 | check_weak_period_daily |
| `apply_filters` | 863 | 5道条件过滤 | get_final_ranked_etfs |
| `get_final_ranked_etfs` | 878 | 选股主流程(4步) | calculate_and_log_ranked_etfs |
| `execute_sell_trades` | 1031 | 卖出执行 | sell_routine |
| `execute_buy_trades` | 1067 | 买入执行 | buy_routine |
| `is_temporarily_suspended` | 1115 | 临时停牌检测 | smart_order / 选股 |
| `smart_order_target_value` | 1143 | 智能下单(风控6道) | 卖出/买入/止损 |
| `minute_level_stop_loss` | 1216 | 分钟级固定止损 | run_daily every_bar |
| `get_security_name` | 1240 | 名称解析 | 各处 |
| `check_defensive_etf_available` | 1249 | 防御ETF可用性 | execute_sell_trades |
| `trade` | 1264 | 空实现 | 未使用 |

## 7. 关键设计点

1. **时间线**：09:00 阈值 → 09:40 池子 → 13:10 计算+卖+买 → 15:10 重置，每分钟止损
2. **弱市切换**：走弱期只用全球池、R²过滤换均线过滤、候选线严格化、可切防御ETF
3. **低换手**：已持有且在候选池的 ETF 优先保留，避免每天来回切换
4. **下单风控**：全天停牌/临时停牌/涨跌停/T+1/最小金额 6 道检查
