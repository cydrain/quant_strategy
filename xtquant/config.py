#!/usr/bin/env python
#  -*- coding: utf-8 -*-
__author__ = 'cydrain'

import logging as log
import time
import traceback

from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount

#=========================================
path = r'C:\Program Files (x86)\迅投极速交易终端\userdata_mini'
account_id = ''

session_id = int(time.time() * 1000)
xt_trader = XtQuantTrader(path, session_id)
account_stock = StockAccount(account_id, 'STOCK')

# 启动交易线程
xt_trader.start()

# 建立交易连接，返回 0 表示连接成功
connect_result = xt_trader.connect()

# 对交易回调进行订阅，订阅后可以收到交易主推，返回 0 表示订阅成功
subscribe_result = xt_trader.subscribe(account_stock)
