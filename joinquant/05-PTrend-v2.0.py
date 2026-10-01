# ============================================================
# 棕榈油 P 日线趋势策略 v2.0（聚宽回测 · 固定方向版）
#   - v2 变更: 方向由 fixed_dir 参数给定（分段已知行情，只做单边）；入场不再判断方向、
#     不启用 e/f/d 过滤；出场仅 ATR 移动止损；不加仓（单笔仓位=总资产×10%）
# 设计文档: docs/superpowers/specs/2026-09-17-ru-trend-pullback-design.md
#   - 信号: 回踩窗口触线 + 站回 MA20 + 距离上限，每交易日 14:45 判定执行
#   - 出场: 初始止损 / 移动止损（每分钟，含夜盘）
#   - 移仓: 主力切换连续 2 日确认 + 先开新后平旧 + 止损价差平移
# 回测设置: 期货账户 / 100 万 / 分钟频率 / 2018-01-01 起
# 换品种清单: PARAMS 的 symbol / exchange（基准后缀）/ night_start,night_end（夜盘时段）
#             / multiplier / margin_rate / 费率（set_order_cost，按品种调整）
# ============================================================
from jqdata import *
import datetime
import pandas as pd

# ==================== 参数（集中，initialize 时拷入 g.params） ====================
PARAMS = {
    'symbol': 'P',               # 品种代码（主力解析/合约前缀；如棕榈油 'P'）
    'exchange': 'XDCE',           # 交易所后缀（仅基准合约用：上期所 XSGE / 大商所 XDCE / 郑商所 XZCE）
    'fixed_dir': 1,               # v2 固定方向（分段已知行情）：1=只做多 / -1=只做空 / 0=按 MA20 斜率判断
    'ma_period': 20,              # MA20 周期（方向/回踩/出场共用）
    'ma_slope_days': 3,           # MA20 上行判定跨度（交易日）
    'pullback_window': 3,         # 回踩窗口交易日数，1 = 同根 K 线规则
    'entry_max_dist_atr': 0,      # 入场距 MA20 距离上限（ATR 倍数），0 = 不限；实验（D 变体）表明启用伤收益，默认关闭
    'pullback_max_break_atr': 0,  # 回踩窗口内单日跌破深度上限（ATR 倍数），0 = 不限
    'deep_drop_lookback': 10,     # d 深跌回看窗口（交易日，含今日）
    'deep_drop_retrace_atr': 2.0, # d 深跌触发：高点回撤 ≥ 该 ATR 倍数（0 = 关闭）
    'deep_drop_day_atr': 1.0,     # d 深跌触发（OR 项）：单日跌幅 ≥ 该 ATR 倍数（0 = 关闭）
    'deep_drop_wait_days': 2,     # d 低点刷新后最短等待交易日数（N）
    'cooldown_days': 0,           # 平仓当日+其后禁止开新仓的交易日数（0 = 仅当日）
    'atr_n': 20,                  # ATR 周期
    'stop_loss_dist_atr': 2.0,    # 止损距离（ATR 倍数），初始与移动共用
    'signal_exit_dist_atr': 0,    # 信号反转失守缓冲（ATR 倍数；0 = 关闭该出场，仅用移动止损）
    'position_pct': 0.10,         # 单笔仓位：保证金占用=总资产×该比例（手数按价格×乘数×保证金率折算，最低 1 手）
    'max_adds': 0,                # 最大加仓次数（v2 不加仓：单笔仓位=总资产×10%）
    'add_min_dist_atr': 0.5,      # 加仓最小距离（ATR 倍数），0 = 仅要求正浮盈
    'signal_time': '14:45',       # 信号判定时刻
    'night_start': '21:00',       # 夜盘任务注册起始时刻（空串 = 无夜盘品种）
    'night_end': '23:00',         # 夜盘任务注册结束时刻
    'multiplier': 10,             # 合约乘数（RU/P 均为 10；换品种按品种调整）
    'margin_rate': 0.12,          # 保证金率（回测设定值）
}

# ==================== 日志辅助 ====================

def log_pfx():
    """日志前缀：当前主力合约代码（如 [RU2605]）；未解析时退回品种代码"""
    c = getattr(g, 'current_contract', None)
    return '[' + (c.split('.')[0] if c else PARAMS['symbol']) + ']'


def trade_mark(side, reason):
    """成交标记：开/加多=🔴、平多=🔶、开/加空=🟢、平空=🔷"""
    if reason in ('entry', 'add'):
        return '🔴' if side == 'long' else '🟢'
    return '🔶' if side == 'long' else '🔷'


def daily_log_separator(ctx):
    """每日日志分隔线：夜盘开盘打印（时间取 night_start），夜盘+次日日盘为一个交易日区块"""
    log.info(f"{'*' * 80}")

# ==================== 纯函数（不依赖聚宽环境，可本地验证） ====================

def calc_ma_atr(highs, lows, closes, ma_period, atr_n):
    """输入完整日线序列（末位为准当日），返回等长的 (ma_series, atr_series)"""
    closes = pd.Series(closes, dtype=float)
    highs = pd.Series(highs, dtype=float)
    lows = pd.Series(lows, dtype=float)
    ma_series = closes.rolling(ma_period).mean().values
    prev_close = closes.shift(1)
    tr = pd.concat([highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()], axis=1).max(axis=1)
    atr_series = tr.rolling(atr_n).mean().values
    return ma_series, atr_series


def check_direction(ma_series, ma_slope_days):
    """方向：1 多头（MA 上行）/ -1 空头（MA 下行）/ 0 无（含数据不足与 NaN）"""
    if len(ma_series) <= ma_slope_days:
        return 0
    now, past = ma_series[-1], ma_series[-1 - ma_slope_days]
    if now > past:
        return 1
    if now < past:
        return -1
    return 0


def check_entry(direction, highs, lows, ma_series, price, atr, pullback_window,
                entry_max_dist_atr, pullback_max_break_atr):
    """入场条件 2-5（方向已确认）：回踩窗口 + 站回 + 距离上限 + 深失守过滤
    highs/lows/ma_series 为完整序列（末位为准当日 D）；0 = 该过滤项不限"""
    win_h = highs[-pullback_window:]
    win_l = lows[-pullback_window:]
    win_ma = ma_series[-pullback_window:]

    def _reject(why):
        log.debug(f"ENTRY_REJECT {why} dir={direction} price={price:.1f} ma={win_ma[-1]:.1f} atr={atr:.1f}")
        return False

    if direction == 1:
        if entry_max_dist_atr > 0 and price - win_ma[-1] > entry_max_dist_atr * atr:
            return _reject('距离超限')
        if price < win_ma[-1]:
            return _reject('未站回MA')
        # any 必须传 list：本环境 any 被遮蔽，生成器输入恒真
        _touch = [win_l[i] <= win_ma[i] for i in range(len(win_l))]
        if not any(_touch):
            return _reject('回踩窗口未触及MA')
        # any 必须传 list：本环境 any 被遮蔽，生成器输入恒真
        _breaks = [win_ma[i] - win_l[i] > pullback_max_break_atr * atr for i in range(len(win_l))]
        if pullback_max_break_atr > 0 and any(_breaks):
            return _reject('窗口内深失守')
    else:
        if entry_max_dist_atr > 0 and win_ma[-1] - price > entry_max_dist_atr * atr:
            return _reject('距离超限')
        if price > win_ma[-1]:
            return _reject('未站回MA')
        # any 必须传 list：本环境 any 被遮蔽，生成器输入恒真
        _touch = [win_h[i] >= win_ma[i] for i in range(len(win_h))]
        if not any(_touch):
            return _reject('回踩窗口未触及MA')
        # any 必须传 list：本环境 any 被遮蔽，生成器输入恒真
        _breaks = [win_h[i] - win_ma[i] > pullback_max_break_atr * atr for i in range(len(win_h))]
        if pullback_max_break_atr > 0 and any(_breaks):
            return _reject('窗口内深失守')
    log.debug(f"ENTRY_PASS dir={direction} price={price:.1f} ma={win_ma[-1]:.1f} atr={atr:.1f}")
    return True


def check_bar_dir(direction, price, prev_close, open_price):
    """入场日方向过滤（e，非严格 ≥/≤）：涨跌腿（vs 昨日真实收盘）+ 烛形腿（vs 准当日开盘）
    多头：price >= prev_close 且 price >= 今开；空头镜像（<=）"""
    if direction == 1:
        ok = price >= prev_close and price >= open_price
    else:
        ok = price <= prev_close and price <= open_price
    if not ok:
        log.debug(f"ENTRY_REJECT 入场日方向不符 dir={direction} price={price:.1f} "
                  f"prev_close={prev_close:.1f} open={open_price:.1f}")
    return ok


def check_prev_ceiling(direction, price, prev_close, ma_prev, atr_prev, entry_max_dist_atr):
    """价格天花板（f，仅约束次日）：D−1 收盘超其自身距离上限 → D 日入场价须严格优于 D−1"""
    if not (atr_prev > 0):                                 # NaN / 数据不足 → 放行
        return True
    if direction == 1:
        if prev_close > ma_prev + entry_max_dist_atr * atr_prev and not price < prev_close:
            log.debug(f"ENTRY_REJECT 价格天花板未翻案 dir={direction} "
                      f"price={price:.1f} prev_close={prev_close:.1f}")
            return False
    else:
        if prev_close < ma_prev - entry_max_dist_atr * atr_prev and not price > prev_close:
            log.debug(f"ENTRY_REJECT 价格天花板未翻案 dir={direction} "
                      f"price={price:.1f} prev_close={prev_close:.1f}")
            return False
    return True


def check_deep_drop(direction, highs, lows, closes, atr, params):
    """深跌后"不创新低"确认（d）：近期存在 ≥retrace×ATR 的下跌腿（或单日跌幅 ≥day×ATR）
    且"本轮下跌腿的最低点日"（空头镜像：反弹腿最高点日）距今日 < wait_days 个交易日 → 拦；阈值 0 = 关闭该项
    基准日 = 最近一段满足幅度要求的"腿"的极值日（取最近的合格腿起点，天然排除其后的弱反弹顶）"""
    L = params['deep_drop_lookback']
    retr = params['deep_drop_retrace_atr']
    day = params['deep_drop_day_atr']
    wait_need = params['deep_drop_wait_days']
    if len(highs) < L or (retr <= 0 and day <= 0) or wait_need <= 0:
        return True
    start = len(highs) - L                 # 回看窗口起点（含今日）
    if direction == 1:
        p_idx = None                                     # 最近一段"下跌腿"的起点（自其高点回落 ≥ retr×ATR）
        if retr > 0:
            for p in range(len(highs) - 2, start - 1, -1):
                seg_l = lows[p + 1:]
                if highs[p] - min(seg_l) >= retr * atr:
                    p_idx = p
                    break
        if p_idx is None:                                # 无足幅下跌腿 → 仅单日跌幅触发（兜底：窗口最低点日）
            if day > 0 and any([closes[i - 1] - closes[i] >= day * atr for i in range(start, len(closes))]):
                l_idx = start + lows[start:].index(min(lows[start:]))
                amp = max(highs[start:l_idx + 1]) - lows[l_idx]
            else:
                return True
        else:
            seg_l = lows[p_idx + 1:]
            l_idx = p_idx + 1 + seg_l.index(min(seg_l))  # 基准日：本轮下跌腿的最低点日
            amp = highs[p_idx] - lows[l_idx]
    else:
        p_idx = None                                     # 空头镜像：最近一段"反弹腿"的起点（自其低点回升 ≥ retr×ATR）
        if retr > 0:
            for p in range(len(highs) - 2, start - 1, -1):
                seg_h = highs[p + 1:]
                if max(seg_h) - lows[p] >= retr * atr:
                    p_idx = p
                    break
        if p_idx is None:                                # 无足幅反弹腿 → 仅单日涨幅触发（兜底：窗口最高点日）
            if day > 0 and any([closes[i] - closes[i - 1] >= day * atr for i in range(start, len(closes))]):
                l_idx = start + highs[start:].index(max(highs[start:]))
                amp = highs[l_idx] - min(lows[start:l_idx + 1])
            else:
                return True
        else:
            seg_h = highs[p_idx + 1:]
            l_idx = p_idx + 1 + seg_h.index(max(seg_h))  # 镜像基准日：本轮反弹腿的最高点日
            amp = highs[l_idx] - lows[p_idx]
    wait = (len(highs) - 1) - l_idx        # 基准日距今日的交易日数
    if wait < wait_need:
        log.debug(f"ENTRY_REJECT 深跌未企稳 wait={wait} (需≥{wait_need}) amp={amp:.1f} atr={atr:.1f}")
        return False
    return True


def check_signal_exit(direction, price, ma_last, atr, signal_exit_dist_atr):
    """信号反转：多头 P1445 < MA20 − k×ATR（空头镜像）"""
    if direction == 1:
        line = ma_last - signal_exit_dist_atr * atr
        hit = price < line
    else:
        line = ma_last + signal_exit_dist_atr * atr
        hit = price > line
    if hit:
        log.debug(f"EXIT_CHECK dir={direction} price={price:.1f} line={line:.1f} hit={hit}")
    return hit


def update_stop(side, extreme, stop_line, atr_lock, bar_high, bar_low, stop_loss_dist_atr):
    """移动止损推进（吊灯式，只朝有利方向）：返回 (extreme, stop_line)"""
    if side == 'long':
        extreme = max(extreme, bar_high)
        return extreme, max(stop_line, extreme - stop_loss_dist_atr * atr_lock)
    extreme = min(extreme, bar_low)
    return extreme, min(stop_line, extreme + stop_loss_dist_atr * atr_lock)


def check_stop_hit(side, stop_line, bar_high, bar_low):
    """分钟 bar 是否触达止损线"""
    if side == 'long':
        hit = bar_low <= stop_line
    else:
        hit = bar_high >= stop_line
    if hit:
        log.debug(f"STOP_CHECK side={side} stop={stop_line:.1f} bar_low={bar_low:.1f} bar_high={bar_high:.1f} hit={hit}")
    return hit


def calc_lots(equity, position_pct, price, multiplier, margin_rate):
    """仓位法手数：lots = max(1, floor(总资产×仓位比例 / (价格×乘数×保证金率)))"""
    denom = price * multiplier * margin_rate
    if denom <= 0:
        return 1
    return max(1, int(equity * position_pct / denom))


def fit_lots_to_margin(lots, price, multiplier, margin_rate, available_cash):
    """保证金约束：按可用资金逐手减仓；1 手都开不出返回 0"""
    margin_per_lot = price * multiplier * margin_rate
    if margin_per_lot <= 0:
        return lots
    return min(lots, int(available_cash / margin_per_lot))


def dir_to_side(direction):
    return 'long' if direction == 1 else 'short'


def side_to_dir(side):
    return 1 if side == 'long' else -1


def quasi_daily_from_bars(bar_dates, bar_opens, bar_highs, bar_lows, bar_closes, session_start):
    """从分钟 bar 过滤出当前 session（前一交易日 20:00 起，含夜盘），合成准当日 open/high/low/close"""
    open_ = hi = lo = close = None
    for ts, o, h, l, c in zip(bar_dates, bar_opens, bar_highs, bar_lows, bar_closes):
        if ts >= session_start:
            if hi is None:
                open_ = o                    # session 首根分钟价 = 准当日开盘
            hi = h if hi is None else max(hi, h)
            lo = l if lo is None else min(lo, l)
            close = c
    if hi is None:
        return None
    return {'open': open_, 'high': hi, 'low': lo, 'close': close}

# ==================== 平台取数 ====================

def current_minute_bar(ctx, contract):
    """当前分钟 bar（合约不在交易时段、数据过期时返回 None），用于止损判定"""
    if contract is None:
        return None
    bars = get_bars(contract, 1, '1m', ['date', 'high', 'low', 'close'], include_now=True)
    if bars is None or len(bars) == 0:
        return None
    row = bars[-1]                     # get_bars 返回 numpy 结构化数组（非 DataFrame），用整数索引取行
    if abs((pd.Timestamp(ctx.current_dt) - pd.Timestamp(row['date'])).total_seconds()) > 60:
        return None
    return {'high': row['high'], 'low': row['low'], 'close': row['close']}


def build_quasi_daily(ctx, contract):
    """准当日日线：当前 session（前一交易日 20:00 起，含夜盘）分钟 bar 合成"""
    bars = get_bars(contract, 400, '1m', ['date', 'open', 'high', 'low', 'close'], include_now=True)
    if bars is None or len(bars) == 0:
        return None
    session_start = pd.Timestamp(datetime.datetime.combine(ctx.previous_date, datetime.time(20, 0)))
    return quasi_daily_from_bars([pd.Timestamp(t) for t in bars['date']],
                                 bars['open'], bars['high'], bars['low'], bars['close'], session_start)


def compute_signal_data(ctx, contract):
    """日线（前 29 根完整 + 准当日）→ MA/ATR 序列、方向、现价；数据不可用返回 None
    说明：attribute_history(unit='1d') 取 30 根，仅用后 29 根完整日线，末位拼接准当日"""
    p = g.params
    if contract is None:
        return None
    n_hist = max(p['ma_period'], p['atr_n']) + 10    # 序列长度随参数自适应（默认 20/20 → 30）
    hist = attribute_history(contract, n_hist + 1, '1d', ['high', 'low', 'close'])
    q = build_quasi_daily(ctx, contract)
    if hist is None or len(hist) == 0 or q is None:
        return None
    hist = hist.iloc[-n_hist:]
    highs = list(hist['high'].values) + [q['high']]
    lows = list(hist['low'].values) + [q['low']]
    closes = list(hist['close'].values) + [q['close']]
    dates = [ts.date() for ts in hist.index] + [ctx.current_dt.date()]
    price = closes[-1]                     # 今日 14:45 价（统一从序列末位取）
    prev_close = closes[-2]                # 昨日真实收盘（e 涨跌腿）
    ma_series, atr_series = calc_ma_atr(highs, lows, closes, p['ma_period'], p['atr_n'])
    ma_prev = ma_series[-2]                # 昨日 MA（f 价格天花板）
    atr_prev = atr_series[-2]              # 昨日 ATR（f 价格天花板）
    return {'contract': contract, 'ma': ma_series, 'atr': atr_series[-1], 'price': price,
            'highs': highs, 'lows': lows, 'closes': closes, 'dates': dates,
            'prev_close': prev_close, 'open': q['open'],
            'ma_prev': ma_prev, 'atr_prev': atr_prev,
            'direction': check_direction(ma_series, p['ma_slope_days'])}

# ==================== 订单封装 ====================

def has_open_order(contract):
    """该合约是否已有未成交委托（停板等待，不重复下单）"""
    for o in get_open_orders().values():
        if o.security == contract:
            return True
    return False


def warn_once(key, msg):
    """按 key 去重告警：仅状态变化时打印，避免每分钟刷屏"""
    if g.warned.get(key) != msg:
        log.warn(msg)
        g.warned[key] = msg


def open_leg(ctx, direction, lots, atr_lock, reason, ma_at_entry=None):
    """市价开一笔 leg；成交后登记 g.legs，未成交（停板）告警不重试"""
    p = g.params
    c = g.current_contract
    if has_open_order(c):
        warn_once('open_' + c, f"{log_pfx()} ❌ WARN open pending exists {c}")
        return False
    price = get_current_data()[c].last_price
    lots = fit_lots_to_margin(lots, price, p['multiplier'], p['margin_rate'],
                              ctx.subportfolios[0].available_cash)
    if lots < 1:
        log.warn(f"{log_pfx()} ❌ WARN open skip margin insufficient {c} price={price:.1f}")
        return False
    side = dir_to_side(direction)
    o = order(c, lots, side=side)
    if o is None or o.filled < lots:
        warn_once('open_' + c, f"{log_pfx()} ❌ WARN open unfilled (limit board?) {c} lots={lots}")
        return False
    stop_line = o.price - p['stop_loss_dist_atr'] * atr_lock if direction == 1 \
        else o.price + p['stop_loss_dist_atr'] * atr_lock
    g.legs.append({'contract': c, 'side': side, 'lots': lots, 'entry_price': o.price,
                   'entry_date': ctx.current_dt.date(), 'atr_lock': atr_lock,
                   'extreme': o.price, 'stop_line': stop_line, 'ma_at_entry': ma_at_entry})
    log.info(f"{log_pfx()} ✅{trade_mark(side, reason)} {reason.upper()} {side} {c} {lots}@{o.price:.1f} "
             f"atr_lock={atr_lock:.1f} stop={stop_line:.1f} equity={ctx.portfolio.total_value:.0f}")
    g.warned.pop('open_' + c, None)
    if reason == 'add':
        g.adds += 1
    return True


def close_leg(ctx, leg, reason):
    """市价平掉单笔 leg；成交后移除并维护全平状态，未成交（停板）告警等待"""
    c = leg['contract']
    if has_open_order(c):
        warn_once('close_' + c, f"{log_pfx()} ❌ WARN close pending exists {c}")
        return False
    # 平台实测：order(数量>0, side='short') 是卖出开空；平仓（多/空）统一用负数量
    amt = -leg['lots']
    o = order(c, amt, side=leg['side'])
    if o is None or o.filled < leg['lots']:
        warn_once('close_' + c, f"{log_pfx()} ❌ WARN close unfilled (limit board?) {c} {leg['side']} "
                                f"lots={leg['lots']} reason={reason}")
        return False
    pnl = (o.price - leg['entry_price']) * leg['lots'] * g.params['multiplier'] * side_to_dir(leg['side'])
    log.info(f"{log_pfx()} ✅{trade_mark(leg['side'], reason)} CLOSE {leg['side']} {c} {leg['lots']}@{o.price:.1f} "
             f"pnl={pnl:.0f} reason={reason} "
             f"entry={leg['entry_price']:.1f} stop={leg['stop_line']:.1f} atr_lock={leg['atr_lock']:.1f}")
    g.legs.remove(leg)
    g.warned.pop('close_' + c, None)
    if not g.legs:
        g.adds = 0
        g.last_flat_date = ctx.current_dt.date()
        log.info(f"{log_pfx()} ✅ 清仓完成，当前空仓")
    return True


def close_all_legs(ctx, reason):
    """全部平仓（信号反转）；未成交的 leg 留待下一 bar 继续"""
    for leg in list(g.legs):
        close_leg(ctx, leg, reason)


def check_state_consistency(ctx):
    """g.legs 与账户实际持仓每 bar 比对；不一致→告警并按实际重建（设计文档 §4）"""
    sp = ctx.subportfolios[0]
    actual = {}
    for side, pos_map in (('long', sp.long_positions), ('short', sp.short_positions)):
        for c, pos in pos_map.items():
            if pos.total_amount > 0:
                actual[(c, side)] = actual.get((c, side), 0) + pos.total_amount
    tracked = {}
    for leg in g.legs:
        key = (leg['contract'], leg['side'])
        tracked[key] = tracked.get(key, 0) + leg['lots']
    if actual == tracked:
        return
    if g.migrating is not None:                          # 移仓中间态：两腿成交后统一修正
        return
    log.warn(f"{log_pfx()} STATE_MISMATCH tracked={tracked} actual={actual} -> rebuild from actual")
    g.legs = []
    for (c, side), amt in actual.items():
        d = compute_signal_data(ctx, c)
        atr = d['atr'] if d is not None else 0.0
        price = get_current_data()[c].last_price
        stop_line = price - g.params['stop_loss_dist_atr'] * atr if side == 'long' \
            else price + g.params['stop_loss_dist_atr'] * atr
        g.legs.append({'contract': c, 'side': side, 'lots': amt, 'entry_price': price,
                       'entry_date': ctx.current_dt.date(), 'atr_lock': atr,
                       'extreme': price, 'stop_line': stop_line,
                       'ma_at_entry': d['ma'][-1] if d is not None else None})
    if not g.legs:
        g.adds = 0
        g.last_flat_date = ctx.current_dt.date()

# ==================== 分钟止损 ====================

def minute_risk_checks(ctx, bar):
    """每笔移动止损推进与触发；停板未成交由 close_leg 告警等待"""
    if g.migrating is not None:                          # 移仓中间态：暂停止损（两腿未定）
        return
    if bar is None or not g.legs:
        return
    p = g.params
    for leg in list(g.legs):
        leg['extreme'], leg['stop_line'] = update_stop(
            leg['side'], leg['extreme'], leg['stop_line'], leg['atr_lock'],
            bar['high'], bar['low'], p['stop_loss_dist_atr'])
        if check_stop_hit(leg['side'], leg['stop_line'], bar['high'], bar['low']):
            log.info(f"{log_pfx()} STOP_HIT {leg['contract']} {leg['side']} stop={leg['stop_line']:.1f} "
                     f"bar_low={bar['low']:.1f} bar_high={bar['high']:.1f}")
            close_leg(ctx, leg, 'stop')

# ==================== 14:45 信号流程 ====================

def entry_ok(data, params):
    """v2 入场入口：方向由 fixed_dir 给定（为 0 时才用 MA20 斜率判断）；
    空仓时价格在趋势一侧即可入场（多: price≥MA20 / 空: price≤MA20）；
    entry_max_dist_atr>0 时附加距离门槛：入场价不得偏离 MA 超过该 ATR 倍数（防追涨/追跌）"""
    if data is None:
        return False
    direction = params['fixed_dir'] or data['direction']
    if direction == 0:
        return False
    ma = data['ma'][-1]
    cap = params['entry_max_dist_atr']
    if direction == 1:
        if data['price'] < ma:
            return False
        return not (cap > 0 and data['price'] - ma > cap * data['atr'])
    if data['price'] > ma:
        return False
    return not (cap > 0 and ma - data['price'] > cap * data['atr'])


def cooldown_ok(ctx):
    """冷却期：平仓当日及其后 cooldown_days 个交易日内禁止开新仓（0 = 仅平仓当日）"""
    p = g.params
    if g.last_flat_date is None:
        return True
    days = get_trade_days(start_date=g.last_flat_date, end_date=ctx.current_dt.date())
    elapsed = len(days) - 1
    return not (elapsed <= p['cooldown_days'])


def try_add(ctx, d):
    """加仓：entry_ok + 正浮盈 + 新回踩/新高在前/MA 推进（R1 三条件）+ 次数限制（设计文档 2.5 修订）"""
    p = g.params
    if not g.legs or g.migrating is not None or g.adds >= p['max_adds']:
        return
    side = g.legs[0]['side']
    if d is None:
        return
    direction = p['fixed_dir'] or d['direction']
    if direction == 0 or dir_to_side(direction) != side:
        return
    if not entry_ok(d, p):
        return
    last = g.legs[-1]
    # 固定规则：金字塔不允许向下加仓
    gap = d['price'] - last['entry_price'] if side == 'long' else last['entry_price'] - d['price']
    if gap <= 0:
        log.debug(f"ADD_REJECT 向下加仓 gap={gap:.1f}")
        return
    # ① 新回踩确认：窗口内触线日必须晚于上一笔入场日（窗口里的老触线作废）
    win_n = p['pullback_window']
    win_l = d['lows'][-win_n:]
    win_h = d['highs'][-win_n:]
    win_ma = d['ma'][-win_n:]
    win_d = d['dates'][-win_n:]
    entry_d = last['entry_date']
    if side == 'long':
        fresh = [i for i in range(win_n) if win_l[i] <= win_ma[i] and win_d[i] > entry_d]
    else:
        fresh = [i for i in range(win_n) if win_h[i] >= win_ma[i] and win_d[i] > entry_d]
    if not fresh:
        log.debug(f"ADD_REJECT 窗口内无新回踩 entry_date={entry_d}")
        return
    # 趋势实质推进（原"价差"条件的设计本意）：MA20 较最近一笔入场时推进 ≥ add_min_dist_atr×ATR
    if p['add_min_dist_atr'] > 0:
        ma_entry = last.get('ma_at_entry')
        if ma_entry is None:                             # 状态缺失（如重建补记），保守跳过
            log.debug("ADD_REJECT 缺少 ma_at_entry，跳过")
            return
        ma_advance = d['ma'][-1] - ma_entry if side == 'long' else ma_entry - d['ma'][-1]
        if ma_advance < p['add_min_dist_atr'] * d['atr']:
            log.debug(f"ADD_REJECT MA未实质推进 advance={ma_advance:.1f} need={p['add_min_dist_atr'] * d['atr']:.1f}")
            return
    lots = calc_lots(ctx.portfolio.total_value, p['position_pct'], d['price'],
                     p['multiplier'], p['margin_rate'])
    open_leg(ctx, direction, lots, d['atr'], 'add', d['ma'][-1])


def try_open(ctx, d):
    """开仓：空仓 + 冷却期已过 + 入场信号（设计文档 2.3/2.6）"""
    p = g.params
    if g.legs or g.migrating is not None:
        return
    if not cooldown_ok(ctx):
        return
    if not entry_ok(d, p):
        return
    direction = p['fixed_dir'] or d['direction']
    lots = calc_lots(ctx.portfolio.total_value, p['position_pct'], d['price'],
                     p['multiplier'], p['margin_rate'])
    open_leg(ctx, direction, lots, d['atr'], 'entry', d['ma'][-1])


def log_daily(d, note=""):
    """DAILY 结构日志：hi/ma/open/price/lo 按数值降序分行，直观展示结构；note 用于换约等补充标记"""
    if d is None:
        return
    levels = sorted([('hi', d['highs'][-1]), ('ma', d['ma'][-1]), ('open', d['open']),
                     ('price', d['price']), ('lo', d['lows'][-1])],
                    key=lambda x: x[1], reverse=True)
    levels_str = "\n".join(f"  {k}={v:.1f}" for k, v in levels)
    log.info(f"{log_pfx()} DAILY {g.current_contract} dir={d['direction']} atr={d['atr']:.1f} "
             f"legs={len(g.legs)} adds={g.adds}{note}\n{levels_str}")


def signal_routine(ctx):
    """14:45 信号流程：信号反转 → 移仓 → 加仓 → 开仓"""
    p = g.params
    if g.current_contract is None:                       # 首个 14:45 解析主力
        g.current_contract = get_dominant_future(p['symbol'])
        log.info(f"{log_pfx()} dominant init {g.current_contract}")
    d = compute_signal_data(ctx, g.current_contract)
    prev_contract = g.current_contract
    log_daily(d)
    if g.legs and d is not None and g.migrating is None and p['signal_exit_dist_atr'] > 0:  # 移仓中间态：仅推进移仓
        direction = side_to_dir(g.legs[0]['side'])
        if check_signal_exit(direction, d['price'], d['ma'][-1], d['atr'], p['signal_exit_dist_atr']):
            line = d['ma'][-1] - direction * p['signal_exit_dist_atr'] * d['atr']
            log.info(f"{log_pfx()} SIGNAL_EXIT {g.current_contract} price={d['price']:.1f} line={line:.1f}")
            close_all_legs(ctx, 'signal_exit')
    check_dominant(ctx)                              # 移仓（设计文档 2.8 执行顺序）
    if g.current_contract != prev_contract:
        d = compute_signal_data(ctx, g.current_contract)   # 换约后按新合约重取
        log_daily(d, note=" (换约后重取)")                  # 换约日补打新合约 DAILY，便于对照入场判据
    try_add(ctx, d)
    try_open(ctx, d)


def risk_and_signal_routine(ctx):
    """分钟单入口：一致性校验 → 止损 → 14:45 信号流程（设计文档 §4）"""
    try:
        p = g.params
        check_state_consistency(ctx)
        bar = current_minute_bar(ctx, g.current_contract)
        minute_risk_checks(ctx, bar)
        retry_migration(ctx)
        if ctx.current_dt.strftime('%H:%M') != p['signal_time']:
            return
        if g.last_signal_date == ctx.current_dt.date():
            return
        signal_routine(ctx)
        g.last_signal_date = ctx.current_dt.date()
    except Exception as e:
        log.error(f"{log_pfx()} minute routine error: {e}")

# ==================== 移仓 ====================

def check_dominant(ctx):
    """主力切换检查：连续 2 个交易日确认；有持仓启动移仓，空仓仅更新记录（设计文档 2.8）"""
    if g.migrating is not None:                          # 移仓进行中：由 retry_migration 推进
        return
    dom = get_dominant_future(g.params['symbol'])
    if dom == g.current_contract:
        g.dominant_counter = {'contract': None, 'days': 0}
        return
    if g.dominant_counter['contract'] == dom:
        g.dominant_counter['days'] += 1
    else:
        g.dominant_counter = {'contract': dom, 'days': 1}
    if g.dominant_counter['days'] < 2:
        return
    g.dominant_counter = {'contract': None, 'days': 0}
    if not g.legs:
        log.info(f"{log_pfx()} dominant switch {g.current_contract} -> {dom} (flat)")
        g.current_contract = dom
        return
    start_migration(ctx, dom)


def start_migration(ctx, new_contract):
    """启动移仓：先开新后平旧"""
    side = g.legs[0]['side']
    # 同样传 list：防内置函数被遮蔽，勿改生成器
    _lots = [leg['lots'] for leg in g.legs]
    total = sum(_lots)
    g.migrating = {'old': g.current_contract, 'new': new_contract, 'side': side, 'total': total,
                   'new_price': None, 'old_price': None}
    log.info(f"{log_pfx()} MIGRATE start {g.current_contract}->{new_contract} side={side} lots={total}")
    retry_migration(ctx)


def retry_migration(ctx):
    """推进移仓：每 bar 重试未成交腿；两腿全成交后统一更新 legs 并做价差平移"""
    m = g.migrating
    if m is None:
        return
    if has_open_order(m['new']) or has_open_order(m['old']):
        return
    if m['new_price'] is None:
        o = order(m['new'], m['total'], side=m['side'])
        if o is None or o.filled < m['total']:
            warn_once('mig_new', f"{log_pfx()} WARN migrate open unfilled {m['new']} lots={m['total']}")
            return
        m['new_price'] = o.price
        g.warned.pop('mig_new', None)
        log.info(f"{log_pfx()} MIGRATE open {m['new']} {m['side']} {m['total']}@{o.price:.1f}")
    if m['old_price'] is None:
        amt = -m['total']   # 同 close_leg：平仓统一用负数量（正数量对空头是加仓）
        o = order(m['old'], amt, side=m['side'])
        if o is None or o.filled < m['total']:
            warn_once('mig_old', f"{log_pfx()} WARN migrate close unfilled {m['old']} lots={m['total']}")
            return
        m['old_price'] = o.price
        g.warned.pop('mig_old', None)
        mult = g.params['multiplier']
        sd = side_to_dir(m['side'])
        pnl = sum([(o.price - leg['entry_price']) * leg['lots'] * mult * sd for leg in g.legs])
        log.info(f"{log_pfx()} MIGRATE close {m['old']} {m['side']} {m['total']}@{o.price:.1f} pnl={pnl:.0f}")
    delta = m['new_price'] - m['old_price']
    d = compute_signal_data(ctx, m['new'])
    if d is None:
        warn_once('mig_atr', f"{log_pfx()} WARN migrate new contract data missing {m['new']}")
    for leg in g.legs:
        leg['contract'] = m['new']
        leg['entry_price'] = m['new_price']
        if d is not None:
            leg['atr_lock'] = d['atr']
            leg['ma_at_entry'] = d['ma'][-1]   # 换约后按新合约当前 MA 刷新（避免跨合约基差）
        leg['stop_line'] += delta
        leg['extreme'] += delta
    g.current_contract = m['new']
    g.migrating = None
    atr_str = f"{d['atr']:.1f}" if d is not None else "n/a"
    log.info(f"{log_pfx()} MIGRATE done {m['old']}->{m['new']} delta={delta:.1f} atr_lock={atr_str}")

# ==================== 初始化 ====================

def initialize(ctx):
    """初始化（设计文档 §4）"""
    try:
        init_cash = ctx.portfolio.starting_cash
        set_subportfolios([SubPortfolioConfig(cash=init_cash, type='futures')])
        set_option('use_real_price', True)
        set_option('avoid_future_data', True)
        set_option('futures_margin_rate', PARAMS['margin_rate'])
        set_order_cost(OrderCost(open_commission=0.000023, close_commission=0.000023,
                                 close_today_commission=0.000023, open_tax=0, close_tax=0,
                                 min_commission=0), type='futures', ref=PARAMS['symbol'])  # 费率按品种调整（当前为 RU 口径）
        set_slippage(PriceRelatedSlippage(0.0003), type='futures')
        set_benchmark(f"{PARAMS['symbol']}9999.{PARAMS['exchange']}")   # 基准：品种指数（换品种同步改 exchange）
        log.set_level('order', 'error')        # 订单日志级别：只显示错误信息
        log.set_level('system', 'error')       # 系统日志级别：只显示错误信息
        log.set_level('strategy', 'info')      # 策略日志级别：显示普通信息及以上

        g.params = dict(PARAMS)
        g.current_contract = None          # 首个 14:45 解析主力
        g.legs = []
        g.adds = 0
        g.last_signal_date = None
        g.last_flat_date = None
        g.dominant_counter = {'contract': None, 'days': 0}
        g.migrating = None
        g.warned = {}

        param_lines = '\n'.join(f"  {k:<23}: {v}" for k, v in sorted(g.params.items()))
        log.info('PARAMS:\n%s' % param_lines)
        run_daily(daily_log_separator, time=PARAMS['night_start'] or '09:00')  # 每日日志分隔线（交易日起点）
        run_daily(risk_and_signal_routine, time='every_bar')    # 回测频率需选"分钟"；平台 every_bar 只覆盖日盘
        # ---- 夜盘分钟任务 ----
        # 实测：平台 every_bar 不覆盖夜盘（夜盘 bar 不触发例程），夜盘止损保护会失效；
        # 而定时任务在夜盘可正常触发、get_bars 能取到新鲜夜盘分钟数据，故按分钟单独注册、复用同一例程。
        # 时段来自参数 night_start/night_end（RU/P 均为 21:00~23:00；空串 = 无夜盘品种）
        if PARAMS['night_start'] and PARAMS['night_end']:
            h0, m0 = [int(x) for x in PARAMS['night_start'].split(':')]
            h1, m1 = [int(x) for x in PARAMS['night_end'].split(':')]
            t = datetime.datetime(2000, 1, 1, h0, m0)
            end_t = datetime.datetime(2000, 1, 1, h1, m1)
            night_task_count = 0
            while t <= end_t:
                run_daily(risk_and_signal_routine, time=t.strftime('%H:%M'))
                night_task_count += 1
                t += datetime.timedelta(minutes=1)
            log.info(f"夜盘分钟任务注册完成: {night_task_count}个 ({PARAMS['night_start']}~{PARAMS['night_end']}, 复用 risk_and_signal_routine)")
        else:
            log.info("夜盘分钟任务未注册（night_start/night_end 为空）")
    except Exception as e:
        log.error(f"{log_pfx()} init error: {e}")
