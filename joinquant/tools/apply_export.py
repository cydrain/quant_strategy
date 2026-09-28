# -*- coding: utf-8 -*-
"""本地安装重刷产物：解压 zip（如需）→ 全合约缺日审计 + 主力期覆盖校验（RU / P 通用）

用法（仓库根目录）：
    python joinquant/tools/apply_export.py
前置：export_ru.zip / export_p.zip（研究环境重刷产出）已放到 local_data/（或已解压为对应文件夹）
说明：沙盒直接以 local_data/export_ru、local_data/export_p 作为数据目录使用
"""
import glob
import os
import shutil
import zipfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', 'local_data'))


def install(folder):
    """确保 local_data/<folder>/ 为最新：有 zip 且比已解压目录新 → 覆盖式重解压"""
    src = os.path.join(ROOT, folder)
    zp = os.path.join(ROOT, folder + '.zip')
    if os.path.exists(zp):
        need = True
        if os.path.isdir(src) and os.path.exists(os.path.join(src, 'trade_days.csv')):
            if os.path.getmtime(os.path.join(src, 'trade_days.csv')) >= os.path.getmtime(zp):
                need = False
        if need:
            if os.path.isdir(src):
                shutil.rmtree(src)
            with zipfile.ZipFile(zp) as z:
                z.extractall(ROOT)
            print(f'已解压 {folder}.zip（覆盖旧目录）')
    if not os.path.isdir(src):
        print(f'缺少 {folder}/ 或 {folder}.zip')
        return None
    return src


def audit(src, tag):
    td = set(pd.to_datetime(pd.read_csv(os.path.join(src, 'trade_days.csv'))['date']).dt.date)
    files = sorted(glob.glob(os.path.join(src, 'min1_*.csv')))
    cover, bad = {}, 0
    for f in files:
        c = os.path.basename(f)[5:-4]
        m = pd.read_csv(f, index_col=0, parse_dates=True)
        have = set(m.index.date)
        cover[c] = have
        lo, hi = min(have), max(have)
        miss = sorted({d for d in td if lo <= d <= hi} - have)
        bad += len(miss)
        extra = ''
        vol = m.groupby(m.index.date)['volume'].sum()
        flat = [str(d) for d, v in vol.items() if v == 0]
        nz = vol[vol > 0]
        if flat and len(nz):
            extra = (f'  | 零成交填充日 {len(flat)} 天 {flat[:4]}{" ..." if len(flat) > 4 else ""}'
                     f' | 有效成交区间 {nz.index.min()}..{nz.index.max()}')
        print(f'[{tag}][{"!!" if miss else "OK"}] {c}: {lo}..{hi} {len(m)} 行, 缺 {len(miss)} 天 '
              f'{[str(d) for d in miss[:6]]}{extra}')
    dom = pd.read_csv(os.path.join(src, 'dominant.csv'))
    dom['date'] = pd.to_datetime(dom['date']).dt.date
    segs, prev = [], None
    for d, c in zip(dom['date'], dom['dominant']):
        if c != prev:
            segs.append([d, d, c]); prev = c
        else:
            segs[-1][1] = d
    bad2 = 0
    for s, e, c in segs:
        code = (c or '').split('.')[0]
        if code not in cover:
            print(f'!! {tag} 主力段 {s}..{e} 合约 {c} 无分钟文件')
            bad2 += 1
            continue
        need = {d for d in td if s <= d <= e}
        miss = sorted(need - cover[code])
        if miss:
            print(f'!! {tag} 主力段 {s}..{e} {code} 缺 {len(miss)} 天: {[str(x) for x in miss[:6]]}')
            bad2 += len(miss)
    print(f'--- {tag}: {len(files)} 个合约，内部缺日 {bad} 天，'
          f'主力期覆盖 {"全部通过" if bad2 == 0 else f"缺 {bad2} 天"}')
    return bad, bad2


def main():
    ok = True
    for folder, tag in [('export_ru', 'RU'), ('export_p', 'P')]:
        src = install(folder)
        if src is None:
            ok = False
            continue
        bad, bad2 = audit(src, tag)
        ok = ok and bad == 0 and bad2 == 0
    print('两个数据集审计通过，可以重跑回测/校准' if ok else '存在缺口，见上方 !! 行')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
