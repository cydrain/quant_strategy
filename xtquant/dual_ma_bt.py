#!/usr/bin/env python
#  -*- coding: utf-8 -*-
__author__ = 'cydrain'

import pandas as pd
import numpy as np
import talib

def init(C):
	C.stock = C.stockcode + '.' + C.market
	print("交易品种", C.stock)
	# line_fast 和 line_slow 分别为两条均线期数
	C.line_fast = 10	# 快线参数
	C.line_slow = 20	# 慢线参数
	# accountid 为测试ID，回测模式资金帐号可以填任意字符串
	C.accountid = "test"

def handlebar(C):
	# 当前K线日期
	bar_date = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')
	# 回测不需要订阅最新行情，使用本地数据速度更快，指定 subscribe 参数为 False
	# 如果回测多个品种，需要先下载对应周期历史数据
	local_data = C.get_market_data_ex(['close'], [C.stock], end_time=bar_date, period=C.period)
	close_list = list(local_data[C.stock].iloc[:, 0])
	# 将获取的历史数据转换为 DataFrame 格式，方便计算
	# 如果目前未持仓，同时快线穿过慢线，则买入 8 成仓位
	if len(close_list) < 1:
		print(bar_date, '行情数据不足，跳过')

	fast_mean = round(np.mean(close_list[-C.line_fast:]), 2)
	slow_mean = round(np.mean(close_list[-C.line_slow:]), 2)
	
	print(f"{bar_date}: 快均线 {fast_mean} 慢均线 {slow_mean}")
	account = get_trade_detail_data('test', 'stock', 'account')
	account = account[0]
	# 可用现金
	available_cash = account.m_dAvailable
	# 持仓信息
	holdings = get_trade_detail_data('test', 'stock', 'position')
	# m_nVolumn 为当前拥有的股数
	holdings = {i.m_strInstrumentID + '.' + i.m_strExchangeID : i.m_nVolume for i in holdings}
	holding_vol = holdings[C.stock] if C.stock in holdings else 0
	if holding_vol == 0 and fast_mean > slow_mean:
		vol = int(available_cash / close_list[-1] / 100) * 100
		# 开仓下单
		# opType 操作类型：买入23，卖出24
		# orderType 下单方式：1101 单股、单帐号、普通、股/手方式下单
		# prType
		passorder(23, 1101, C.accountid, C.stock, 5, -1, vol, C)
		print(f"{bar_date} 开仓")
		C.draw_text(1, 1, '开')
	# 如果目前持仓中，同时快线下穿慢线，则全部平仓
	elif holding_vol > 0 and fast_mean < slow_mean:
		# 状态变更为未持仓
		C.holding = False
		# 下单平仓
		passorder(24, 1101, C.accountid, C.stock, 5, -1, holding_vol, C)
		print(f"{bar_date} 平仓")
		C.draw_text(1, 1, '平')

