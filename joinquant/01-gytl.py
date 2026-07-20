# 标题：隔夜套利策略
# 六步选股：
# 1. 市值小于300亿
# 2. 在20个交易日内有过涨停
# 3. 下午14:30后再选股，只选涨幅在3%~5%之间的主板票
# 4. 量比要大于1
# 5. 换手率在5%~10%之间
# 6. 股价全天都在分时均线上方

import jqdata
from jqdata import *
from jqdata import finance
import numpy as np
import pandas as pd
import time

'''
================================================================================
总体回测前
================================================================================
'''
def initialize(context):
    # 设置参数
    set_params()
    # 设置全局变量
    set_variables()
    # 设置回测
    set_backtest()


# 1
# 设置参数
def set_params():
    # 设置股票池
    g.security = get_index_stocks('000300.XSHG')
    # 持仓股票
    g.hold_stocks = []
    # 最大持仓数
    g.max_hold_num = 5
    # 标记当日是否已开盘清仓
    g.has_sold = False
    # 标记当日是否已执行买入
    g.has_bought_today = False

# 2
# 设置全局变量
def set_variables():
    # 可行股票池
    g.available_stocks = []

# 3
# 设置回测
def set_backtest():
    # 一律使用真实价格
    set_option('use_real_price', True)
    # 过滤log
    log.set_level('order', 'error')
    # 设置基准
    set_benchmark('000300.XSHG')

'''
================================================================================
每日回测前
================================================================================
'''
def before_trading_start(context):
    current_dt = context.current_dt
    log.info("")
    log.info(f"=================================================")
    # 设置滑点、手续费和指数成分股
    set_slip_fee(context)
    # 每日盘前重置清仓标记
    g.has_sold = False
    # 标记当日是否已执行买入
    g.has_bought_today = False
    # 获取所有主板票
    g.stock_list = get_valid_main_board_stocks(current_dt)
    log.info(f"主版股票数: {len(g.stock_list)}")
    # 遍历筛选条件1/2
    g.stock_list = filter_market_cap(g.stock_list, current_dt, 300)
    log.info(f"市值小于300亿股票数: {len(g.stock_list)}")
    g.stock_list = filter_has_limit_up_in_Ndays(g.stock_list, current_dt)
    log.info(f"20天内有过涨停的股票数: {len(g.stock_list)}")

# 4
# 根据不同的时间段设置滑点与手续费并且更新指数成分股
def set_slip_fee(context):
    # 更新指数成分股
    g.security = get_index_stocks('000300.XSHG')
    # 将滑点设置为0
    set_slippage(FixedSlippage(0)) 
    # 根据不同的时间段设置手续费
    dt=context.current_dt
    
    if dt>datetime.datetime(2013,1, 1):
        set_commission(PerTrade(buy_cost=0.0003, sell_cost=0.0013, min_cost=5)) 
        
    elif dt>datetime.datetime(2011,1, 1):
        set_commission(PerTrade(buy_cost=0.001, sell_cost=0.002, min_cost=5))
            
    elif dt>datetime.datetime(2009,1, 1):
        set_commission(PerTrade(buy_cost=0.002, sell_cost=0.003, min_cost=5))
                
    else:
        set_commission(PerTrade(buy_cost=0.003, sell_cost=0.004, min_cost=5))
    

def get_valid_main_board_stocks(current_dt):
    """
    获取主板股票(60开头沪主板、000开头深主板)，剔除ST、当日停牌标的
    :param current_dt: 完整datetime时间对象
    :return: list 有效股票代码列表
    """
    today = current_dt.date()
    # 获取全市场股票基础信息，包含股票名称
    all_sec_df = get_all_securities(types=['stock'], date=today)

    # 筛选主板代码
    main_board_codes = [
        code for code in all_sec_df.index
        if code.startswith("60") or code.startswith("000")
    ]

    valid_stocks = []
    for sec in main_board_codes:
        # 取股票名称判断是否ST
        stock_name = all_sec_df.loc[sec, 'display_name']
        if 'ST' in stock_name or '*ST' in stock_name or stock_name.startswith('S'):
            continue

        # 判断当日是否停牌
        df = get_price(
            sec,
            start_date=str(today),
            end_date=str(today),
            frequency='daily',
            fields=['paused']
        )
        if len(df) == 0 or df['paused'].iloc[0] == 1:
            #log.info(f"{sec} 今日停牌，过滤")
            continue

        valid_stocks.append(sec)
    return valid_stocks
    

def filter_market_cap(stock_list, current_dt, cap_thresh):
    """
    批量过滤股票列表，保留总市值 ≤ cap_thresh 亿元的标的
    :param stock_list: 股票代码列表
    :param current_dt: 当前完整datetime时间
    :param cap_thresh: 市值上限，单位：亿元
    :return: list，过滤后股票代码
    """
    today = current_dt.date()
    # 批量查询当前池子所有股票市值，减少接口次数
    q = query(valuation.code, valuation.market_cap).filter(valuation.code.in_(stock_list))
    cap_df = get_fundamentals(q, date=today)
    
    valid_stocks = []
    for _, row in cap_df.iterrows():
        code = row['code']
        market_cap = row['market_cap']
        # 市值非空且不超过阈值才保留
        if not pd.isna(market_cap) and market_cap <= cap_thresh:
            valid_stocks.append(code)
    return valid_stocks


def filter_has_limit_up_in_Ndays(stock_list, current_dt, window=20, up_thresh=0.098):
    """
    批量筛选：截至当前日期，近window个交易日出现过单日涨幅≥涨停阈值的股票
    :param stock_list: 待筛选股票池
    :param current_dt: 策略当前datetime时间
    :param window: 回溯天数，默认20
    :param up_thresh: 涨停涨幅阈值，默认0.098(9.8%)
    :return: list 满足近N日有涨停的股票代码
    """
    today = current_dt.date()
    result = []
    end_day = str(today)
    
    for sec in stock_list:
        # 截止today，向前取window根日线
        hist_df = get_price(
            security=sec,
            end_date=end_day,
            count=window,
            frequency='daily',
            fields=['close']
        )
        # K线数量不足直接剔除
        if len(hist_df) < window:
            #log.info(f"{sec} 历史K线不足{window}根，过滤")
            continue
        hist_df = hist_df.dropna(subset=['close'])
        if len(hist_df) < window:
            #log.info(f"{sec} 剔除空值后K线不足{window}根，过滤")
            continue
        
        # 逐日计算涨跌幅（前日收盘→当日收盘真实日间涨跌幅）
        pct_change = hist_df['close'].pct_change()
        # 任意一天达到涨停阈值则保留
        if (pct_change >= up_thresh).any():
            result.append(sec)
    return result
    

'''
================================================================================
每天交易时
================================================================================
'''
# 每个回测单位
def handle_data(context, data):
    current_dt = context.current_dt

    # 1. 开盘9:31 第一分钟执行清仓，仅执行一次
    if (current_dt.hour == 9 and current_dt.minute == 31) and (not g.has_sold):
        clear_all_positions(context, current_dt)
        g.has_sold = True
        return

    # 2. 14:50直接进入选股买入逻辑
    if not (current_dt.hour == 14 and current_dt.minute == 50):
        return
    # 已完成当日买入则不再重复买入
    if g.has_bought_today:
        return

    # 4. 遍历筛选条件3/4/5/6
    candidate_list = filter_daily_return(g.stock_list, current_dt)
    log.info(f"当天涨幅在[3%,5%]之间的股票数: {len(candidate_list)}")
    candidate_list = filter_volume_ratio(candidate_list, current_dt)
    log.info(f"当天量比大于1.0的股票数: {len(candidate_list)}")
    candidate_list = filter_turnover_ratio(candidate_list, current_dt)
    log.info(f"当天换手率在[5%,10%]之间的股票数: {len(candidate_list)}")
    candidate_list = filter_close_above_twap(candidate_list, current_dt)
    log.info(f"当日全部分钟收盘价始终高于全天分时均价的股票数: {len(candidate_list)}")

    # 5. 调用买入函数
    buy_selected_stocks(context, current_dt, candidate_list, g.max_hold_num)
    

def buy_selected_stocks(context, current_dt, candidate_list, max_num):
    """
    选股买入函数：支持残留停牌持仓名额占用、动态控制总持仓不超限、均分资金市价买入
    核心逻辑说明：
        1. 兼容历史残留持仓：g.hold_stocks 中未清仓的停牌个股会占用持仓名额
        2. 动态计算可新开仓数量：可买数量 = 最大持仓上限max_num - 已有残留持仓数量
        3. 严格空仓校验：仅校验当日可交易持仓，停牌残留持仓不触发报错，但占用名额
        4. 随机打乱候选池，逐个校验停牌、执行买入，计数满额度终止循环
        5. 仅真实成交标的合并进全局持仓列表
        6. 函数执行末尾打印全部可交易持仓的持仓数量、持仓均价明细
    前置约束：
        调用前已过滤当日全天停牌个股；当日可交易仓位必须完全空仓
    :param context: 策略上下文
    :param current_dt: 策略当前完整datetime时间对象
    :param candidate_list: 多层筛选后符合条件的候选股票列表
    :param max_num: 账户最大总持仓数量上限（含历史停牌残留持仓）
    :return: real_buy: 当日实际新开仓成交的股票列表
    """
    # 1. 候选池为空直接返回
    if not candidate_list:
        log.info("无符合条件的买入标的")
        return g.hold_stocks

    # 2. 校验当日可交易持仓，有持仓直接报错退出
    for sec, pos_info in context.portfolio.positions.items():
        if pos_info.total_amount > 0:
            raise RuntimeError(f"买入前检测到可交易持仓 {sec}，数量{pos_info.total_amount}，流程终止，请先清仓！")

    # 统计历史未清仓残留持仓（停牌无法卖出标的），计算当日可新开仓额度
    exist_hold_cnt = len(g.hold_stocks)
    remain_can_buy = max_num - exist_hold_cnt
    if remain_can_buy <= 0:
        log.info(f"已有{exist_hold_cnt}只未清仓残留标的，已达最大持仓上限{max_num}，今日不新开仓")
        return []

    today = current_dt.date()
    # 复制列表随机打乱
    shuffle_list = candidate_list.copy()
    random.shuffle(shuffle_list)

    cash_total = context.portfolio.available_cash
    real_buy = []
    buy_count = 0

    # 逐一遍历，停牌跳过，买入成功计数，满额退出
    for sec in shuffle_list:
        if buy_count >= remain_can_buy:
            break

        # 校验当前时点是否盘中停牌
        minute_df = get_price(
            sec,
            start_date=str(today),
            end_date=current_dt,
            frequency="1m",
            fields=['paused']
        )
        if len(minute_df) == 0 or minute_df['paused'].iloc[-1] == 1:
            log.info(f"{sec} 14:50盘中临时停牌，跳过买入")
            continue

        # 动态计算剩余标的单只分配资金
        left_need_buy = remain_can_buy - buy_count
        single_cash = cash_total / left_need_buy
        if single_cash < 100:
            log.info(f"{sec} 单只分配资金不足100元，跳过委托")
            continue
        
        order_value(sec, single_cash)
        
        # 短暂等待后校验持仓，确认是否真实成交
        time.sleep(0.05)
        positions = context.portfolio.positions
        if sec in positions and positions[sec].total_amount > 0:
            real_buy.append(sec)
            buy_count += 1
            cash_total -= single_cash
            log.info(f"  买入 {sec}, 分配资金 {single_cash:.2f}，成交确认")
        else:
            log.info(f"  {sec} 下单后停牌/委托作废，不计入持仓，资金回收")

    # 合并历史残留持仓 + 当日真实成交持仓，更新全局持仓记录
    g.hold_stocks = g.hold_stocks + real_buy
    g.has_bought_today = (len(real_buy) > 0)

    # 打印全部持仓明细，区分可交易/停牌不可交易，每支仅打印一次
    log.info("===== 当前全部持仓明细（含停牌残留） =====")
    pos_map = {sec: pos_info for sec, pos_info in context.portfolio.positions.items() if pos_info.total_amount > 0}
    for sec in g.hold_stocks:
        if sec in pos_map:
            pos_info = pos_map[sec]
            total_share = pos_info.total_amount
            avg_cost = pos_info.avg_cost
            log.info(f"  {sec}，可交易，持仓：{total_share}，均价：{avg_cost:.2f}, 总价：{total_share * avg_cost:.2f}")
        else:
            log.info(f"  {sec}，停牌，当日无持仓数据")

    return real_buy
    
    
def clear_all_positions(context, current_dt):
    """
    市价清仓全部多头持仓，跳过停牌无法成交标的
    以当前时点分钟线最新价作为卖出成交均价计算盈亏
    :param context: 策略上下文
    :param current_dt: 策略当前完整datetime
    """
    today = current_dt.date()
    trade_sec_set = set(context.portfolio.positions.keys())
    for sec in list(trade_sec_set):
        pos_info = context.portfolio.positions[sec]
        pos = pos_info.total_amount
        if pos <= 0:
            continue

        minute_df = get_price(
            sec,
            start_date=str(today),
            end_date=current_dt,
            frequency="1m",
            fields=['paused', 'close']
        )
        # 盘中临时停牌跳过卖出
        if len(minute_df) == 0 or minute_df['paused'].iloc[0] == 1:
            log.info(f"{sec} 盘中临时停牌，无法清仓，持仓残留")
            continue

        sell_avg_price = minute_df['close'].iloc[-1]
        cost_price = pos_info.avg_cost
        total_cost = pos * cost_price
        total_sell = pos * sell_avg_price
        profit = total_sell - total_cost
        profit_rate = profit / total_cost if total_cost != 0 else 0

        order(sec, -pos)
        log.info(
            f"  卖出 {sec}，持仓 {pos}，"
            f"均价 {cost_price:.2f}，卖出均价 {sell_avg_price:.2f}，"
            f"盈亏 {profit:.2f}，盈亏比例 {profit_rate:.2%}"
        )
        # 成功下单卖出，立刻从全局持仓列表移除
        if sec in g.hold_stocks:
            g.hold_stocks.remove(sec)

    # 找出昨日持仓里全天停牌、不在positions中的股票
    missing_paused = [sec for sec in g.hold_stocks if sec not in trade_sec_set]
    if missing_paused:
        log.error(f"昨日持仓今日全天停牌，无法卖出，保留待次日处理：{missing_paused}")


def filter_daily_return(stock_list, current_dt, lower=0.03, upper=0.05):
    """
    批量筛选截至current_dt时刻，当日开盘到现价涨跌幅处于[lower, upper]区间的股票
    :param stock_list: 股票代码列表
    :param current_dt: 完整datetime时间对象
    :param lower: 最低日内涨跌幅阈值
    :param upper: 最高日内涨跌幅阈值
    :return: 过滤后股票代码列表
    """
    today = current_dt.date()
    res = []
    for sec in stock_list:
        df = get_price(
            sec,
            start_date=str(today),
            end_date=current_dt,
            frequency='1d',
            fields=['open', 'close']
        )
        if len(df) == 0:
            continue
        row = df.iloc[-1]
        open_price = row['open']
        close_price = row['close']
        if open_price == 0 or pd.isna(open_price) or pd.isna(close_price):
            continue
        daily_return = (close_price - open_price) / open_price
        if lower <= daily_return <= upper:
            res.append(sec)
    return res
    

def filter_volume_ratio(stock_list, current_dt, threshold=1):
    """
    批量筛选盘中量比大于阈值的股票
    量比 = 当日每分钟均量 / 近5日全天每分钟均量
    :param stock_list: 已过滤停牌股票列表
    :param current_dt: 当前分时datetime快照
    :param threshold: 量比阈值
    :return: 符合条件股票列表
    """
    today = current_dt.date()
    result = []
    total_day_min = 240  # A股全天交易分钟

    for sec in stock_list:
        # 1. 获取今日截至当前分时累计成交量
        df_today = get_price(
            sec,
            start_date=str(today),
            end_date=current_dt,
            frequency="minute",
            fields=["volume"]
        )
        if len(df_today) == 0:
            continue
        today_vol = df_today["volume"].sum()
        passed_min = len(df_today)
        today_min_avg = today_vol / passed_min

        # 2. 获取过去5个完整交易日日线总成交量
        start_5day = today - timedelta(days=8)
        df_5day = get_price(
            sec,
            start_date=str(start_5day),
            end_date=str(today - timedelta(days=1)),
            frequency="daily",
            fields=["volume"]
        )
        # 剔除无成交、停牌日
        df_5day = df_5day[df_5day["volume"] > 0]
        if len(df_5day) < 3:
            log.info(f"{sec} 近5日有效交易日不足，跳过")
            continue
        avg_5day_total_vol = df_5day["volume"].mean()
        avg_5day_min_vol = avg_5day_total_vol / total_day_min

        # 3. 计算标准量比
        vol_ratio = today_min_avg / avg_5day_min_vol
        if vol_ratio > threshold:
            result.append(sec)
    return result


def filter_turnover_ratio(stock_list, current_dt, lower=5, upper=10):
    """
    批量筛选盘中实时换手率在 [lower, upper] 区间的股票
    换手率 = 当日累计成交量 ÷ 流通股本 × 100%，打印每只个股实时换手
    :param stock_list: 已过滤停牌股票列表
    :param current_dt: 当前分时datetime快照
    :param lower: 换手率下限(百分比)
    :param upper: 换手率上限(百分比)
    :return: 满足条件股票列表
    """
    today = current_dt.date()
    result = []
    for sec in stock_list:
        minute_df = get_price(
            sec,
            start_date=str(today),
            end_date=current_dt,
            frequency="minute",
            fields=["volume"]
        )
        if len(minute_df) == 0:
            log.info(f"{sec} 无当日分时数据，跳过")
            continue
        total_trade_vol = minute_df["volume"].sum()
        if total_trade_vol <= 0:
            log.info(f"{sec} 当日无成交量，跳过")
            continue

        q = query(valuation.circulating_cap).filter(valuation.code == sec)
        cap_df = get_fundamentals(q, date=str(today))
        if len(cap_df) == 0:
            log.info(f"{sec} 无流通股本基本面数据，跳过")
            continue
        circulate_cap_wan = cap_df.iloc[0]["circulating_cap"]
        if pd.isna(circulate_cap_wan) or circulate_cap_wan <= 0:
            log.info(f"{sec} 流通股本数据异常，跳过")
            continue
        
        # 关键修复：万股 → 股，×10000
        circulate_share = circulate_cap_wan * 10000
        turnover_pct = (total_trade_vol / circulate_share) * 100
        #log.info(
        #    f"{sec} 累计成交{total_trade_vol:.0f}股，"
        #    f"流通股本{circulate_cap_wan:.2f}万股={circulate_share:.0f}股，"
        #    f"实时换手率{turnover_pct:.2f}%"
        #)

        if lower <= turnover_pct <= upper:
            result.append(sec)
    return result
    

def filter_close_above_twap(stock_list, current_dt):
    """
    批量筛选：截至current_dt当日，收盘价高于全天成交量加权均价TWAP
    :param stock_list: 股票代码列表
    :param current_dt: 完整datetime时间对象，行情截止快照
    :return: 满足条件股票代码列表
    """
    today = current_dt.date()
    end_dt = current_dt
    result = []
    for sec in stock_list:
        minute_df = get_price(
            sec,
            start_date=str(today),
            end_date=end_dt,
            frequency='minute',
            fields=['open','high','low','close','volume']
        )
        if len(minute_df) == 0:
            log.info(f"{sec} 无当日分钟数据，跳过")
            continue
        minute_df = minute_df.dropna(subset=['close', 'volume'])
        total_volume = minute_df['volume'].sum()
        if total_volume <= 0:
            log.info(f"{sec} 当日无成交量，跳过")
            continue
        
        # 单分钟均价
        minute_df['avg_min_price'] = (minute_df['open'] + minute_df['high'] + minute_df['low'] + minute_df['close']) / 4
        total_amount = (minute_df['avg_min_price'] * minute_df['volume']).sum()
        twap = total_amount / total_volume
        
        close_price = minute_df['close'].iloc[-1]
        if close_price > twap:
            result.append(sec)
    return result
    
    