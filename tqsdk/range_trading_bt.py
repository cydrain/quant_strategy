#!/usr/bin/env python
#  -*- coding: utf-8 -*-
__author__ = 'cydrain'

from datetime import date
from tqsdk import TqApi, TqAuth, TqBacktest, TargetPosTask, tafunc
import pandas as pd

'''
如果当前价格从基准价格继续向下走：
则每下跌100点开5手多仓；每反弹50点平仓5手。
回测从 2025-04-16 到 2025-05-09
'''

price_baseline = 14800
price_delta = 100   # 价格每下跌100点，建仓5手
price_offset = 50   # 价格每反弹50点，平仓5手
pos_delta = 5       # 每次下单5手
steps = 5           #

# 在创建 api 实例时传入 TqBacktest 就会进入回测模式
api = TqApi(web_gui=True,
            backtest=TqBacktest(start_dt=date(2025, 4, 16),
                                end_dt=date(2025, 5, 9)),
            auth=TqAuth("cydrain", "7ujmko)P"))

# 获得5分钟K线的引用
symbol = "SHFE.ru2509"
duration_seconds = 5 * 60
data_length = 3

klines = api.get_kline_serial(symbol, duration_seconds, data_length)

# 创建目标持仓 task，该 task 负责调整仓位到指定的目标仓位
target_pos = TargetPosTask(api, symbol)

def cross_down(series, bar_array):
    for i, bar in enumerate(bar_array):
        if tafunc.crossdown(series, pd.Series(bar, range(series.size))).iloc[-1]:
            return i
    return -1

def cross_up(series, bar_array):
    for i, bar in enumerate(bar_array):
        if tafunc.crossup(series, pd.Series(bar, range(series.size))).iloc[-1]:
            return i
    return -1

price_down_array = []
price_up_array = []
for i in range(5):
    price_down_array.append(price_baseline - (i + 1) * price_delta)
    price_up_array.append(price_baseline - (i + 1) * price_delta + price_offset)

print("price down array: ", price_down_array)
print("price up array: ", price_up_array)

while True:
    api.wait_update()
    # 只有在新创建出K线时才判断开平仓条件
    if api.is_changing(klines.iloc[-1], "datetime"):
        # print("k-line", klines.close)
        # 判断前一根K线是否下穿基准线
        idx = cross_down(klines.close[:-1], price_down_array)
        if idx != -1:
            curr_pos = (idx + 1) * pos_delta
            print(f"价格下穿，开仓：前K价 {klines.open.iloc[-2]} {klines.close.iloc[-2]} 最新价 {klines.open.iloc[-1]}: 目标多头 {curr_pos} 手")
            # 设置目标持仓
            target_pos.set_target_volume(curr_pos)
            continue

        # 判断前一根K线是否上穿基准线
        idx = cross_up(klines.close[:-1], price_up_array)
        if idx != -1:
            curr_pos = idx * pos_delta
            print(f"价格上穿，平仓：前K价 {klines.open.iloc[-2]} {klines.close.iloc[-2]} 最新价 {klines.open.iloc[-1]}: 目标多头 {curr_pos} 手")
            # 设置目标持仓
            target_pos.set_target_volume(curr_pos)
            continue
