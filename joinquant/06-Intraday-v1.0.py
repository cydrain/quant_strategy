# ============================================================
# 期货日内策略 v1.0（聚宽回测 · 开盘区间突破 ORB）
#   规则：
#   - 开盘前 orb_minutes 分钟（默认 30）的高低点为开盘区间
#   - 盘中分钟级检查：收盘价突破区间上沿+缓冲做多 / 下沿+缓冲做空，市价 lots_per_trade 手
#   - 止损：区间对侧（默认）或 入场价 ∓ orb_stop_atr_mult×ATR；可选 ATR 吊灯移动止损
#   - 单笔持仓：止损后才允许再次突破入场（受冷却与每日次数上限约束）
#   - 收盘前 flat_before_close 分钟（默认 14:55）强平，不留隔夜
#   骨架特性：
#   - 品种参数化：symbol 为连续合约代码，交易时经 get_dominant_future 映射主力
#   - 周期参数化：bar_freq 分钟（默认 5），指标 bar 由分钟数据聚合、仅用已完成 bar；
#                 止损/触发为盘中分钟级检查
#   - 交易时段：日盘 09:00-11:30 / 13:30-15:00，夜盘 21:00-23:00（归属次一交易日）
#     trade_windows='night+day'（默认）| 'day_only'；夜盘任务逐分钟注册（平台 every_bar 不覆盖夜盘）
#   - orb_session='day'(09:00 开盘定区间) | 'night'(21:00 开盘定区间)；
#     night_close_flat=1 时夜盘收盘前也强平（夜盘视为独立日内段）
#   - 风控：每日最大开仓次数 max_trades_per_day、单日亏损熔断 max_daily_loss_pct、
#     止损后冷却 cooldown_minutes、开盘 session_open_blackout 分钟禁入场、
#     死水盘过滤（区间宽度 < orb_min_range_atr×ATR 当日不做）、可选日线均线方向过滤
#   - 费用：开/平昨/平今费率分列（平台按聚宽语义计平今；沙盒当前统一按 close_commission）
#   - 移仓：交易日起点（必然空仓）做主力切换检查，连续 2 个交易日确认
#   沙盒口径: 盘中周期为会话锚定分桶（日盘 09:00/13:30 起、夜盘 21:00 起，仅含已完成 bar，
#             索引=bar 起始时刻）；成交价=当前分钟收盘（卖出减一跳动）
# 回测设置: 期货账户 / 100 万 / 分钟频率
# 沙盒运行示例:
#   RU: python joinquant/backtest_sandbox/runner.py --strategy joinquant/06-Intraday-v1.0.py \
#       --data local_data/export_ru --param symbol=RU --param exchange=SHFE --out joinquant/logs/out_06_ru.log
#   P : python joinquant/backtest_sandbox/runner.py --strategy joinquant/06-Intraday-v1.0.py \
#       --data local_data/export_p --tick 2 --param symbol=P --param exchange=XDCE --param tick=2 \
#       --out joinquant/logs/out_06_p.log
#   周期/时段: --param bar_freq=15   --param orb_minutes=20   --param trade_windows=day_only
# ============================================================
from jqdata import *
import datetime
import pandas as pd

# ==================== 参数（集中，initialize 时拷入 g.params） ====================
PARAMS = {
    # ---- 品种 / 数据 ----
    'symbol': 'RU',               # 品种代码（连续合约；主力解析/合约前缀）
    'exchange': 'SHFE',           # 交易所后缀（仅基准合约用）
    'tick': 5.0,                  # 最小变动价位（RU=5 / P=2，缓冲用；与 runner --tick 保持一致）
    'multiplier': 10,             # 合约乘数
    'bar_freq': 5,                # 指标周期（分钟，默认 5）
    # ---- 会话 / 平仓 ----
    'trade_windows': 'night+day',  # 参与时段: 'night+day' | 'day_only'
    'night_close_flat': 0,        # 1 = 夜盘收盘前也强平（夜盘视为独立日内段）
    'flat_before_close': 5,       # 收盘前 N 分钟强平（日盘 14:55；夜盘 22:55 当 night_close_flat=1）
    'entry_cutoff': 30,           # 收盘前 N 分钟起禁开新仓（日盘 14:30 后；夜盘 22:30 后）
    'session_open_blackout': 5,   # 每段开盘后 N 分钟内禁开新仓（避开开盘噪音）
    'night_start': '21:00',       # 夜盘任务注册起始（空串 = 无夜盘品种）
    'night_end': '23:00',         # 夜盘任务注册结束
    'signal_time': '14:45',       # 每日 LEVELS 日志时刻
    # ---- 风控 ----
    'max_trades_per_day': 4,      # 每日最大开仓次数（区间突破建议 2-3）
    'max_daily_loss_pct': 1.0,    # 单日亏损达起始权益 N% → 全平并当日停手（0=关闭）
    'cooldown_minutes': 30,       # 止损后冷却分钟数
    'lots_per_trade': 1,          # 每笔基础手数
    'margin_cap_pct': 0.10,       # 保证金占用上限（当前权益比例；0=不限）
    'margin_rate': 0.12,          # 保证金率（回测设定值）
    # ---- ORB 参数 ----
    'orb_session': 'day',         # 以哪段开盘定区间: 'day'(09:00) | 'night'(21:00)
    'orb_minutes': 30,            # 开盘区间长度（分钟）
    'entry_buffer_ticks': 1,      # 突破缓冲（跳），滤掉刚好触线
    'atr_period': 14,             # ATR 周期（单位=bar_freq 根；止损/死水盘过滤用）
    'orb_stop_mode': 'range_opposite',  # 止损: 'range_opposite'(区间对侧) | 'atr'(倍数)
    'orb_stop_atr_mult': 1.0,     # orb_stop_mode='atr' 时止损距离（ATR 倍数）
    'orb_exit_mode': 'stop_only',  # 出场: 'stop_only'(仅止损+强平) | 'trail'(ATR 吊灯移动止损)
    'orb_trail_atr_mult': 1.5,    # 吊灯移动止损距离（ATR 倍数）
    'orb_min_range_atr': 1.0,     # 区间宽度 < N×ATR 视为死水盘，当日不做
    'orb_day_ma_filter': 0,       # 1 = 只做日线均线方向的突破（收盘价 vs MA）
    'orb_day_ma_period': 20,      # 日线均线周期
    # ---- 费用（费率=成交额比例；贴回聚宽后按当时交易所标准调整） ----
    'fee_open': 0.000023,
    'fee_close': 0.000023,        # 平昨
    'fee_close_today': 0.000023,  # 平今（日内策略几乎全部平今）
}

# ==================== 日志辅助 ====================


def log_pfx():
    c = getattr(g, 'current_contract', None)
    return '[' + (c.split('.')[0] if c else PARAMS['symbol']) + ']'


def trade_mark(side, reason):
    """开/加多=🔴、平多=🔶、开/加空=🟢、平空=🔷"""
    if reason in ('entry', 'entry_stall', 'add'):
        return '🔴' if side == 'long' else '🟢'
    return '🔶' if side == 'long' else '🔷'


def warn_once(key, msg):
    if g.warned.get(key) != msg:
        log.warn(msg)
        g.warned[key] = msg


def daily_log_separator(ctx):
    log.info(f"{'*' * 80}")

# ==================== 时间 / 会话 ====================


def eff_trade_day(ctx):
    """事件所属交易日：夜盘（>=21:00）归属次一交易日，日盘=日历日"""
    d = ctx.current_dt.date()
    if ctx.current_dt.hour * 60 + ctx.current_dt.minute >= 21 * 60:
        ds = get_trade_days(start_date=d)
        if ds and ds[0] == d and len(ds) > 1:
            return ds[1]
        if ds:
            return ds[0]
    return d


def session_of(now):
    """当前会话：'day' / 'night' / None（无事件分钟）"""
    hm = now.hour * 60 + now.minute
    if 21 * 60 <= hm <= 23 * 60:
        return 'night'
    if 9 * 60 < hm <= 11 * 60 + 30:
        return 'day'
    if 13 * 60 + 30 < hm <= 15 * 60:
        return 'day'
    return None


def session_start_ts(ctx, session):
    """会话（开盘）起始时刻：夜盘=当日历日 21:00；日盘=交易日 09:00"""
    d0 = ctx.current_dt.date() if session == 'night' else g.td
    h0 = 21 if session == 'night' else 9
    return pd.Timestamp(datetime.datetime.combine(d0, datetime.time(h0, 0)))


def hm_of(dt0):
    return dt0.hour * 60 + dt0.minute

# ==================== 指标（纯函数） ====================


def calc_atr(hist, period):
    """ATR：最近 period 根已完成 bar 的 TR 均值（口径=bar_freq 周期）"""
    if hist is None or len(hist) < period + 1:
        return None
    h = hist['high'].values
    l = hist['low'].values
    c = hist['close'].values
    trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
           for i in range(len(c) - period, len(c))]
    return float(sum(trs) / period)


def signal_atr(ctx, hist, period):
    """带缓存的 ATR（同一根已完成 bar 内不重复计算）"""
    if hist is None or len(hist) < period + 1:
        return None
    key = (g.current_contract, hist.index[-1], period)
    if g.atr_cache is not None and g.atr_cache[0] == key:
        return g.atr_cache[1]
    v = calc_atr(hist, period)
    g.atr_cache = (key, v)
    return v

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
    return {'high': row['high'], 'low': row['low'], 'close': row['close']}


def load_signal_bars(ctx):
    """信号周期 bar 序列（仅已完成 bar，索引=bar 起始时刻）"""
    p = g.params
    if g.current_contract is None:
        return None
    need = max(120, p['atr_period'] * 3, (p['orb_minutes'] // p['bar_freq'] + 2) * 3)
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
    """保证金占用上限（当前权益 × margin_cap_pct）：按剩余额度限手；0 = 不限"""
    p = g.params
    if p['margin_cap_pct'] <= 0:
        return lots
    margin_per_lot = price * p['multiplier'] * p['margin_rate']
    if margin_per_lot <= 0:
        return lots
    used = sum(l['lots'] for l in g.legs) * margin_per_lot
    room = ctx.portfolio.total_value * p['margin_cap_pct'] - used
    return min(lots, int(room / margin_per_lot))


def open_trade(ctx, side):
    """市价开 lots_per_trade 手（含保证金约束）；成功返回成交价，失败返回 None"""
    p = g.params
    c = g.current_contract
    if has_open_order(c):
        warn_once('open_' + c, f"{log_pfx()} ❌ WARN open pending exists {c}")
        return None
    price = get_current_data()[c].last_price
    lots = fit_lots_to_margin(p['lots_per_trade'], price, p['multiplier'], p['margin_rate'],
                              ctx.subportfolios[0].available_cash)
    lots = fit_lots_to_cap(ctx, lots, price)
    if lots < 1:
        warn_once('open_skip_' + c, f"{log_pfx()} ❌ WARN open skip margin insufficient/cap {c}")
        return None
    o = order(c, lots, side=side)
    if o is None or o.filled < lots:
        warn_once('open_' + c, f"{log_pfx()} ❌ WARN open unfilled (limit board?) {c} lots={lots}")
        return None
    g.legs.append({'contract': c, 'side': side, 'lots': lots, 'entry_price': o.price})
    g.trades_td += 1
    g.warned.pop('open_' + c, None)
    return o.price


def close_all(ctx, reason):
    """全部平仓（止损/强平/熔断）；未成交腿留待下一 bar 继续"""
    pnl_sum = 0.0
    closed = 0
    for leg in list(g.legs):
        c = leg['contract']
        if has_open_order(c):
            warn_once('close_' + c, f"{log_pfx()} ❌ WARN close pending exists {c}")
            continue
        o = order(c, -leg['lots'], side=leg['side'])
        if o is None or o.filled < leg['lots']:
            warn_once('close_' + c, f"{log_pfx()} ❌ WARN close unfilled (limit board?) {c} "
                                    f"{leg['side']} lots={leg['lots']} reason={reason}")
            continue
        d = 1 if leg['side'] == 'long' else -1
        pnl = (o.price - leg['entry_price']) * leg['lots'] * g.params['multiplier'] * d
        pnl_sum += pnl
        log.info(f"{log_pfx()} ✅{trade_mark(leg['side'], reason)} CLOSE {leg['side']} {c} {leg['lots']}@{o.price:.1f} "
                 f"pnl={pnl:.0f} reason={reason} entry={leg['entry_price']:.1f}")
        g.legs.remove(leg)
        closed += 1
        g.warned.pop('close_' + c, None)
    if closed:
        log.info(f"{log_pfx()} ✅ 清仓完成({reason}) {closed}笔 pnl={pnl_sum:.0f}")
    return closed

# ==================== 交易日状态 / 移仓 ====================


def check_dominant(ctx):
    """主力切换检查（交易日起点空仓时调用）：连续 2 个交易日确认后切换；持仓时暂缓"""
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
    if g.legs:                       # 防御：正常应已在上一交易日收盘前强平
        log.warn(f"{log_pfx()} WARN td rollover with open legs, force flat")
        close_all(ctx, 'rollover')
    g.trades_td = 0
    g.td_disabled = False
    g.entry_block_until = None
    g.cooldown_until = None
    g.orb_range = None
    g.orb_stop = None
    g.orb_peak = None
    g.orb_dir = 0
    g.atr_cache = None
    g.equity0 = ctx.portfolio.total_value
    check_dominant(ctx)
    log.info(f"{log_pfx()} TD_START td={td} equity={g.equity0:.0f} "
             f"window={p['trade_windows']} orb_session={p['orb_session']}")

# ==================== 通用闸门 / 风控 ====================


def entries_allowed(ctx, session):
    """开仓闸门；返回 (是否允许, 原因)"""
    p = g.params
    now = ctx.current_dt
    if g.td_disabled:
        return False, 'td_disabled'
    if g.entry_block_until is not None and now < g.entry_block_until:
        return False, 'after_flat'
    if g.cooldown_until is not None and now < g.cooldown_until:
        return False, 'cooldown'
    if g.trades_td >= p['max_trades_per_day']:
        return False, 'max_trades'
    hm = hm_of(now)
    if session == 'day':
        if hm < 9 * 60 + p['session_open_blackout']:
            return False, 'open_blackout'
        if hm > 15 * 60 - p['entry_cutoff']:
            return False, 'entry_cutoff'
    elif session == 'night':
        if hm < 21 * 60 + p['session_open_blackout']:
            return False, 'open_blackout'
        if hm > 23 * 60 - p['entry_cutoff']:
            return False, 'entry_cutoff'
    return True, ''


def daily_loss_guard(ctx):
    """单日亏损熔断：跌破起始权益 ×(1-pct) → 全平并当日停手"""
    p = g.params
    if g.td_disabled or p['max_daily_loss_pct'] <= 0:
        return
    floor = g.equity0 * (1 - p['max_daily_loss_pct'] / 100.0)
    if ctx.portfolio.total_value <= floor:
        log.warn(f"{log_pfx()} ❌ DAILY_LOSS_STOP equity={ctx.portfolio.total_value:.0f} floor={floor:.0f}")
        close_all(ctx, 'daily_loss')
        g.td_disabled = True


def maybe_force_flat(ctx, session):
    """收盘前强平：日盘 15:00-flat_before_close；夜盘 23:00-flat_before_close（night_close_flat=1）"""
    p = g.params
    hm = hm_of(ctx.current_dt)
    flat_hm = None
    if session == 'day':
        flat_hm = 15 * 60 - p['flat_before_close']
    elif session == 'night' and p['night_close_flat'] and p['trade_windows'] == 'night+day':
        flat_hm = 23 * 60 - p['flat_before_close']
    if flat_hm is None or hm != flat_hm:
        return
    if g.legs:
        close_all(ctx, 'flat_eod')
    # 无论有无仓位都封锁至本日历日结束（防 entry_cutoff=0 时收盘前进场；
    # 夜盘 22:55 强平时次日 09:00 起自然解锁，日盘 14:55 强平后跨 td 由 rollover 重置）
    g.entry_block_until = pd.Timestamp(ctx.current_dt.date()) + pd.Timedelta(days=1)

# ==================== 账户一致性 ====================


def check_state_consistency(ctx):
    """g.legs 与账户实际持仓每 bar 比对；不一致→告警并按实际重建"""
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
    log.warn(f"{log_pfx()} STATE_MISMATCH tracked={tracked} actual={actual} -> rebuild from actual")
    g.legs = []
    for (c, side), amt in actual.items():
        price = get_current_data()[c].last_price
        g.legs.append({'contract': c, 'side': side, 'lots': amt, 'entry_price': price})
    if g.legs:
        p = g.params
        price = get_current_data()[g.legs[0]['contract']].last_price
        d = 1 if g.legs[0]['side'] == 'long' else -1
        g.orb_stop = price - d * p['orb_stop_atr_mult'] * (g.ev_atr or 0)
        g.orb_peak = price

# ==================== ORB 开盘区间突破 ====================


def orb_manage(ctx):
    """ORB 止损（含可选吊灯移动止损），盘中分钟级"""
    p = g.params
    bar = g.ev_bar
    if not g.legs or bar is None or g.orb_stop is None:
        return
    leg = g.legs[0]
    d = 1 if leg['side'] == 'long' else -1
    stop = g.orb_stop
    if p['orb_exit_mode'] == 'trail' and g.ev_atr:
        if d == 1:
            g.orb_peak = max(g.orb_peak if g.orb_peak is not None else leg['entry_price'], bar['high'])
            stop = max(stop, g.orb_peak - p['orb_trail_atr_mult'] * g.ev_atr)
        else:
            g.orb_peak = min(g.orb_peak if g.orb_peak is not None else leg['entry_price'], bar['low'])
            stop = min(stop, g.orb_peak + p['orb_trail_atr_mult'] * g.ev_atr)
    hit = (bar['low'] <= stop) if d == 1 else (bar['high'] >= stop)
    if hit:
        log.info(f"{log_pfx()} STOP_HIT {leg['side']} stop={stop:.1f} "
                 f"bar_low={bar['low']:.1f} bar_high={bar['high']:.1f}")
        close_all(ctx, 'stop')
        g.orb_stop = None
        g.cooldown_until = pd.Timestamp(ctx.current_dt) + datetime.timedelta(minutes=p['cooldown_minutes'])


def orb_entries(ctx):
    """ORB 区间计算（每交易日一次）与突破入场"""
    p = g.params
    if g.ev_session != p['orb_session']:
        return
    bar, hist = g.ev_bar, g.ev_hist
    if bar is None or hist is None:
        return
    if g.orb_range is None:
        s0 = session_start_ts(ctx, g.ev_session)
        end = s0 + pd.Timedelta(minutes=p['orb_minutes'])
        if pd.Timestamp(ctx.current_dt) < end:
            return
        rng = hist[(hist.index >= s0) & (hist.index < end)]
        if len(rng) == 0:
            return
        hi = float(rng['high'].max())
        lo = float(rng['low'].min())
        atr = g.ev_atr
        dead = atr is not None and (hi - lo) < p['orb_min_range_atr'] * atr
        g.orb_range = {'hi': hi, 'lo': lo, 'dead': dead}
        g.orb_dir = 0
        if p['orb_day_ma_filter']:
            d = attribute_history(g.current_contract, p['orb_day_ma_period'], '1d', ['close'])
            if len(d) >= p['orb_day_ma_period']:
                ma = float(d['close'].mean())
                last = float(d['close'].iloc[-1])
                g.orb_dir = 1 if last >= ma else -1
        log.info(f"{log_pfx()} ORB_RANGE {g.current_contract} hi={hi:.1f} lo={lo:.1f} "
                 f"width={hi - lo:.1f} dir={g.orb_dir}{' DEAD' if dead else ''}")
        if dead:
            return
    if g.orb_range['dead']:
        return
    if g.legs:   # 单笔持仓：止损/离场后才允许再入场
        return
    if p['orb_stop_mode'] == 'atr' and not g.ev_atr:
        return
    hi, lo = g.orb_range['hi'], g.orb_range['lo']
    buf = p['entry_buffer_ticks'] * p['tick']
    long_sig = bar['high'] >= hi + buf
    short_sig = bar['low'] <= lo - buf
    if not long_sig and not short_sig:
        return
    ok, why = entries_allowed(ctx, g.ev_session)
    if not ok:
        log.debug(f"{log_pfx()} ORB ENTRY_BLOCKED ({why}) hi={hi:.1f} lo={lo:.1f}")
        return
    if long_sig and g.orb_dir in (0, 1):
        fill = open_trade(ctx, 'long')
        if fill is None:
            return
        g.orb_stop = lo if p['orb_stop_mode'] == 'range_opposite' else fill - p['orb_stop_atr_mult'] * g.ev_atr
        g.orb_peak = fill
        log.info(f"{log_pfx()} ✅{trade_mark('long', 'entry')} ORB_ENTRY long {g.current_contract} "
                 f"{g.legs[-1]['lots']}@{fill:.1f} stop={g.orb_stop:.1f} range=[{lo:.1f},{hi:.1f}]")
    elif short_sig and g.orb_dir in (0, -1):
        fill = open_trade(ctx, 'short')
        if fill is None:
            return
        g.orb_stop = hi if p['orb_stop_mode'] == 'range_opposite' else fill + p['orb_stop_atr_mult'] * g.ev_atr
        g.orb_peak = fill
        log.info(f"{log_pfx()} ✅{trade_mark('short', 'entry')} ORB_ENTRY short {g.current_contract} "
                 f"{g.legs[-1]['lots']}@{fill:.1f} stop={g.orb_stop:.1f} range=[{lo:.1f},{hi:.1f}]")

# ==================== LEVELS 日志 ====================


def levels_log(ctx, now, session):
    p = g.params
    if session != 'day' or now.strftime('%H:%M') != p['signal_time']:
        return
    if g.last_levels_date == ctx.current_dt.date():
        return
    g.last_levels_date = ctx.current_dt.date()
    r = g.orb_range
    rtxt = f"range=[{r['lo']:.1f},{r['hi']:.1f}]{' DEAD' if r['dead'] else ''}" if r else 'range=n/a'
    catr = f"ATR({p['atr_period']},{p['bar_freq']}m)={g.ev_atr:.1f}" if g.ev_atr else 'ATR=n/a'
    log.info(f"{log_pfx()} LEVELS ORB {rtxt} dir={g.orb_dir} stop="
             f"{(g.orb_stop if g.orb_stop is not None else 0):.1f} {catr} "
             f"trades={g.trades_td}/{p['max_trades_per_day']}")

# ==================== 分钟单入口 ====================


def intraday_routine(ctx):
    """分钟单入口：状态推进 → 风控 → 止损 → 强平 → 入场 → LEVELS"""
    try:
        p = g.params
        now = ctx.current_dt
        rollover(ctx, eff_trade_day(ctx))
        session = session_of(now)
        if session is None:
            return
        check_state_consistency(ctx)
        g.ev_session = session
        g.ev_bar = current_minute_bar(ctx, g.current_contract)
        g.ev_hist = load_signal_bars(ctx)
        g.ev_atr = signal_atr(ctx, g.ev_hist, p['atr_period'])
        daily_loss_guard(ctx)
        orb_manage(ctx)
        maybe_force_flat(ctx, session)
        orb_entries(ctx)
        levels_log(ctx, now, session)
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
        if g.params['trade_windows'] == 'day_only' and g.params['orb_session'] == 'night':
            log.warn("trade_windows=day_only 与 orb_session=night 冲突，orb_session 回退 day")
            g.params['orb_session'] = 'day'

        g.current_contract = None
        g.td = None
        g.equity0 = 0.0
        g.trades_td = 0
        g.td_disabled = False
        g.entry_block_until = None
        g.cooldown_until = None
        g.orb_range = None
        g.orb_stop = None
        g.orb_peak = None
        g.orb_dir = 0
        g.atr_cache = None
        g.last_levels_date = None
        g.dominant_counter = {'contract': None, 'days': 0}
        g.legs = []
        g.warned = {}
        g.ev_bar = None
        g.ev_hist = None
        g.ev_atr = None
        g.ev_session = None

        param_lines = '\n'.join(f"  {k:<23}: {v}" for k, v in sorted(g.params.items()))
        log.info('PARAMS:\n%s' % param_lines)
        use_night = g.params['trade_windows'] == 'night+day' and g.params['night_start'] and g.params['night_end']
        run_daily(daily_log_separator, time=g.params['night_start'] if use_night else '09:01')
        run_daily(intraday_routine, time='every_bar')
        if use_night:
            h0, m0 = [int(x) for x in g.params['night_start'].split(':')]
            h1, m1 = [int(x) for x in g.params['night_end'].split(':')]
            t0 = datetime.datetime(2000, 1, 1, h0, m0)
            end_t = datetime.datetime(2000, 1, 1, h1, m1)
            night_task_count = 0
            while t0 <= end_t:
                run_daily(intraday_routine, time=t0.strftime('%H:%M'))
                night_task_count += 1
                t0 += datetime.timedelta(minutes=1)
            log.info(f"夜盘分钟任务注册完成: {night_task_count}个 "
                     f"({g.params['night_start']}~{g.params['night_end']}, 复用 intraday_routine)")
        else:
            log.info(f"夜盘分钟任务未注册（trade_windows={g.params['trade_windows']}）")
    except Exception as e:
        log.error(f"{log_pfx()} init error: {e}")
