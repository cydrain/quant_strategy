# ============================================================
# 橡胶 RU 日线趋势回踩策略 v1.0（聚宽回测）
# 设计文档: docs/superpowers/specs/2026-09-17-ru-trend-pullback-design.md
#   - 信号: 日线 MA20 方向 + 回踩窗口站回，每交易日 14:45 判定执行
#   - 出场: 信号反转(MA20 失守缓冲) / 初始止损 / 移动止损（每分钟，含夜盘）
#   - 加仓: 金字塔最多 2 次
#   - 移仓: 主力切换连续 2 日确认 + 先开新后平旧 + 止损价差平移
# 回测设置: 期货账户 / 50 万 / 分钟频率 / 2018-01-01 起
# ============================================================
from jqdata import *
import datetime
import pandas as pd

# ==================== 参数（集中，initialize 时拷入 g.params） ====================
PARAMS = {
    'ma_period': 20,              # MA20 周期（方向/回踩/出场共用）
    'ma_slope_days': 3,           # MA20 上行判定跨度（交易日）
    'pullback_window': 3,         # 回踩窗口交易日数，1 = 同根 K 线规则
    'entry_max_dist_atr': 1.0,    # 站回日距 MA20 的距离上限（ATR 倍数），0 = 不限
    'pullback_max_break_atr': 0,  # 回踩窗口内单日跌破深度上限（ATR 倍数），0 = 不限
    'cooldown_days': 0,           # 全平后禁止开新仓的交易日数
    'atr_n': 20,                  # ATR 周期
    'stop_loss_dist_atr': 2.0,    # 止损距离（ATR 倍数），初始与移动共用
    'signal_exit_dist_atr': 0.5,  # 信号反转失守缓冲（ATR 倍数）
    'risk_pct': 0.02,             # 单笔风险预算
    'max_adds': 2,                # 最大加仓次数
    'add_min_dist_atr': 0.5,      # 加仓最小距离（ATR 倍数），0 = 仅要求正浮盈
    'signal_time': '14:45',       # 信号判定时刻
    'multiplier': 10,             # RU 合约乘数
    'margin_rate': 0.12,          # 保证金率（回测设定值）
}

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
    if direction == 1:
        if entry_max_dist_atr > 0 and price - win_ma[-1] > entry_max_dist_atr * atr:
            return False
        if price < win_ma[-1]:
            return False
        if not any(win_l[i] <= win_ma[i] for i in range(len(win_l))):
            return False
        if pullback_max_break_atr > 0 and any(win_ma[i] - win_l[i] > pullback_max_break_atr * atr
                                              for i in range(len(win_l))):
            return False
    else:
        if entry_max_dist_atr > 0 and win_ma[-1] - price > entry_max_dist_atr * atr:
            return False
        if price > win_ma[-1]:
            return False
        if not any(win_h[i] >= win_ma[i] for i in range(len(win_h))):
            return False
        if pullback_max_break_atr > 0 and any(win_h[i] - win_ma[i] > pullback_max_break_atr * atr
                                              for i in range(len(win_h))):
            return False
    return True


def check_signal_exit(direction, price, ma_last, atr, signal_exit_dist_atr):
    """信号反转：多头 P1445 < MA20 − k×ATR（空头镜像）"""
    if direction == 1:
        return price < ma_last - signal_exit_dist_atr * atr
    return price > ma_last + signal_exit_dist_atr * atr


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
        return bar_low <= stop_line
    return bar_high >= stop_line


def calc_lots(equity, risk_pct, stop_loss_dist_atr, atr_lock, multiplier):
    """风险预算法手数：lots = max(1, floor(权益×R / (k×ATR×乘数)))"""
    denom = stop_loss_dist_atr * atr_lock * multiplier
    if denom <= 0:
        return 1
    return max(1, int(equity * risk_pct / denom))


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


def quasi_daily_from_bars(bar_dates, bar_highs, bar_lows, bar_closes, session_start):
    """从分钟 bar 过滤出当前 session（前一交易日 20:00 起，含夜盘），合成准当日 high/low/close"""
    hi = lo = close = None
    for ts, h, l, c in zip(bar_dates, bar_highs, bar_lows, bar_closes):
        if ts >= session_start:
            hi = h if hi is None else max(hi, h)
            lo = l if lo is None else min(lo, l)
            close = c
    if hi is None:
        return None
    return {'high': hi, 'low': lo, 'close': close}

# ==================== 平台取数 ====================

def current_minute_bar(contract, context):
    """当前分钟 bar（合约不在交易时段、数据过期时返回 None），用于止损判定"""
    if contract is None:
        return None
    bars = get_bars(contract, 1, '1m', ['date', 'high', 'low', 'close'], include_now=True)
    if bars is None or len(bars) == 0:
        return None
    row = bars.iloc[-1]
    if abs((pd.Timestamp(context.current_dt) - pd.Timestamp(row['date'])).total_seconds()) > 60:
        return None
    return {'high': row['high'], 'low': row['low'], 'close': row['close']}


def build_quasi_daily(contract, context):
    """准当日日线：当前 session（前一交易日 20:00 起，含夜盘）分钟 bar 合成"""
    bars = get_bars(contract, 400, '1m', ['date', 'high', 'low', 'close'], include_now=True)
    if bars is None or len(bars) == 0:
        return None
    session_start = pd.Timestamp(datetime.datetime.combine(context.previous_date, datetime.time(20, 0)))
    return quasi_daily_from_bars([pd.Timestamp(t) for t in bars['date']],
                                 bars['high'], bars['low'], bars['close'], session_start)


def compute_signal_data(contract, context):
    """日线（前 29 根完整 + 准当日）→ MA/ATR 序列、方向、现价；数据不可用返回 None
    说明：attribute_history(unit='1d') 取 30 根，仅用后 29 根完整日线，末位拼接准当日"""
    p = g.params
    if contract is None:
        return None
    hist = attribute_history(contract, 30, '1d', ['high', 'low', 'close'])
    q = build_quasi_daily(contract, context)
    if hist is None or len(hist) == 0 or q is None:
        return None
    hist = hist.iloc[-29:]
    highs = list(hist['high'].values) + [q['high']]
    lows = list(hist['low'].values) + [q['low']]
    closes = list(hist['close'].values) + [q['close']]
    ma_series, atr_series = calc_ma_atr(highs, lows, closes, p['ma_period'], p['atr_n'])
    return {'contract': contract, 'ma': ma_series, 'atr': atr_series[-1], 'price': q['close'],
            'highs': highs, 'lows': lows,
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


def open_leg(context, direction, lots, atr_lock, reason):
    """市价开一笔 leg；成交后登记 g.legs，未成交（停板）告警不重试"""
    p = g.params
    c = g.current_contract
    if has_open_order(c):
        warn_once('open_' + c, f"[RU] WARN open pending exists {c}")
        return False
    price = get_current_data()[c].last_price
    lots = fit_lots_to_margin(lots, price, p['multiplier'], p['margin_rate'],
                              context.subportfolios[0].available_cash)
    if lots < 1:
        log.warn(f"[RU] WARN open skip margin insufficient {c} price={price:.1f}")
        return False
    side = dir_to_side(direction)
    o = order(c, lots, side=side)
    if o is None or o.filled < lots:
        warn_once('open_' + c, f"[RU] WARN open unfilled (limit board?) {c} lots={lots}")
        return False
    stop_line = o.price - p['stop_loss_dist_atr'] * atr_lock if direction == 1 \
        else o.price + p['stop_loss_dist_atr'] * atr_lock
    g.legs.append({'contract': c, 'side': side, 'lots': lots, 'entry_price': o.price,
                   'entry_date': context.current_dt.date(), 'atr_lock': atr_lock,
                   'extreme': o.price, 'stop_line': stop_line})
    log.info(f"[RU] {reason.upper()} {side} {c} {lots}@{o.price:.1f} atr_lock={atr_lock:.1f} "
             f"stop={stop_line:.1f} equity={context.portfolio.total_value:.0f}")
    g.warned.pop('open_' + c, None)
    if reason == 'add':
        g.adds += 1
    return True


def close_leg(context, leg, reason):
    """市价平掉单笔 leg；成交后移除并维护全平状态，未成交（停板）告警等待"""
    c = leg['contract']
    if has_open_order(c):
        warn_once('close_' + c, f"[RU] WARN close pending exists {c}")
        return False
    amt = -leg['lots'] if leg['side'] == 'long' else leg['lots']
    o = order(c, amt, side=leg['side'])
    if o is None or o.filled < leg['lots']:
        warn_once('close_' + c, f"[RU] WARN close unfilled (limit board?) {c} {leg['side']} "
                                f"lots={leg['lots']} reason={reason}")
        return False
    pnl = (o.price - leg['entry_price']) * leg['lots'] * g.params['multiplier'] * side_to_dir(leg['side'])
    log.info(f"[RU] CLOSE {leg['side']} {c} {leg['lots']}@{o.price:.1f} pnl={pnl:.0f} reason={reason} "
             f"entry={leg['entry_price']:.1f} stop={leg['stop_line']:.1f} atr_lock={leg['atr_lock']:.1f}")
    g.legs.remove(leg)
    g.warned.pop('close_' + c, None)
    if not g.legs:
        g.adds = 0
        g.last_flat_date = context.current_dt.date()
        log.info("[RU] FLAT")
    return True


def close_all_legs(context, reason):
    """全部平仓（信号反转）；未成交的 leg 留待下一 bar 继续"""
    for leg in list(g.legs):
        close_leg(context, leg, reason)


def check_state_consistency(context):
    """g.legs 与账户实际持仓每 bar 比对；不一致→告警并按实际重建（设计文档 §4）"""
    sp = context.subportfolios[0]
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
    log.warn(f"[RU] STATE_MISMATCH tracked={tracked} actual={actual} -> rebuild from actual")
    g.legs = []
    for (c, side), amt in actual.items():
        d = compute_signal_data(c, context)
        atr = d['atr'] if d is not None else 0.0
        price = get_current_data()[c].last_price
        stop_line = price - g.params['stop_loss_dist_atr'] * atr if side == 'long' \
            else price + g.params['stop_loss_dist_atr'] * atr
        g.legs.append({'contract': c, 'side': side, 'lots': amt, 'entry_price': price,
                       'entry_date': context.current_dt.date(), 'atr_lock': atr,
                       'extreme': price, 'stop_line': stop_line})
    if not g.legs:
        g.adds = 0
        g.last_flat_date = context.current_dt.date()

# ==================== 分钟止损 ====================

def minute_risk_checks(context, bar):
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
            log.info(f"[RU] STOP_HIT {leg['contract']} {leg['side']} stop={leg['stop_line']:.1f} "
                     f"bar_low={bar['low']:.1f} bar_high={bar['high']:.1f}")
            close_leg(context, leg, 'stop')

# ==================== 14:45 信号流程 ====================

def entry_ok(d, p):
    """入场条件 2-5 的统一入口（方向非 0 + 回踩/站回/距离/深失守）"""
    if d is None or d['direction'] == 0:
        return False
    return check_entry(d['direction'], d['highs'], d['lows'], d['ma'], d['price'], d['atr'],
                       p['pullback_window'], p['entry_max_dist_atr'], p['pullback_max_break_atr'])


def cooldown_ok(context):
    """冷却期：全平后 cooldown_days 个交易日内禁止开新仓（0 = 关闭）"""
    p = g.params
    if g.last_flat_date is None or p['cooldown_days'] <= 0:
        return True
    days = get_trade_days(start_date=g.last_flat_date, end_date=context.current_dt.date())
    elapsed = len(days) - 1
    return not (1 <= elapsed <= p['cooldown_days'])


def try_add(context, d):
    """加仓：入场形态 + 价差确认 + 次数限制（设计文档 2.5）"""
    p = g.params
    if not g.legs or g.migrating is not None or g.adds >= p['max_adds']:
        return
    side = g.legs[0]['side']
    if d is None or d['direction'] == 0 or dir_to_side(d['direction']) != side:
        return
    if not entry_ok(d, p):
        return
    gap = d['price'] - g.legs[-1]['entry_price'] if side == 'long' \
        else g.legs[-1]['entry_price'] - d['price']
    if p['add_min_dist_atr'] > 0:
        if gap < p['add_min_dist_atr'] * d['atr']:
            return
    elif gap <= 0:
        return
    lots = calc_lots(context.portfolio.total_value, p['risk_pct'], p['stop_loss_dist_atr'],
                     d['atr'], p['multiplier'])
    open_leg(context, d['direction'], lots, d['atr'], 'add')


def try_open(context, d):
    """开仓：空仓 + 冷却期已过 + 入场信号（设计文档 2.3/2.6）"""
    p = g.params
    if g.legs or g.migrating is not None:
        return
    if not cooldown_ok(context):
        return
    if not entry_ok(d, p):
        return
    lots = calc_lots(context.portfolio.total_value, p['risk_pct'], p['stop_loss_dist_atr'],
                     d['atr'], p['multiplier'])
    open_leg(context, d['direction'], lots, d['atr'], 'entry')


def signal_routine(context):
    """14:45 信号流程：信号反转 → 移仓 → 加仓 → 开仓"""
    p = g.params
    if g.current_contract is None:                       # 首个 14:45 解析主力
        g.current_contract = get_dominant_future('RU')
        log.info(f"[RU] dominant init {g.current_contract}")
    d = compute_signal_data(g.current_contract, context)
    prev_contract = g.current_contract
    if d is not None:
        log.info(f"[RU] DAILY {g.current_contract} price={d['price']:.1f} ma={d['ma'][-1]:.1f} "
                 f"atr={d['atr']:.1f} dir={d['direction']} hi={d['highs'][-1]:.1f} "
                 f"lo={d['lows'][-1]:.1f} legs={len(g.legs)} adds={g.adds}")
    if g.legs and d is not None and g.migrating is None:  # 移仓中间态：仅推进移仓
        direction = side_to_dir(g.legs[0]['side'])
        if check_signal_exit(direction, d['price'], d['ma'][-1], d['atr'], p['signal_exit_dist_atr']):
            line = d['ma'][-1] - direction * p['signal_exit_dist_atr'] * d['atr']
            log.info(f"[RU] SIGNAL_EXIT {g.current_contract} price={d['price']:.1f} line={line:.1f}")
            close_all_legs(context, 'signal_exit')
    check_dominant(context)                              # 移仓（设计文档 2.8 执行顺序）
    if g.current_contract != prev_contract:
        d = compute_signal_data(g.current_contract, context)   # 换约后按新合约重取
    try_add(context, d)
    try_open(context, d)


def risk_and_signal_routine(context):
    """分钟单入口：一致性校验 → 止损 → 14:45 信号流程（设计文档 §4）"""
    try:
        p = g.params
        check_state_consistency(context)
        bar = current_minute_bar(g.current_contract, context)
        minute_risk_checks(context, bar)
        retry_migration(context)
        if context.current_dt.strftime('%H:%M') != p['signal_time']:
            return
        if g.last_signal_date == context.current_dt.date():
            return
        signal_routine(context)
        g.last_signal_date = context.current_dt.date()
    except Exception as e:
        log.error(f"[RU] minute routine error: {e}")

# ==================== 移仓 ====================

def check_dominant(context):
    """主力切换检查：连续 2 个交易日确认；有持仓启动移仓，空仓仅更新记录（设计文档 2.8）"""
    if g.migrating is not None:                          # 移仓进行中：由 retry_migration 推进
        return
    dom = get_dominant_future('RU')
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
        log.info(f"[RU] dominant switch {g.current_contract} -> {dom} (flat)")
        g.current_contract = dom
        return
    start_migration(context, dom)


def start_migration(context, new_contract):
    """启动移仓：先开新后平旧"""
    side = g.legs[0]['side']
    total = sum(leg['lots'] for leg in g.legs)
    g.migrating = {'old': g.current_contract, 'new': new_contract, 'side': side, 'total': total,
                   'new_price': None, 'old_price': None}
    log.info(f"[RU] MIGRATE start {g.current_contract}->{new_contract} side={side} lots={total}")
    retry_migration(context)


def retry_migration(context):
    """推进移仓：每 bar 重试未成交腿；两腿全成交后统一更新 legs 并做价差平移"""
    m = g.migrating
    if m is None:
        return
    if has_open_order(m['new']) or has_open_order(m['old']):
        return
    if m['new_price'] is None:
        o = order(m['new'], m['total'], side=m['side'])
        if o is None or o.filled < m['total']:
            warn_once('mig_new', f"[RU] WARN migrate open unfilled {m['new']} lots={m['total']}")
            return
        m['new_price'] = o.price
        g.warned.pop('mig_new', None)
        log.info(f"[RU] MIGRATE open {m['new']} {m['side']} {m['total']}@{o.price:.1f}")
    if m['old_price'] is None:
        amt = -m['total'] if m['side'] == 'long' else m['total']
        o = order(m['old'], amt, side=m['side'])
        if o is None or o.filled < m['total']:
            warn_once('mig_old', f"[RU] WARN migrate close unfilled {m['old']} lots={m['total']}")
            return
        m['old_price'] = o.price
        g.warned.pop('mig_old', None)
        log.info(f"[RU] MIGRATE close {m['old']} {m['side']} {m['total']}@{o.price:.1f}")
    delta = m['new_price'] - m['old_price']
    d = compute_signal_data(m['new'], context)
    if d is None:
        warn_once('mig_atr', f"[RU] WARN migrate new contract data missing {m['new']}")
    for leg in g.legs:
        leg['contract'] = m['new']
        leg['entry_price'] = m['new_price']
        if d is not None:
            leg['atr_lock'] = d['atr']
        leg['stop_line'] += delta
        leg['extreme'] += delta
    g.current_contract = m['new']
    g.migrating = None
    atr_str = f"{d['atr']:.1f}" if d is not None else "n/a"
    log.info(f"[RU] MIGRATE done {m['old']}->{m['new']} delta={delta:.1f} atr_lock={atr_str}")

# ==================== 初始化 ====================

def initialize(context):
    """初始化（设计文档 §4）"""
    try:
        init_cash = context.portfolio.starting_cash
        set_subportfolios([SubPortfolioConfig(cash=init_cash, type='futures')])
        set_option('use_real_price', True)
        set_option('avoid_future_data', True)
        set_option('futures_margin_rate', PARAMS['margin_rate'])
        set_order_cost(OrderCost(open_commission=0.000023, close_commission=0.000023,
                                 close_today_commission=0.000023, open_tax=0, close_tax=0,
                                 min_commission=0), type='futures', ref='RU')
        set_slippage(PriceRelatedSlippage(0.0003), type='futures')
        set_benchmark('RU9999.XSGE')
        log.set_level('order', 'error')

        g.params = dict(PARAMS)
        g.current_contract = None          # 首个 14:45 解析主力
        g.legs = []
        g.adds = 0
        g.last_signal_date = None
        g.last_flat_date = None
        g.dominant_counter = {'contract': None, 'days': 0}
        g.migrating = None
        g.warned = {}

        log.info('PARAMS: %s' % sorted(g.params.items()))
        run_daily(risk_and_signal_routine, time='every_bar')    # 回测频率需选"分钟"
    except Exception as e:
        log.error(f"[RU] init error: {e}")
