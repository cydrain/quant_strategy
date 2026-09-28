# -*- coding: utf-8 -*-
"""拉取本地回放所需数据（jqdatasdk）→ local_data/

用法：python tools/fetch_jq_data.py
前置：pip install jqdatasdk pandas；凭据同 jq_auth_check.py（自动认证）
输出：
  local_data/trade_days.csv        交易日历（2025-10-01 ~ 2026-09-24）
  local_data/daily_RU2605.csv 等   日线（open/high/low/close/volume/open_interest）
  local_data/min1_RU2605.csv 等    1分钟线（按月分段拉取后合并）
  local_data/dominant.csv          逐交易日主力合约（优先 get_dominant_future，失败用持仓量兜底）
"""
import os
import sys
import time
import datetime

import pandas as pd

try:
    import jqdatasdk as jq
except ImportError:
    sys.exit("未安装 jqdatasdk：请在 conda 环境内执行 pip install jqdatasdk pandas")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'local_data')
CONTRACTS = ['RU2605.XSGE', 'RU2609.XSGE', 'RU2701.XSGE']
START = '2025-10-01'
END = '2026-09-24'


def ensure_auth():
    if jq.is_auth():
        return
    user = os.environ.get('JQ_USER')
    pwd = os.environ.get('JQ_PASS')
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.jq_env')
    if (not user or not pwd) and os.path.exists(env_file):
        for line in open(env_file, encoding='utf-8'):
            line = line.strip()
            if line.startswith('JQ_USER='):
                user = line.split('=', 1)[1].strip()
            elif line.startswith('JQ_PASS='):
                pwd = line.split('=', 1)[1].strip()
    if not user or not pwd:
        sys.exit("未找到凭据：设置 JQ_USER/JQ_PASS 环境变量，或写入 tools/.jq_env")
    jq.auth(user, pwd)
    print("认证成功，剩余流量:", jq.get_query_count())


def month_ranges(start_ym, end_ym):
    res = []
    y, m = int(start_ym[:4]), int(start_ym[5:7])
    y2, m2 = int(end_ym[:4]), int(end_ym[5:7])
    while (y, m) <= (y2, m2):
        first = datetime.date(y, m, 1)
        nxt = datetime.date(y + 1, 1, 1) if m == 12 else datetime.date(y, m + 1, 1)
        res.append((str(first), str(nxt - datetime.timedelta(days=1))))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return res


def fetch_trade_days():
    days = jq.get_trade_days(start_date=START, end_date=END)
    pd.DataFrame({'date': [str(d) for d in days]}).to_csv(os.path.join(OUT, 'trade_days.csv'), index=False)
    print(f"trade_days.csv: {len(days)} 个交易日")


def fetch_daily():
    for c in CONTRACTS:
        code = c.split('.')[0]
        try:
            df = jq.get_price(c, start_date=START, end_date=END, frequency='daily',
                              fields=['open', 'high', 'low', 'close', 'volume', 'open_interest'])
            if df is None or df.empty:
                print(f"daily_{code}: 空（检查权限）")
                continue
            df.to_csv(os.path.join(OUT, f'daily_{code}.csv'))
            print(f"daily_{code}.csv: {len(df)} 根")
        except Exception as e:
            print(f"daily_{code} 拉取失败: {e}")


def fetch_min1():
    ranges = month_ranges('2025-10', '2026-09')
    for c in CONTRACTS:
        code = c.split('.')[0]
        frames = []
        for s, e in ranges:
            try:
                df = jq.get_price(c, start_date=s, end_date=e, frequency='1m',
                                  fields=['open', 'high', 'low', 'close', 'volume'])
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception as ex:
                print(f"min1_{code} {s[:7]} 失败: {ex}")
            time.sleep(0.1)
        if frames:
            all_df = pd.concat(frames)
            all_df = all_df[~all_df.index.duplicated(keep='first')]
            all_df.to_csv(os.path.join(OUT, f'min1_{code}.csv'))
            print(f"min1_{code}.csv: {len(all_df)} 根")
        else:
            print(f"min1_{code}: 空（检查分钟数据权限）")


def fetch_dominant(trade_day_strs):
    fn = getattr(jq, 'get_dominant_future', None)
    doms = None
    if fn:
        try:
            doms = [fn('RU', date=d) for d in trade_day_strs]
        except Exception as e:
            print("get_dominant_future 调用失败，改用持仓量兜底:", e)
            doms = None
    if doms is None:
        oi = {}
        for c in CONTRACTS:
            code = c.split('.')[0]
            p = jq.get_price(c, start_date=START, end_date=END, frequency='daily', fields=['open_interest'])
            if p is not None and not p.empty:
                oi[code] = p['open_interest']
        if not oi:
            print("dominant.csv: 无法生成（接口与持仓量都不可用）")
            return
        oi_df = pd.DataFrame(oi)
        oi_df.index = [str(i)[:10] for i in oi_df.index]
        doms = list(oi_df.idxmax(axis=1).reindex(trade_day_strs))
    pd.DataFrame({'date': trade_day_strs, 'dominant': doms}).to_csv(os.path.join(OUT, 'dominant.csv'), index=False)
    print(f"dominant.csv: {len(trade_day_strs)} 行")


def main():
    ensure_auth()
    os.makedirs(OUT, exist_ok=True)
    fetch_trade_days()
    fetch_daily()
    fetch_min1()
    days = jq.get_trade_days(start_date='2025-12-01', end_date=END)
    fetch_dominant([str(d) for d in days])
    print("\n完成。数据目录:", OUT)


if __name__ == '__main__':
    main()
