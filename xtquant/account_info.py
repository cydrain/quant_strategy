#!/usr/bin/env python
#  -*- coding: utf-8 -*-
__author__ = 'cydrain'

import pandas as pd
from xtquant.qmttools.functions import get_trade_detail_data


def get_money(C):
    df_m = pd.DataFrame(columns=['帐号类型', '总资产', '净资产', '总市值', '可用资金', '可用保证金'])
    money_info = get_trade_detail_data(accountid, accounttype, 'account')
    if len(money_info) == 1:
        # 如果资金信息的列表只胡一个元素，那么就是 money_info[0]
        df_m.loc[0, '帐号类型'] = money_info[0].m_nBrokerType
        df_m.loc[0, '总资产'] = money_info[0].m_dBalance
        df_m.loc[0, '净资产'] = money_info[0].m_dAssureAsset
        df_m.loc[0, '总市值'] = money_info[0].m_dInstrumentValue
        df_m.loc[0, '可用资金'] = money_info[0].m_dAvailable
        df_m.loc[0, '可用保证金'] = money_info[0].m_dEnableBailBalance
        print(df_m)

def get_position(C):
    position_info = get_trade_detail_data(accountid, accounttype, 'position')
    print(position_info, type(position_info))

    def position_to_dict(pos):
        return {
            '股票代码': pos.m_strInstrumentID + '.' + pos.m_strExchangeID,
            '持仓量': pos.m_nVolume,
            '可用数量': pos.m_nCanUseVolume,
            '持仓市值': pos.m_dMarketValue,
            '持仓成本': pos.m_dPositionCost,
            '持仓盈亏': pos.m_dPositionProfit,
            '浮动盈亏': pos.m_dFloatProfit,
            '成本价': pos.m_dOpenPrice
        }

    if len(position_info) > 0:
        df_pos = pd.DataFrame(list(map(position_to_dict, position_info)))
        df_pos = df_pos[['股票代码', '持仓量', '可用数量', '持仓市值', '持仓盈亏', '浮动盈亏', '持仓成本', '成本价']]
        df_pos['成本价'] = df_pos['成本价'].round(2)
        print(df_pos)

def get_deal(C):
    deal_info = get_trade_detail_data(accountid, accounttype, 'deal')
    print(deal_info, type(deal_info))

    def deal_to_dict(deal):
        return {
            '资金帐号': deal.m_strAccountID,
            '股票代码': deal.m_strInstrumentID + '.' + deal.m_strExchangeID,
            '成交编号': deal.m_strTradeID,
            '委托号': deal.m_strOrdeerSysID,
            '买卖方向': deal.m_nDirection,
            '买卖标记': deal.m_strOptName,
            '成交均价': deal.m_dPrice,
            '成交量': deal.m_nVolume,
            '成交日期': deal.m_strTradeDate,
            '成交时间': deal.m_strTradeTime,
            '成交额': deal.m_dTradeAmount,
        }

    if len(deal_info) > 0:
        df_deal = pd.DataFrame(list(map(deal_to_dict, deal_info)))
        if not df_deal.empty:
            df_deal = df_deal[['股票代码', '成交编号', '委托号', '买卖方向', '买卖标记', '成交均价', '成交量', '成交日期', '成交时间', '成交额']]
            print(df_deal)
            return df_deal
    else:
        return pd.DataFrame(columns=['股票代码', '成交编号', '委托号', '买卖方向', '买卖标记', '成交均价', '成交量', '成交日期', '成交时间', '成交额'])


def init(C):
    # 查询帐户资金信息
    get_money(C)
    # 查询持仓信息
    get_position(C)
    # 查询成交信息
    get_deal(C)
    # 查询委托信息
    get_order(C)

def handlebar(C):
    if not C.is_last_bar():
        return
    pass
