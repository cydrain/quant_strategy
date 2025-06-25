#!/usr/bin/env python
#  -*- coding: utf-8 -*-
__author__ = 'cydrain'

from datetime import date, datetime
from dingtalkchatbot.chatbot import DingtalkChatbot
import pandas as pd
import time
from tqsdk import TqAccount, TqApi, TqAuth, TargetPosTask, TqBacktest, TqKq

pd_display_rows = 500
pd_display_cols = 1000
pd_display_width = 1000
pd.set_option('display.min_rows', pd_display_rows)
pd.set_option('display.max_rows', pd_display_rows)
pd.set_option('display.max_columns', pd_display_cols)
pd.set_option('display.width', pd_display_width)
pd.set_option('display.max_colwidth', pd_display_width)
pd.set_option('display.unicode.ambiguous_as_wide', True)
pd.set_option('display.unicode.east_asian_width', True)
pd.set_option('expand_frame_repr', False)

# 群机器人信息
webhook = "https://oapi.dingtalk.com/robot/send?access_token=c82976ef40dd0a4cf64b9bbe126f8272c9367ee7b3a09c748eb63076670a6534"
secret = "SECc34dbd0ee68473ccc86f510feceb7a6913d91c3f9da480b632238e631e337c46"

# 得到当前时间，精确到秒
def get_current_time():
    now = datetime.now()
    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")
    return formatted_time

def show_info(content):
    print('*' * 80)
    print(content)
    print('*' * 80)
    print('')

# 发送钉钉消息
def send_dingding_msg(content):
    show_info(content)
    chatbot = DingtalkChatbot(webhook=webhook, secret=secret)
    chatbot.send_text(msg=content)

# 返回更新后的帐户信息
def wait_account_update(api):
    account_info = api.get_account()
    deadline = time.time() + 1
    while api.wait_update(deadline):
        if api.is_changing(account_info):
            break
    return account_info

# 返回单一品种更新后的持仓信息
def wait_position_update(api, symbol):
    position_info = api.get_position(symbol)
    deadline = time.time() + 1
    while api.wait_update(deadline):
        if api.is_changing(position_info):
            break
    return position_info

# 查看帐户信息并发送
def send_account_info(api):
    account = wait_account_update(api)

    content = "%s\n帐户信息\n" % get_current_time()
    content += "====================\n"
    content += "帐户权益： %.2f\n" % account.balance
    content += "可用资金： %.2f\n" % account.available
    content += "保证金占用： %.2f\n" % account.margin
    content += "浮动盈亏： %.2f\n" % account.float_profit
    content += "持仓盈亏： %.2f\n" % account.position_profit
    content += "当日平仓盈亏： %.2f\n" % account.close_profit
    content += "当日手续费： %.2f\n" % account.commission
    content += "风险度： %.2f%%\n" % (account.risk_ratio * 100)

    send_dingding_msg(content)

# 查看持仓信息并发送
def send_position_info(api):
    position = api.get_position()

    symbol_list = list(position.keys())
    content = "%s\n持仓信息\n" % get_current_time()
    content += "====================\n"

    # 更新持仓合约信息
    info_str = ""
    for symbol in symbol_list:
        position_info = wait_position_update(api, symbol)
        trade_dir = 0
        pos = 0
        float_profit = 0.0
        open_price = 0.0
        last_price = position_info['last_price']
        if position_info['pos'] > 0:
            trade_dir = 1
            pos = position_info['pos_long']
            float_profit = position_info['float_profit_long']
            open_price = position_info['open_price_long']
        elif position_info['pos'] < 0:
            trade_dir = -1
            pos = position_info['pos_short']
            float_profit = position_info['float_profit_short']
            open_price = position_info['open_price_short']

        info_str += "%s\n" % symbol
        info_str += "持仓： %s %d\n" % ("多" if trade_dir == 1 else "空", pos)
        info_str += "开仓价： %.2f\n" % open_price
        info_str += "最新价： %.2f\n" % last_price
        info_str += "浮动盈亏： %.2f\n" % float_profit
        info_str += "---\n"

    content += info_str if info_str != "" else "空仓"
    send_dingding_msg(content)

def main():
    tq_kq = TqKq()
    # api = TqApi(account=tq_kq, auth=TqAuth(user_name='cydrain', password='7ujmko)P'))
    api = TqApi(web_gui=True,
                backtest=TqBacktest(start_dt=date(2025, 4, 16),
                                    end_dt=date(2025, 5, 9)),
                auth=TqAuth("cydrain", "7ujmko)P"))

    symbol_ru = "SHFE.ru2509"
    symbol_jd = "DCE.jd2509"
    klines = api.get_kline_serial(symbol_ru, 60, 3)
    target_ru = TargetPosTask(api, symbol_ru)
    target_ru.set_target_volume(-15)
    target_jd = TargetPosTask(api, symbol_jd)
    target_jd.set_target_volume(30)
    while True:
        api.wait_update()
        if api.is_changing(klines):
            break

    send_account_info(api)
    send_position_info(api)
    api.close()

if __name__ == '__main__':
    main()
