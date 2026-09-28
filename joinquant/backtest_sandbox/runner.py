# -*- coding: utf-8 -*-
"""
本地逐分钟回放引擎（沙盒）
加载 joinquant/ 下的策略文件（只读，不做任何修改），用 jqdata.py 模拟层在本地运行：

    python joinquant/backtest_sandbox/runner.py \
        --strategy joinquant/04-RubberTrendPullback-v1.0.py \
        --data local_data/export_ru --start 2020-01-01 --end 2026-09-28 --out out.log
    # 棕榈油：--data local_data/export_p --tick 2 --param symbol=P --param exchange=XDCE

事件语义（对齐聚宽期货回测）：
- 交易日 D 的日盘：每个分钟 bar 触发 every_bar（策略内 14:45 判定即来源于此）
- 交易日 D 的夜盘（日历日 D 的 21:00~23:00，归属次一交易日）：仅触发显式注册的 run_daily 任务
- every_bar 不覆盖夜盘（平台实测特性，策略注释亦如此说明）
"""
import argparse
import bisect
import datetime as dt
import importlib.util
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jqdata  # noqa: E402


def parse_args():
    ap = argparse.ArgumentParser(description='沙盒回放引擎')
    ap.add_argument('--strategy', default=os.path.join(HERE, '..', '04-RubberTrendPullback-v1.0.py'))
    ap.add_argument('--data', default=os.path.join(HERE, '..', '..', 'local_data', 'export_ru'))
    ap.add_argument('--start', default='2020-01-01')
    ap.add_argument('--end', default='2026-09-28')
    ap.add_argument('--cash', type=float, default=1_000_000)
    ap.add_argument('--tick', type=float, default=5.0)
    ap.add_argument('--multiplier', type=float, default=10)
    ap.add_argument('--out', default=os.path.join(HERE, 'out.log'))
    ap.add_argument('--param', action='append', default=[],
                    help='运行时覆盖策略 PARAMS（k=v，可重复；策略文件保持零修改），如 --param symbol=P --param exchange=XDCE')
    ap.add_argument('--debug', action='store_true', help='策略日志级别设为 debug（打印 ENTRY_REJECT 等判定细节）')
    return ap.parse_args()


def _coerce(v):
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def main():
    args = parse_args()
    strategy_path = os.path.abspath(args.strategy)
    data_root = os.path.abspath(args.data)
    out_log = os.path.abspath(args.out)

    store = jqdata._configure(data_root, out_log, cash=args.cash, tick=args.tick,
                              multiplier=args.multiplier)
    ctx = jqdata.context

    # ---- 加载策略（只读） ----
    spec = importlib.util.spec_from_file_location('strategy_under_test', strategy_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for kv in args.param:
        k, v = kv.split('=', 1)
        mod.PARAMS[k] = _coerce(v)
    if args.param:
        print(f'[sandbox] PARAMS override: { {kv.split("=", 1)[0]: _coerce(kv.split("=", 1)[1]) for kv in args.param} }')

    # ---- 初始化（聚宽在回测起始日 00:00 调用 initialize） ----
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    ctx.current_dt = dt.datetime.combine(start, dt.time(0, 0))
    ctx.current_date = start
    ctx.previous_date = None
    try:
        mod.initialize(ctx)
    except Exception:
        traceback.print_exc()
    if args.debug:
        jqdata.log.set_level('strategy', 'debug')

    every, timed = jqdata._schedule()
    day_labels, night_labels = jqdata._session_grid()
    night_bar_set = set(night_labels)

    trade_days = store.trade_days
    days = [d for d in trade_days if start <= d <= end]
    errors = []

    def prev_trade_day(d):
        i = bisect.bisect_left(trade_days, d)
        return trade_days[i - 1] if i > 0 else None

    def fire(t, prev, is_day_bar):
        ctx.current_dt = t
        ctx.current_date = t.date()
        ctx.previous_date = prev
        try:
            if is_day_bar:
                for f in every:
                    f(ctx)
            for f in timed.get(t.strftime('%H:%M'), ()):
                f(ctx)
        except Exception:
            errors.append((t, traceback.format_exc()))

    n_events = 0
    for k, d in enumerate(days):
        prev = prev_trade_day(d)
        # 日盘：每个 bar 触发 every_bar（同时触发该分钟的定时任务，如有）
        for label in day_labels:
            h, m = label.split(':')
            fire(dt.datetime.combine(d, dt.time(int(h), int(m))), prev, True)
            n_events += 1
        # 夜盘：21:00 开盘时刻（无 bar）+ 21:01~23:00 各分钟 bar；仅触发显式注册的定时任务
        for label in ['21:00'] + night_labels:
            h, m = label.split(':')
            fire(dt.datetime.combine(d, dt.time(int(h), int(m))), prev, False)
            n_events += 1
        if (k + 1) % 200 == 0:
            print(f'[sandbox] {k + 1}/{len(days)} days...', file=sys.stderr, flush=True)

    # ---- 汇总 ----
    acct = jqdata._account
    print(f'[sandbox] strategy={strategy_path}')
    print(f'[sandbox] events={n_events} days={len(days)} fills={len(acct.fills)} '
          f'errors={len(errors)}')
    print(f'[sandbox] final_equity={acct.total_value():.2f} realized={acct.realized:.2f} '
          f'fees={acct.fees:.2f} open={ {k: v for k, v in acct.pos.items()} }')
    print(f'[sandbox] log -> {out_log}')
    for t, tb in errors[:5]:
        print(f'[sandbox][ERROR] {t}\n{tb}', file=sys.stderr)
    return 0 if not errors else 1


if __name__ == '__main__':
    sys.exit(main())
