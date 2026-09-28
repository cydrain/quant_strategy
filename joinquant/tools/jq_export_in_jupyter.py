# -*- coding: utf-8 -*-
"""在聚宽【研究环境】里整段运行：导出本地沙盒所需数据（RU / 棕榈油 P 同一套逻辑）

任务（两个品种同区间、同逻辑，均强制重刷）：
  RU（上期所）与棕榈油 P（大商所）：2020-01-01 ~ 2026-09-28 区间内的**全部主力合约**
  （自动解析逐交易日主力 → 逐个合约导出；修复首版按月拉取吞"月末交易日"的缺日问题）

每个合约导出：日线（自 2019-08-01 起，含预热）+ 1 分钟线（交割月前 14 个月起、不早于 2019-12-01，含夜盘）
分钟线拉取：先整段拉（快）→ 交易日历逐日校验 → 缺日逐日补拉（最多 3 轮）；整段异常则改按月分片
外加：交易日历（自 2019-08-01 起）、逐交易日主力映射（与回测同源 get_dominant_future）
运行完请下载 export_ru.zip 与 export_p.zip（两个都要）
"""
import datetime
import os
import shutil

import pandas as pd

from jqdata import *

FIELDS_1M = ['open', 'high', 'low', 'close', 'volume']


def month_ranges(start_ym, end_ym):
    """[(分片首日, 次月1日)]：结束日取次月1日——聚宽分钟线 end_date 会吞掉边界当日"""
    res = []
    y, m = int(start_ym[:4]), int(start_ym[5:7])
    y2, m2 = int(end_ym[:4]), int(end_ym[5:7])
    while (y, m) <= (y2, m2):
        first = datetime.date(y, m, 1)
        nxt = datetime.date(y + 1, 1, 1) if m == 12 else datetime.date(y, m + 1, 1)
        res.append((str(first), str(nxt)))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return res


def shift_months(y, m, delta):
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def _fetch_one_day(contract, d):
    """单日补拉：起止 = [当日, 次日)，返回该日数据或 None"""
    try:
        one = get_price(contract, start_date=str(d), end_date=str(d + datetime.timedelta(days=1)),
                        frequency='1m', fields=FIELDS_1M)
    except Exception as ex:
        print(f'        单日补拉 {d} 异常: {ex}')
        return None
    if one is None or not len(one):
        return None
    one = one[one.index.date == d]
    return one if len(one) else None


def fetch_min1(contract, start, end, path, valid_lo=None, valid_hi=None):
    """分钟线：整段优先（异常则按月分片）+ 逐交易日校验 + 缺日逐日补漏（最多 3 轮）
    valid_lo/valid_hi：该合约应有数据的日期范围（建议传日线首尾日期；缺省用数据自身首尾）"""
    code = contract.split('.')[0]
    allm = None
    try:
        whole = get_price(contract, start_date=start, end_date=end, frequency='1m', fields=FIELDS_1M)
        if whole is not None and len(whole):
            allm = whole[~whole.index.duplicated(keep='first')].sort_index()
            print(f'    min1_{code}: 整段 {len(allm)} 行')
    except Exception as ex:
        print(f'    min1_{code}: 整段拉取异常({ex})，改按月分片')
    if allm is None:
        frames = []
        for s0, e0 in month_ranges(start[:7], end[:7]):
            try:
                m = get_price(contract, start_date=s0, end_date=e0, frequency='1m', fields=FIELDS_1M)
                if m is not None and len(m):
                    frames.append(m)
            except Exception as ex:
                print(f'      {code} {s0[:7]} 段失败: {ex}')
        if not frames:
            print(f'    min1_{code}: 为空！（检查分钟数据权限/范围）')
            return
        allm = pd.concat(frames)
        allm = allm[~allm.index.duplicated(keep='first')].sort_index()
        print(f'    min1_{code}: 按月分片 {len(allm)} 行')

    have = set(allm.index.date)
    lo = valid_lo or min(have)
    hi = valid_hi or max(have)
    try:
        lo = max(lo, datetime.date.fromisoformat(start))      # 不早于本次拉取起点
        lo = max(lo, get_security_info(contract).start_date)  # 不早于合约挂牌日
    except Exception:
        pass
    want = [d for d in get_trade_days(start_date=lo, end_date=hi) if d not in have]
    abnormal = len(want) > 100                      # 保护：校验区间异常时不空转逐日补漏
    if abnormal:
        print(f'    min1_{code}: !! 缺日数量异常（{len(want)} 天 >100，校验区间 {lo}..{hi}），'
              f'跳过逐日补漏，请把本段输出发我')
    for rnd in range(3):
        if not want or abnormal:
            break
        print(f'    min1_{code}: 校验发现 {len(want)} 个缺日，逐日补漏(第{rnd + 1}轮): '
              f'{[str(d) for d in want[:8]]}{" ..." if len(want) > 8 else ""}')
        fixed, still = [], []
        for d in want:
            one = _fetch_one_day(contract, d)
            if one is not None:
                fixed.append(one)
            else:
                still.append(d)
        if not fixed:
            print(f'    min1_{code}: 本轮补漏 0 成功，停止重试')
            break
        allm = pd.concat([allm] + fixed)
        allm = allm[~allm.index.duplicated(keep='first')].sort_index()
        have = set(allm.index.date)
        want = [d for d in still if d not in have]
    allm.to_csv(path)
    if want:
        print(f'    min1_{code}: !! 仍有 {len(want)} 天补漏失败 {[str(d) for d in want]}（请把本段输出发我）')
    cnt = allm.groupby(allm.index.date).size()
    print(f'    min1_{code}: {len(allm)} 行, 每日 {min(cnt)}~{max(cnt)} 根, '
          f'{"逐日校验通过" if not want else "存在缺口"}')


def resolve_dominants(symbol, exchange, dom_start, end):
    """逐交易日解析主力（与回测同源）；失败则用持仓量兜底"""
    day_strs = [str(d) for d in get_trade_days(start_date=dom_start, end_date=end)]
    doms = None
    try:
        doms = []
        for i, d in enumerate(day_strs):
            doms.append(get_dominant_future(symbol, date=d))
            if (i + 1) % 250 == 0:
                print(f'  {symbol} 主力解析进度 {i + 1}/{len(day_strs)}')
        print(f'  {symbol} get_dominant_future 解析成功')
    except Exception as e:
        print(f'  {symbol} get_dominant_future 失败，改用持仓量兜底: {e}')
        doms = None
    if doms is None:
        cands = []
        for y in range(int(dom_start[:4]) - 1, int(end[:4]) + 2):
            for mm in ['01', '05', '09']:
                cands.append(f'{symbol}{y % 100:02d}{mm}.{exchange}')
        oi = {}
        for c in cands:
            try:
                p = get_price(c, start_date=dom_start, end_date=end, frequency='daily',
                              fields=['open_interest'])
                if p is not None and len(p):
                    oi[c] = p['open_interest']
            except Exception:
                pass
        if not oi:
            return day_strs, None
        oi_df = pd.DataFrame(oi)
        oi_df.index = [str(i)[:10] for i in oi_df.index]
        doms = list(oi_df.idxmax(axis=1).reindex(day_strs))
    return day_strs, doms


def export_product(symbol, exchange, folder, day_start, min_start, dom_start, end, force):
    """导出某品种：主力解析 → 全合约（日线 + 分钟线）→ 交易日历 + dominant.csv"""
    if os.path.exists(f'{folder}/trade_days.csv') and not force:
        print(f'{symbol}：{folder} 已存在，跳过（如需重刷，先删除该文件夹或把 force 设为 True）\n')
        return
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)

    days_all = get_trade_days(start_date=day_start, end_date=end)
    pd.DataFrame({'date': [str(d) for d in days_all]}).to_csv(f'{folder}/trade_days.csv', index=False)
    print(f'{symbol} trade_days.csv: {len(days_all)} 天 ({day_start}..{end})')

    day_strs, doms = resolve_dominants(symbol, exchange, dom_start, end)
    if doms is None:
        print(f'{symbol} dominant 解析失败，请把本段输出发我')
        return
    pd.DataFrame({'date': day_strs, 'dominant': doms}).to_csv(f'{folder}/dominant.csv', index=False)
    runs, prev = [], None
    for d, dm in zip(day_strs, doms):
        tag = (dm or '?').split('.')[0]
        if tag != prev:
            runs.append((d, tag)); prev = tag
    print(f'{symbol} 主力切换序列: {runs}')
    contracts = sorted(set([d for d in doms if isinstance(d, str) and d]))
    print(f'{symbol} 主力合约共 {len(contracts)} 个:', [c.split('.')[0] for c in contracts], '\n')

    for c in contracts:
        code = c.split('.')[0]
        print(f'开始 {code} ...')
        d = None
        try:
            d = get_price(c, start_date=day_start, end_date=end, frequency='daily',
                          fields=['open', 'high', 'low', 'close', 'volume', 'open_interest'])
        except Exception as e:
            print(f'  daily_{code} 失败: {e}')
        dlo = dhi = None
        if d is not None and len(d):
            d.to_csv(f'{folder}/daily_{code}.csv')
            dd = d.dropna(subset=['close'])        # 未上市/已退市日期是整行空值，须按有效行取真实区间
            if len(dd):
                dlo, dhi = dd.index[0].date(), dd.index[-1].date()
            print(f'  daily_{code}: {len(d)} 行, 有效区间 {dlo}..{dhi}')
        digits = code[-4:]                             # 代码尾部 4 位 = 年月（兼容 RU / P 等不同长度品种前缀）
        yy = 2000 + int(digits[:2])
        mm = int(digits[2:])
        sy, sm = shift_months(yy, mm, -14)             # 交割月前 14 个月起
        start = f'{sy}-{sm:02d}-01'
        if start < min_start:
            start = min_start
        nxt = datetime.date(yy + 1, 1, 1) if mm == 12 else datetime.date(yy, mm + 1, 1)
        end_c = str(nxt - datetime.timedelta(days=1))  # 交割月末
        fetch_min1(c, start, end_c, f'{folder}/min1_{code}.csv', valid_lo=dlo, valid_hi=dhi)
    print(f'{symbol} 完成\n')


def main():
    for symbol, exchange, folder in [('RU', 'XSGE', 'export_ru'), ('P', 'XDCE', 'export_p')]:
        export_product(symbol, exchange, folder,
                       day_start='2019-08-01', min_start='2019-12-01', dom_start='2019-12-01',
                       end='2026-09-28', force=True)
    for name in ['export_p', 'export_ru']:
        if os.path.exists(name):
            shutil.make_archive(name, 'zip', root_dir='.', base_dir=name)
            print(f'{name}.zip 已生成')
    print('\n完成：请下载 export_ru.zip 与 export_p.zip（两个都要）')


main()
