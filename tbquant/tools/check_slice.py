# -*- coding: utf-8 -*-
"""切片回归检查：TBQuant .tbf 导出文本 与 基准 的逐行比对。

用法（在仓库根目录执行；导出文本用 tbquant/tools/read_tbf.py 从 .tbf 生成）:

  # 1) 生成基准（从一次已“验明正身”的运行导出）
  python tbquant/tools/check_slice.py make <export.txt> -d tbquant/baselines/slice4

  # 2) 回归检查（新跑一次并导出后；通过 exit 0，有差异 exit 1 并列出明细）
  python tbquant/tools/check_slice.py check <new_export.txt> -d tbquant/baselines/slice4

比对规则:
  严格比对（(时间戳, 消息体) 逐字全等）: 交易行(OPEN/CLOSE/SKIP)、DAILY 行、结束行、未识别行
  参数A/B/C: 只比消息体（时间戳=运行日，跨日运行会变，不参与比对）
  豁免:     其余头部行（00:00:00 时间戳）：图层/合约/模式/设置 等排版与环境字段
            —— StrategyMode(SM 0/4) 与 SetMarginRate 返回(True/False) 同切片两次
               运行不稳定（实证）；表头文案随公式版本演进；.tbf 表名 GUID 每会话随机。
  软豁免:   基准目录下 soft_exempt.txt，每行一个正则（# 注释；对 "<时间戳> <消息体>"
            整行 re.search）。命中行不参与严格比对，值差异仅提示不判 FAIL；
            命中行数不一致仍判 FAIL（图表范围可能变化）。文案类问题避开软豁免、
            用头部豁免机制解决。
"""
import argparse
import io
import os
import re
import sys

LVL = re.compile(r'^\[(?:Info|Warn|Debug|Error)\]\s*')
DAILY = re.compile(
    r'^DAILY (\S+) N=(\S+) EL=(\S+) XL=(\S+) ES=(\S+) XS=(\S+) '
    r'units=(\S+) lots=(\S+) stop=(\S+) equity=(\S+) win=(\S+) d1=(\S+)$')


def load(path):
    rows = []
    for line in io.open(path, encoding='utf-8', errors='replace'):
        line = line.rstrip('\n')
        if line.startswith('###') or line.startswith('FileAppend |'):
            continue
        parts = line.split(' | ', 2)
        if len(parts) == 3:
            rows.append((parts[1].strip(), LVL.sub('', parts[2])))
    return rows


def classify(ts, msg):
    if '运行结束' in msg:
        return 'final'
    if msg.startswith('DAILY '):
        return 'daily'
    if any(k in msg for k in ('OPEN', 'CLOSE', 'SKIP')):
        return 'trade'
    if msg.startswith(('参数A:', '参数B:', '参数C:')):
        return 'params'
    if '00:00:00' in ts:
        return 'header'
    return 'other'


def split_classes(rows):
    out = {}
    for ts, msg in rows:
        out.setdefault(classify(ts, msg), []).append((ts, msg))
    return out


def load_soft_patterns(base_dir):
    path = os.path.join(base_dir, 'soft_exempt.txt')
    pats = []
    if os.path.exists(path):
        for line in io.open(path, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#'):
                pats.append(line)
    return pats


def is_soft(item, pats):
    ts, msg = item
    line = '%s %s' % (ts, msg)
    return any(re.search(p, line) for p in pats)


def compare(base, new, soft_pats):
    diffs = []
    notes = []
    for cls in ('trade', 'daily', 'params', 'final', 'other'):
        a_all = base.get(cls, [])
        b_all = new.get(cls, [])
        a_soft = [x for x in a_all if is_soft(x, soft_pats)]
        b_soft = [x for x in b_all if is_soft(x, soft_pats)]
        a = [x for x in a_all if not is_soft(x, soft_pats)]
        b = [x for x in b_all if not is_soft(x, soft_pats)]
        if len(a_soft) != len(b_soft):
            diffs.append('类 %s: 软豁免行数不一致（基准 %d vs 新 %d，图表范围可能变化）'
                         % (cls, len(a_soft), len(b_soft)))
        else:
            diff_n = 0
            for x, y in zip(a_soft, b_soft):
                if x != y:
                    diff_n += 1
                    notes.append('  %s 软豁免差异 #%d 基准: %s' % (cls, diff_n, x))
                    notes.append('  %s 软豁免差异 #%d 新  : %s' % (cls, diff_n, y))
            if a_soft or b_soft:
                print('[check_slice] %-6s 软豁免 %d 条（值差异 %d，仅提示不判 FAIL）'
                      % (cls, len(a_soft), diff_n))
        if cls == 'params':  # 时间戳=运行日（跨日会变），只比消息体
            a = [m for _, m in a]
            b = [m for _, m in b]
        if a == b:
            print('[check_slice] %-6s %3d 条一致' % (cls, len(a)))
            continue
        mis = sum(1 for i in range(max(len(a), len(b)))
                  if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None))
        diffs.append('类 %s: 基准 %d 条 vs 新 %d 条，共 %d 处不一致' % (cls, len(a), len(b), mis))
        shown = 0
        for i in range(max(len(a), len(b))):
            x = a[i] if i < len(a) else None
            y = b[i] if i < len(b) else None
            if x != y:
                if shown < 10:
                    shown += 1
                    diffs.append('  #%d 基准: %s' % (i + 1, x))
                    diffs.append('  #%d 新  : %s' % (i + 1, y))
    print('[check_slice] header 豁免 %d/%d 行（SM/SetMarginRate/排版等，不参与比对）'
          % (len(base.get('header', [])), len(new.get('header', []))))
    if soft_pats:
        print('[check_slice] 软豁免模式 %d 条（soft_exempt.txt）' % len(soft_pats))
    return diffs, notes


def make_baseline(export, out_dir, soft=None):
    rows = load(export)
    cls = split_classes(rows)
    os.makedirs(out_dir, exist_ok=True)
    with io.open(os.path.join(out_dir, 'raw_export.txt'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('### 基准原始导出快照（来源：%s），格式与 .tbf 导出一致；比对源\n'
                 % os.path.basename(export))
        for i, (ts, msg) in enumerate(rows):
            fh.write('%d | %s | %s\n' % (i, ts, msg))
    with io.open(os.path.join(out_dir, 'trades.txt'), 'w', encoding='utf-8', newline='\n') as fh:
        for ts, msg in cls.get('trade', []):
            fh.write('%s | %s\n' % (ts, msg))
    with io.open(os.path.join(out_dir, 'daily.csv'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('date,ts,contract,N,EL,XL,ES,XS,units,lots,stop,equity,win,d1\n')
        for ts, msg in cls.get('daily', []):
            m = DAILY.match(msg)
            if not m:
                continue
            fh.write('%s,%s,%s\n' % (ts.split(' ')[0], ts, ','.join(m.groups())))
    with io.open(os.path.join(out_dir, 'summary.txt'), 'w', encoding='utf-8', newline='\n') as fh:
        for _, msg in cls.get('params', []):
            fh.write('%s\n' % msg)
        for ts, msg in cls.get('final', []):
            fh.write('%s | %s\n' % (ts, msg))
    soft_path = os.path.join(out_dir, 'soft_exempt.txt')
    if soft:
        with io.open(soft_path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write('# 软豁免正则（对 "<时间戳> <消息体>" 整行 re.search）：命中行不参与严格比对，\n'
                     '# 值差异仅提示不判 FAIL；命中行数不一致仍判 FAIL。\n')
            for p in soft:
                fh.write('%s\n' % p)
    elif os.path.exists(soft_path):
        print('[check_slice] 保留已有 soft_exempt.txt（%d 条模式）' % len(load_soft_patterns(out_dir)))
    print('[check_slice] 基准已生成 -> %s' % out_dir)
    print('  raw_export.txt  %d 行（比对源）' % len(rows))
    print('  trades.txt      %d 条' % len(cls.get('trade', [])))
    print('  daily.csv       %d 行' % len(cls.get('daily', [])))
    print('  summary.txt     参数A/B/C + 结束行')
    if soft:
        print('  soft_exempt.txt %d 条软豁免模式' % len(soft))


def check(export, base_dir):
    base_raw = os.path.join(base_dir, 'raw_export.txt')
    if not os.path.exists(base_raw):
        print('[check_slice] 找不到基准: %s' % base_raw, file=sys.stderr)
        return 2
    base = split_classes(load(base_raw))
    new = split_classes(load(export))
    soft_pats = load_soft_patterns(base_dir)
    print('[check_slice] 基准: %s' % base_raw)
    print('[check_slice] 新跑: %s' % export)
    diffs, notes = compare(base, new, soft_pats)
    if notes:
        print('[check_slice] 软豁免明细（不判 FAIL）:')
        for n in notes:
            print(n)
    if diffs:
        print('[check_slice] FAIL — 与基准不一致:')
        for d in diffs:
            print('  ' + d)
        return 1
    tail = '（软豁免 %d 处值差异仅提示）' % (len(notes) // 2) if notes else ''
    print('[check_slice] OK — 与基准完全一致（交易/DAILY/参数/结束行）%s' % tail)
    return 0


def main():
    ap = argparse.ArgumentParser(description='切片回归检查（TBQuant 导出文本 vs 基准）')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p1 = sub.add_parser('make', help='从导出文本生成基准')
    p1.add_argument('export', help='.tbf 导出的文本')
    p1.add_argument('-d', '--dir', required=True, help='基准目录，如 tbquant/baselines/slice4')
    p1.add_argument('-s', '--soft', action='append', default=[],
                    help='软豁免正则（可多次；写入基准目录 soft_exempt.txt）')
    p2 = sub.add_parser('check', help='回归检查')
    p2.add_argument('export', help='.tbf 导出的文本')
    p2.add_argument('-d', '--dir', required=True, help='基准目录')
    args = ap.parse_args()
    if args.cmd == 'make':
        make_baseline(args.export, args.dir, args.soft)
        return 0
    return check(args.export, args.dir)


if __name__ == '__main__':
    sys.exit(main())
