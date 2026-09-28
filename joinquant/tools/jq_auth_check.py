# -*- coding: utf-8 -*-
"""聚宽账号认证 + 数据权限探测（jqdatasdk）

用法：python tools/jq_auth_check.py
凭据来源（优先级）：环境变量 JQ_USER / JQ_PASS → tools/.jq_env 文件（两行，勿提交）
"""
import os
import sys

try:
    import jqdatasdk as jq
except ImportError:
    sys.exit("未安装 jqdatasdk：请在 conda 环境内执行 pip install jqdatasdk pandas")


def load_creds():
    user = os.environ.get('JQ_USER')
    pwd = os.environ.get('JQ_PASS')
    if user and pwd:
        return user, pwd
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.jq_env')
    if os.path.exists(env_file):
        for line in open(env_file, encoding='utf-8'):
            line = line.strip()
            if line.startswith('JQ_USER='):
                user = line.split('=', 1)[1].strip()
            elif line.startswith('JQ_PASS='):
                pwd = line.split('=', 1)[1].strip()
    return user, pwd


def main():
    user, pwd = load_creds()
    if not user or not pwd:
        sys.exit("未找到凭据：请设置环境变量 JQ_USER/JQ_PASS，或写入 tools/.jq_env（JQ_USER=... / JQ_PASS=...）")

    jq.auth(user, pwd)
    print("认证成功")
    print("剩余流量:", jq.get_query_count())

    # ① 日线探测
    try:
        df = jq.get_price('RU2701.XSGE', start_date='2026-08-01', end_date='2026-08-12',
                          frequency='daily', fields=['open', 'high', 'low', 'close', 'open_interest'])
        print("\n[日线] RU2701 2026-08-01~08-12:", 0 if df is None else len(df), "根")
        if df is not None and not df.empty:
            print(df.head(3))
    except Exception as e:
        print("\n[日线] 探测失败:", e)

    # ② 分钟线探测
    try:
        df = jq.get_price('RU2701.XSGE', start_date='2026-08-10 09:00:00', end_date='2026-08-10 10:00:00',
                          frequency='1m', fields=['open', 'high', 'low', 'close'])
        print("\n[分钟] RU2701 8/10 09:00~10:00:", 0 if df is None else len(df), "根")
        if df is not None and not df.empty:
            print(df.head(3))
    except Exception as e:
        print("\n[分钟] 探测失败（可能免费账号无分钟权限）:", e)

    # ③ 主力合约接口探测
    fn = getattr(jq, 'get_dominant_future', None)
    print("\n[主力] get_dominant_future 可用:", bool(fn))
    if fn:
        try:
            print("  RU @2026-08-10 ->", fn('RU', date='2026-08-10'))
        except Exception as e1:
            try:
                print("  RU @2026-08-10 ->", fn('RU', '2026-08-10'))
            except Exception as e2:
                print("  调用失败:", e1, '/', e2)

    # ④ 交易日历探测
    try:
        days = jq.get_trade_days(start_date='2026-09-01', end_date='2026-09-24')
        print("\n[日历] 2026-09-01~09-24 共", len(days), "个交易日")
    except Exception as e:
        print("\n[日历] 探测失败:", e)


if __name__ == '__main__':
    main()
