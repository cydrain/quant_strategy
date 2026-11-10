# -*- coding: utf-8 -*-
"""
并行批量回测：把一个或多个切片任务分发到多个 runner.py 子进程。

任务格式（--task "name start end [k=v ...]"，k=v 透传为 --param，可重复 --task）：
    python joinquant/backtest_sandbox/run_batch.py \
        --strategy joinquant/05-PTrend-turtle-v3.2.py \
        --data local_data/export_p --tick 1 --jobs 6 \
        --outdir joinquant/logs/batch_20261110 \
        --task "slice1 2020-03-13 2021-01-31 fixed_dir=1" \
        --task "slice2 2021-06-01 2022-03-31 fixed_dir=1" \
        --task "slice3 2022-05-01 2022-10-31 fixed_dir=-1" \
        --task "slice4 2024-02-01 2024-04-26 fixed_dir=1" \
        --task "slice5 2024-08-01 2024-12-30 fixed_dir=1" \
        --task "slice6 2025-01-01 2025-03-10 fixed_dir=1"

输出：<outdir>/<name>.log、<name>_equity.csv、<name>.stdout；结束时打印汇总表。
"""
import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))

_EQUITY_RE = re.compile(r'final_equity=([\d.]+)\s+final_return=([+-]?[\d.]+)')
_RET_RE = re.compile(r'final_return=[+-]?[\d.]+\s+\(([+-]?[\d.]+)%\)')
_STAT_RE = re.compile(r'fills=(\d+)\s+errors=(\d+)')


def parse_args():
    ap = argparse.ArgumentParser(description='并行批量回测（多进程调用 runner.py）')
    ap.add_argument('--strategy', required=True)
    ap.add_argument('--data', required=True)
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--task', action='append', required=True,
                    help='name start end [k=v ...]（k=v 透传为 --param，可重复 --task）')
    ap.add_argument('--jobs', type=int, default=4, help='并行任务数（默认 4）')
    ap.add_argument('--tick', default='1')
    ap.add_argument('--multiplier', default='10')
    ap.add_argument('--cash', default='1000000')
    ap.add_argument('--debug', action='store_true')
    return ap.parse_args()


def _parse_task(s):
    parts = s.split()
    if len(parts) < 3:
        raise SystemExit(f'--task 格式错误: {s!r}（需 name start end [k=v ...]）')
    return parts[0], parts[1], parts[2], parts[3:]


def run_one(args, task):
    name, start, end, params = _parse_task(task)
    log = os.path.join(args.outdir, f'{name}.log')
    eq = os.path.join(args.outdir, f'{name}_equity.csv')
    cmd = [sys.executable, os.path.join(HERE, 'runner.py'),
           '--strategy', args.strategy, '--data', args.data,
           '--start', start, '--end', end,
           '--tick', str(args.tick), '--multiplier', str(args.multiplier),
           '--cash', str(args.cash), '--out', log, '--equity-out', eq]
    for p in params:
        cmd += ['--param', p]
    if args.debug:
        cmd.append('--debug')
    t0 = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True, text=True)
    wall = time.perf_counter() - t0
    with open(os.path.join(args.outdir, f'{name}.stdout'), 'w', encoding='utf-8') as fh:
        fh.write(r.stdout)
        fh.write(r.stderr)
    m_eq = _EQUITY_RE.search(r.stdout)
    m_ret = _RET_RE.search(r.stdout)
    m_st = _STAT_RE.search(r.stdout)
    return {'name': name, 'start': start, 'end': end, 'rc': r.returncode,
            'equity': float(m_eq.group(1)) if m_eq else None,
            'ret_pct': float(m_ret.group(1)) if m_ret else None,
            'fills': int(m_st.group(1)) if m_st else None,
            'errors': int(m_st.group(2)) if m_st else None,
            'wall': wall}


def main():
    t_start = time.perf_counter()
    args = parse_args()
    names = [t.split()[0] for t in args.task]
    if len(set(names)) != len(names):
        raise SystemExit(f'任务名重复: {names}')
    os.makedirs(args.outdir, exist_ok=True)
    print(f'[batch] {len(args.task)} tasks, jobs={args.jobs}, outdir={args.outdir}', flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        results = list(ex.map(lambda t: run_one(args, t), args.task))
    print(f'{"name":<12}{"start":<12}{"end":<12}{"equity":>14}{"return":>10}'
          f'{"fills":>7}{"errors":>8}{"wall":>8}')
    for r in results:
        eq = f'{r["equity"]:,.2f}' if r['equity'] is not None else '-'
        ret = f'{r["ret_pct"]:+.2f}%' if r['ret_pct'] is not None else '-'
        print(f'{r["name"]:<12}{r["start"]:<12}{r["end"]:<12}{eq:>14}{ret:>10}'
              f'{str(r["fills"]):>7}{str(r["errors"]):>8}{r["wall"]:>7.1f}s')
    total_eq = sum(r['equity'] for r in results if r['equity'] is not None)
    print(f'[batch] equity_sum={total_eq:,.2f} real_wall={time.perf_counter() - t_start:.1f}s')
    bad = [r for r in results if r['rc'] != 0 or r['errors']]
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
