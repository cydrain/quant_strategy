#!/usr/bin/env python
#  -*- coding: utf-8 -*-
__author__ = 'cydrain'

from tqsdk import TqApi, TqAuth
import pandas as pd

# 创建API实例,传入自己的快期账户
api = TqApi(auth=TqAuth("cydrain", "7ujmko)P"))

# 定义要获取的K线周期（秒）
duration_seconds = 5 * 60  # 5分钟线
data_length = 10000  # 获取10000根K线

# 主力合约列表（示例）
main_contracts = [
    "KQ.m@SHFE.ru", # 上期所铜主力
    "KQ.m@DCE.jd",  # 大商所鸡蛋主力
    "KQ.m@DCE.p",   # 大商所棕榈油主力
]

all_data = {}

for symbol in main_contracts:
    try:
        print(f"正在下载 {symbol} 的K线数据...")
        klines = api.get_kline_serial(
            symbol=symbol,
            duration_seconds=duration_seconds,
            data_length=data_length
        )
        all_data[symbol] = klines
        print(f"{symbol} 数据下载完成，共 {len(klines)} 条记录")
    except Exception as e:
        print(f"下载 {symbol} 数据失败: {str(e)}")

api.close()

# 将数据保存到CSV文件
for symbol, data in all_data.items():
    df = pd.DataFrame(data)
    filename = f"klines/{symbol.replace('@', '_').replace('.', '_')}_kline.csv"
    df.to_csv(filename, index=False)
    print(f"已保存 {filename}")
