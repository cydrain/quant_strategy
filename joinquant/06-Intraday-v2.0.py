# ============================================================
# 期货日内策略 v2.0（聚宽回测 · 15 分钟区间 + 5 分钟 FVG 回踩）
#   来源：Jesse Rogers | Casper Trading
#         "This Boring Strategy Made Me Profitable (Beginner Friendly)"
#   规则：
#   - 09:00-09:15 的高低点为当日区间 H/L，09:15 后固定
#   - 09:15 后扫描已完成的 5m 三 K 结构：
#       多头 FVG: high[1] < low[3]，且三根中至少一根收盘 > H、至少一根收盘 <= H
#       空头 FVG: low[1] > high[3]，且三根中至少一根收盘 < L、至少一根收盘 >= L
#       不要求 FVG 缺口本身在区间外/贴边；三根全部收在突破方向外侧 -> 无效
#   - 入场：1m 触及 FVG 边缘（多=high[1]，空=low[1]）后按当前价市价成交
#   - 止损：第一根 FVG K 线低点 -1 tick / 高点 +1 tick
#   - 目标：固定 2R（按实际成交价与止损距离）
#   - 日盘 09:00-11:30 / 13:30-15:00；11:30 起停止开新仓；14:55 强平
#   - 每天最多 1 笔、固定 1 手、单笔持仓、不加仓、不移动止损、不分批
#   - 1% 日亏熔断、10% 保证金上限；主力/换月/手续费/滑点/状态一致性沿用 v1.0
#   沙盒口径：
#   - 5m bar 由 1m 聚合，索引=bar 起始时刻，仅含已完成 bar
#   - 成交价=当前分钟收盘；卖出再减一个 tick
#   - 同一 1m bar 同时触及止损与目标时，保守按止损先成交
#   - 沙盒无挂单，所有 order 即时成交；入场为 1m 触及后市价
# 回测设置：期货账户 / 100 万 / 分钟频率
# 沙盒运行：
#   RU: python joinquant/backtest_sandbox/runner.py --strategy joinquant/06-Intraday-v2.0.py \
#       --data local_data/export_ru --param symbol=RU --param exchange=SHFE \
#       --out joinquant/logs/out_06v2_ru.log
#   P : python joinquant/backtest_sandbox/runner.py --strategy joinquant/06-Intraday-v2.0.py \
#       --data local_data/export_p --tick 2 --param symbol=P --param exchange=XDCE --param tick=2 \
#       --out joinquant/logs/out_06v2_p.log
#   参数：--param entry_cutoff=11:30 --param flat_before_close=5 --param bar_freq=5
# ============================================================
from jqdata import *
import datetime
import pandas as pd

# ==================== 参数（集中，initialize 时拷入 g.params） ====================
PARAMS = {
    # ---- 品种 / 数据 ----
    'symbol': 'RU',               # 品种代码（连续合约；主力解析/合约前缀）
    'exchange': 'SHFE',           # 交易所后缀（仅基准合约用）
    'tick': 5.0,                  # 最小变动价位（RU=5 / P=2；与 runner --tick 保持一致）
    'multiplier': 10,             # 合约乘数
    'bar_freq': 5,                # FVG 周期（分钟；默认 5）
    # ---- 区间 / 时段 ----
    'range_start': '09:00',       # 开盘区间起点
    'range_minutes': 15,          # 开盘区间长度（分钟）
    'entry_cutoff': '11:30',      # 该时刻起停止开新仓
    'flat_before_close': 5,       # 收盘前 N 分钟强平（日盘 14:55）
    'signal_time': '11:20',       # 每日 LEVELS 日志时刻
    # ---- 风控 / 仓位 ----
    'max_trades_per_day': 1,      # 每日最大开仓次数
    'max_daily_loss_pct': 1.0,    # 单日亏损达起始权益 N% -> 全平并当日停手（0=关闭）
    'lots_per_trade': 1,          # 每笔固定手数
    'margin_cap_pct': 0.10,       # 保证金占用上限（当前权益比例；0=不限）
    'margin_rate': 0.12,          # 保证金率（回测设定值）
    # ---- 费用（费率=成交额比例；贴回聚宽后按当时交易所标准调整） ----
    'fee_open': 0.000023,
    'fee_close': 0.000023,        # 平昨
    'fee_close_today': 0.000023,  # 平今（日内策略几乎全部平今）
}

# ==================== 日志 / 时间辅助 ====================


def log_pfx():
    c = getattr(g, 'current_contract', None)
    return '[' + (c.split('.')[0] if c else PARAMS['symbol']) + ']'


def warn_once(key, msg):
    if g.warned.get(key) != msg:
        log.warn(msg)
        g.warned[key] = msg


def hm_of(dt0):
    return dt0.hour * 60 + dt0.minute


def hm_of_str(s):
    h, m = s.split(':')
    return int(h) * 60 + int(m)


def session_of(now):
    """当前会话：日盘 09:00-11:30 / 13:30-15:00；其余返回 None"""
    hm = hm_of(now)
    if 9 * 60 < hm <= 11 * 60 + 30:
        return 'day'
    if 13 * 60 + 30 < hm <= 15 * 60:
        return 'day'
    return None

# ==================== 平台取数 ====================


def current_minute_bar(ctx, contract):
    """当前分钟 bar（合约不在交易时段、数据过期时返回 None）"""
    if contract is None:
        return None
    bars = get_bars(contract, 1, '1m', ['date', 'high', 'low', 'close'], include_now=True)
    if bars is None or len(bars) == 0:
        return None
    row = bars[-1]
    if abs((pd.Timestamp(ctx.current_dt) - pd.Timestamp(row['date'])).total_seconds()) > 60:
        return None
    return {'high': float(row['high']), 'low': float(row['low']), 'close': float(row['close'])}


def load_signal_bars(ctx):
    """FVG 周期 bar 序列（仅已完成 bar，索引=bar 起始时刻）"""
    p = g.params
    if g.current_contract is None:
        return None
    need = max(30, p['range_minutes'] // p['bar_freq'] + 10)
    hist = attribute_history(g.current_contract, need, f"{p['bar_freq']}m",
                             ['open', 'high', 'low', 'close'])
    if hist is None or len(hist) == 0:
        return None
    return hist

# ==================== 订单封装 ====================


def has_open_order(contract):
    for o in get_open_orders().values():
        if o.security == contract:
            return True
    return False


def fit_lots_to_margin(lots, price, multiplier, margin_rate, available_cash):
    """物理保证金约束：按可用资金逐手减仓；1 手都开不出返回 0"""
    margin_per_lot = price * multiplier * margin_rate
    if margin_per_lot <= 0:
        return lots
    return min(lots, int(available_cash / margin_per_lot))


def fit_lots_to_cap(ctx, lots, price):
    """保证金占用上限（当前权益 x margin_cap_pct）：按剩余额度限手；0=不限"""
    p = g.params
    if p['margin_cap_pct'] <= 0:
        return lots
    margin_per_lot = price * p['multiplier'] * p['margin_rate']
    if margin_per_lot <= 0:
        return lots
    used = sum(l['lots'] for l in g.legs) * margin_per_lot
    room = ctx.portfolio.total_value * p['margin_cap_pct'] - used
    return min(lots, int(room / margin_per_lot))


def open_trade(ctx, side, stop, fvg=None):
    """市价开 lots_per_trade 手，按实际成交价计算 2R；成功返回成交价，失败返回 None"""
    p = g.params
    c = g.current_contract
    if c is None or g.legs:
        return None
    if has_open_order(c):
        warn_once('open_' + c, f"{log_pfx()} WARN open pending exists {c}")
        return None
    price = get_current_data()[c].last_price
    if price is None or price <= 0:
        warn_once('open_price_' + c, f"{log_pfx()} WARN open skip no price {c}")
        return None
    lots = fit_lots_to_margin(p['lots_per_trade'], price, p['multiplier'], p['margin_rate'],
                              ctx.subportfolios[0].available_cash)
    lots = fit_lots_to_cap(ctx, lots, price)
    if lots < 1:
        warn_once('open_skip_' + c, f"{log_pfx()} WARN open skip margin insufficient/cap {c}")
        return None
    o = order(c, lots, side=side)
    if o is None or o.filled < lots:
        warn_once('open_' + c, f"{log_pfx()} WARN open unfilled (limit board?) {c} lots={lots}")
        return None
    d = 1 if side == 'long' else -1
    risk = (o.price - stop) * d
    if risk <= 0:
        # 成交已越过结构止损：立即反向平掉，计入当日交易次数
        log.warn(f"{log_pfx()} WARN invalid fill {side} fill={o.price:.1f} stop={stop:.1f} -> close")
        order(c, -lots, side=side)
        g.trades_td += 1
        g.fvgs = []
        return None
    target = o.price + d * 2 * risk
    leg = {'contract': c, 'side': side, 'lots': lots, 'entry_price': o.price,
           'stop': stop, 'target': target, 'entry_dt': ctx.current_dt,
           'exit_reason': None, 'fvg_key': fvg['key'] if fvg else None,
           'fvg_entry': fvg['entry'] if fvg else None}
    g.legs.append(leg)
    g.trades_td += 1
    g.fvgs = []
    g.warned.pop('open_' + c, None)
    log.info(f"{log_pfx()} [ENTRY] {side} {c} {lots}@{o.price:.1f} stop={stop:.1f} "
             f"target={target:.1f} R={risk:.1f}")
    return o.price


def close_all(ctx, reason):
    """全部平仓（止损/目标/强平/熔断）；未成交腿留待下一 bar 继续"""
    pnl_sum = 0.0
    closed = 0
    for leg in list(g.legs):
        c = leg['contract']
        if has_open_order(c):
            warn_once('close_' + c, f"{log_pfx()} WARN close pending exists {c}")
            continue
        o = order(c, -leg['lots'], side=leg['side'])
        if o is None or o.filled < leg['lots']:
            warn_once('close_' + c, f"{log_pfx()} WARN close unfilled (limit board?) {c} "
                                    f"{leg['side']} lots={leg['lots']} reason={reason}")
            continue
        d = 1 if leg['side'] == 'long' else -1
        pnl = (o.price - leg['entry_price']) * leg['lots'] * g.params['multiplier'] * d
        pnl_sum += pnl
        log.info(f"{log_pfx()} [CLOSE] {leg['side']} {c} {leg['lots']}@{o.price:.1f} "
                 f"pnl={pnl:.0f} reason={reason} entry={leg['entry_price']:.1f}")
        g.legs.remove(leg)
        closed += 1
        g.warned.pop('close_' + c, None)
    if closed:
        log.info(f"{log_pfx()} [CLOSE_ALL] {closed}笔 pnl={pnl_sum:.0f} reason={reason}")
        g.fvgs = []
    return closed

# ==================== 交易日状态 / 移仓 ====================


def check_dominant(ctx):
    """主力切换检查（交易日起点调用）：连续 2 个交易日确认后切换；持仓时暂缓"""
    dom = get_dominant_future(g.params['symbol'], date=g.td)
    if dom is None:
        return
    if g.current_contract is None:
        g.current_contract = dom
        log.info(f"{log_pfx()} dominant init {dom}")
        return
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
    if g.legs:
        log.warn(f"{log_pfx()} WARN dominant switch {g.current_contract} -> {dom} deferred (holding)")
        return
    log.info(f"{log_pfx()} dominant switch {g.current_contract} -> {dom} (flat)")
    g.current_contract = dom


def rollover(ctx, td):
    """交易日切换：清状态、记起始权益、做主力检查"""
    p = g.params
    if g.td == td:
        return
    g.td = td
    if g.legs:                       # 防御：正常应已在上一交易日 14:55 强平
        log.warn(f"{log_pfx()} WARN td rollover with open legs, force flat")
        close_all(ctx, 'rollover')
    g.trades_td = 0
    g.td_disabled = False
    g.daily_loss_hit = False
    g.day_closed = False
    g.range = None
    g.fvgs = []
    g.last_fvg_bar = None
    g.last_levels_date = None
    g.equity0 = ctx.portfolio.total_value
    check_dominant(ctx)
    log.info('*' * 80)
    log.info(f"{log_pfx()} TD_START td={td} equity={g.equity0:.0f} "
             f"range={p['range_start']}+{p['range_minutes']}m cutoff={p['entry_cutoff']}")

# ==================== 开盘区间 ====================


def compute_range(ctx):
    """09:00-09:15 区间：H/L 只算一次，09:15 后固定"""
    p = g.params
    if g.range is not None:
        return
    start_hm = hm_of_str(p['range_start'])
    if hm_of(ctx.current_dt) < start_hm + p['range_minutes']:
        return
    hist = g.ev_hist
    if hist is None or len(hist) == 0:
        return
    s0 = pd.Timestamp(datetime.datetime.combine(
        g.td, datetime.time(start_hm // 60, start_hm % 60)))
    end = s0 + pd.Timedelta(minutes=p['range_minutes'])
    rng = hist[(hist.index >= s0) & (hist.index < end)]
    need = max(1, p['range_minutes'] // p['bar_freq'])
    if len(rng) < need:
        warn_once('range_data', f"{log_pfx()} WARN range {g.td} bars={len(rng)}/{need}, wait")
        return
    hi = float(rng['high'].max())
    lo = float(rng['low'].min())
    g.range = {'hi': hi, 'lo': lo, 'start': s0, 'end': end}
    log.info(f"{log_pfx()} [RANGE] {g.td} {s0.strftime('%H:%M')}-{end.strftime('%H:%M')} "
             f"hi={hi:.1f} lo={lo:.1f} width={hi - lo:.1f} bars={len(rng)}")

# ==================== FVG 识别 ====================


def build_fvg(hist):
    """用最新三根已完成 bar 识别一个有效 FVG；无效返回 None"""
    p = g.params
    if hist is None or len(hist) < 3 or g.range is None:
        return None
    a, b, c = hist.iloc[-3], hist.iloc[-2], hist.iloc[-1]
    i1 = pd.Timestamp(hist.index[-3])
    i2 = pd.Timestamp(hist.index[-2])
    i3 = pd.Timestamp(hist.index[-1])
    hi, lo = g.range['hi'], g.range['lo']
    closes = [float(a['close']), float(b['close']), float(c['close'])]

    if float(a['high']) < float(c['low']):
        # 多头 FVG：三根收盘必须跨过区间上沿 H
        has_out = any(x > hi for x in closes)
        has_in = any(x <= hi for x in closes)
        if not (has_out and has_in):
            log.debug(f"{log_pfx()} FVG_REJECT long bars={i1}..{i3} H={hi:.1f} "
                      f"closes={[round(x, 1) for x in closes]}")
            return None
        return {'key': ('long', i1, i2, i3), 'side': 'long',
                'entry': float(a['high']), 'stop': float(a['low']) - p['tick'],
                'gap': float(c['low']) - float(a['high']),
                'i1': i1, 'i2': i2, 'i3': i3, 'closes': closes,
                'range_hi': hi, 'range_lo': lo}

    if float(a['low']) > float(c['high']):
        # 空头 FVG：三根收盘必须跨过区间下沿 L
        has_out = any(x < lo for x in closes)
        has_in = any(x >= lo for x in closes)
        if not (has_out and has_in):
            log.debug(f"{log_pfx()} FVG_REJECT short bars={i1}..{i3} L={lo:.1f} "
                      f"closes={[round(x, 1) for x in closes]}")
            return None
        return {'key': ('short', i1, i2, i3), 'side': 'short',
                'entry': float(a['low']), 'stop': float(a['high']) + p['tick'],
                'gap': float(a['low']) - float(c['high']),
                'i1': i1, 'i2': i2, 'i3': i3, 'closes': closes,
                'range_hi': hi, 'range_lo': lo}
    return None


def detect_fvgs(ctx):
    """每出现一根新的已完成 5m bar，检查一次最新三 K 结构"""
    p = g.params
    if g.range is None or g.legs or g.td_disabled or g.day_closed:
        return
    if g.trades_td >= p['max_trades_per_day']:
        return
    hist = g.ev_hist
    if hist is None or len(hist) < 3:
        return
    if hm_of(ctx.current_dt) >= hm_of_str(p['entry_cutoff']):
        if g.fvgs:
            log.info(f"{log_pfx()} [FVG_EXPIRE] {len(g.fvgs)} pending at cutoff")
            g.fvgs = []
        return
    i3 = pd.Timestamp(hist.index[-1])
    if g.last_fvg_bar is not None and i3 <= g.last_fvg_bar:
        return
    g.last_fvg_bar = i3
    fvg = build_fvg(hist)
    if fvg is None:
        return
    if any(f['key'] == fvg['key'] for f in g.fvgs):
        return
    g.fvgs.append(fvg)
    log.info(f"{log_pfx()} [FVG] {fvg['side']} "
             f"bars={fvg['i1'].strftime('%H:%M')}~{fvg['i3'].strftime('%H:%M')} "
             f"entry={fvg['entry']:.1f} stop={fvg['stop']:.1f} gap={fvg['gap']:.1f} "
             f"range=[{fvg['range_lo']:.1f},{fvg['range_hi']:.1f}] "
             f"closes={[round(x, 1) for x in fvg['closes']]}")

# ==================== 入场 ====================


def maybe_enter(ctx):
    """1m 是否触及任一有效 FVG 边缘；触及后按当前价市价入场"""
    p = g.params
    if g.range is None or g.legs or g.td_disabled or g.day_closed:
        return
    if g.trades_td >= p['max_trades_per_day'] or not g.fvgs:
        return
    if hm_of(ctx.current_dt) >= hm_of_str(p['entry_cutoff']):
        return
    bar = g.ev_bar
    if bar is None:
        return
    c = g.current_contract
    price = get_current_data()[c].last_price if c else None
    if price is None or price <= 0:
        return

    for fvg in list(g.fvgs):
        side = fvg['side']
        d = 1 if side == 'long' else -1
        hit = (bar['low'] <= fvg['entry']) if side == 'long' else (bar['high'] >= fvg['entry'])
        if not hit:
            continue
        if (price - fvg['stop']) * d <= 0:
            g.fvgs.remove(fvg)
            log.info(f"{log_pfx()} [FVG_SKIP] {side} price={price:.1f} through "
                     f"stop={fvg['stop']:.1f}")
            continue
        log.info(f"{log_pfx()} [TRIGGER] {side} level={fvg['entry']:.1f} "
                 f"bar_low={bar['low']:.1f} bar_high={bar['high']:.1f}")
        fill = open_trade(ctx, side, fvg['stop'], fvg)
        if fill is None:
            return
        leg = g.legs[-1]
        log.info(f"{log_pfx()} [FILL] {side} planned={fvg['entry']:.1f} fill={fill:.1f} "
                 f"stop={leg['stop']:.1f} target={leg['target']:.1f} "
                 f"R={abs(fill - leg['stop']):.1f}")

# ==================== 持仓管理 / 风控 ====================


def manage_position(ctx):
    """1m 检查止损与 2R 目标；同一 bar 同时触发按止损先成交"""
    if not g.legs:
        return
    bar = g.ev_bar
    if bar is None:
        return
    leg = g.legs[0]
    if leg.get('exit_reason'):
        close_all(ctx, leg['exit_reason'])
        return
    stop = leg.get('stop')
    target = leg.get('target')
    if stop is None or target is None:
        log.warn(f"{log_pfx()} WARN position missing stop/target -> force flat")
        g.td_disabled = True
        g.fvgs = []
        close_all(ctx, 'missing_stop')
        return
    side = leg['side']
    stop_hit = (bar['low'] <= stop) if side == 'long' else (bar['high'] >= stop)
    if stop_hit:
        leg['exit_reason'] = 'stop'
        log.info(f"{log_pfx()} [STOP_HIT] {side} stop={stop:.1f} "
                 f"bar_low={bar['low']:.1f} bar_high={bar['high']:.1f}")
        close_all(ctx, 'stop')
        return
    target_hit = (bar['high'] >= target) if side == 'long' else (bar['low'] <= target)
    if target_hit:
        leg['exit_reason'] = 'target'
        log.info(f"{log_pfx()} [TARGET_HIT] {side} target={target:.1f} "
                 f"bar_low={bar['low']:.1f} bar_high={bar['high']:.1f}")
        close_all(ctx, 'target')


def daily_loss_guard(ctx):
    """单日亏损熔断：跌破起始权益 x(1-pct) -> 全平并当日停手"""
    p = g.params
    if p['max_daily_loss_pct'] <= 0 or g.equity0 <= 0:
        return
    if g.daily_loss_hit:
        if g.legs:
            close_all(ctx, 'daily_loss')
        return
    floor = g.equity0 * (1 - p['max_daily_loss_pct'] / 100.0)
    if ctx.portfolio.total_value <= floor:
        log.warn(f"{log_pfx()} [DAILY_LOSS_STOP] equity={ctx.portfolio.total_value:.0f} "
                 f"floor={floor:.0f}")
        g.daily_loss_hit = True
        g.td_disabled = True
        g.fvgs = []
        if g.legs:
            close_all(ctx, 'daily_loss')


def maybe_force_flat(ctx):
    """14:55 起强平；未成交则后续 bar 继续重试"""
    p = g.params
    flat_hm = 15 * 60 - p['flat_before_close']
    if hm_of(ctx.current_dt) < flat_hm:
        return
    if not g.day_closed:
        g.day_closed = True
        g.fvgs = []
        log.info(f"{log_pfx()} [FLAT_TIME] {flat_hm // 60:02d}:{flat_hm % 60:02d}")
    if g.legs:
        close_all(ctx, 'flat_eod')

# ==================== 账户一致性 ====================


def check_state_consistency(ctx):
    """g.legs 与账户实际持仓每 bar 比对；不一致 -> 全平并当日停手"""
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
    log.warn(f"{log_pfx()} STATE_MISMATCH tracked={tracked} actual={actual} "
             f"-> flatten and disable today")
    g.td_disabled = True
    g.fvgs = []
    g.legs = []
    for (c, side), amt in actual.items():
        price = get_current_data()[c].last_price
        g.legs.append({'contract': c, 'side': side, 'lots': amt,
                       'entry_price': float(price) if price else 0.0,
                       'stop': None, 'target': None, 'entry_dt': ctx.current_dt,
                       'exit_reason': None, 'fvg_key': None, 'fvg_entry': None})
    if g.legs:
        close_all(ctx, 'state_mismatch')

# ==================== LEVELS 日志 ====================


def levels_log(ctx, now):
    p = g.params
    if now.strftime('%H:%M') != p['signal_time']:
        return
    if g.last_levels_date == ctx.current_dt.date():
        return
    g.last_levels_date = ctx.current_dt.date()
    r = g.range
    rtxt = f"range=[{r['lo']:.1f},{r['hi']:.1f}]" if r else 'range=n/a'
    ftxt = ', '.join(f"{f['side']}@{f['entry']:.1f}" for f in g.fvgs) or 'none'
    if g.legs:
        leg = g.legs[0]
        ptxt = (f"{leg['side']}@{leg['entry_price']:.1f} "
                f"stop={leg['stop']:.1f} target={leg['target']:.1f}")
    else:
        ptxt = 'flat'
    log.info(f"{log_pfx()} [LEVELS] {rtxt} fvgs=[{ftxt}] pos={ptxt} "
             f"trades={g.trades_td}/{p['max_trades_per_day']}")

# ==================== 分钟单入口 ====================


def intraday_routine(ctx):
    """分钟单入口：交易日推进 -> 风控 -> 持仓 -> 强平 -> FVG -> 入场 -> LEVELS"""
    try:
        now = ctx.current_dt
        if session_of(now) is None:
            return
        rollover(ctx, now.date())
        if g.current_contract is None:
            check_dominant(ctx)
        check_state_consistency(ctx)
        g.ev_bar = current_minute_bar(ctx, g.current_contract)
        g.ev_hist = load_signal_bars(ctx)
        compute_range(ctx)
        daily_loss_guard(ctx)
        manage_position(ctx)
        maybe_force_flat(ctx)
        detect_fvgs(ctx)
        maybe_enter(ctx)
        levels_log(ctx, now)
    except Exception as e:
        log.error(f"{log_pfx()} routine error: {e}")

# ==================== 初始化 ====================


def initialize(ctx):
    try:
        init_cash = ctx.portfolio.starting_cash
        set_subportfolios([SubPortfolioConfig(cash=init_cash, type='futures')])
        set_option('use_real_price', True)
        set_option('avoid_future_data', True)
        set_option('futures_margin_rate', PARAMS['margin_rate'])
        set_order_cost(OrderCost(open_commission=PARAMS['fee_open'],
                                 close_commission=PARAMS['fee_close'],
                                 close_today_commission=PARAMS['fee_close_today'],
                                 open_tax=0, close_tax=0, min_commission=0),
                       type='futures', ref=PARAMS['symbol'])
        set_slippage(PriceRelatedSlippage(0.0003), type='futures')
        set_benchmark(f"{PARAMS['symbol']}9999.{PARAMS['exchange']}")
        log.set_level('order', 'error')
        log.set_level('system', 'error')
        log.set_level('strategy', 'info')

        g.params = dict(PARAMS)
        g.current_contract = None
        g.td = None
        g.equity0 = 0.0
        g.trades_td = 0
        g.td_disabled = False
        g.daily_loss_hit = False
        g.day_closed = False
        g.range = None
        g.fvgs = []
        g.last_fvg_bar = None
        g.last_levels_date = None
        g.dominant_counter = {'contract': None, 'days': 0}
        g.legs = []
        g.warned = {}
        g.ev_bar = None
        g.ev_hist = None

        param_lines = '\n'.join(f"  {k:<23}: {v}" for k, v in sorted(g.params.items()))
        log.info('PARAMS:\n%s' % param_lines)
        flat_hm = 15 * 60 - g.params['flat_before_close']
        log.info(f"日盘 15m 区间 + {g.params['bar_freq']}m FVG；"
                 f"{g.params['entry_cutoff']} 停止开新仓；"
                 f"{flat_hm // 60:02d}:{flat_hm % 60:02d} 强平；无夜盘任务")
        run_daily(intraday_routine, time='every_bar')
    except Exception as e:
        log.error(f"{log_pfx()} init error: {e}")
