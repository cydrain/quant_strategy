# ============================================================
# 棕榈油 P 波动收缩突破策略 v1.0（NR7 / 布林收口 · 聚宽回测）
#   思路: 波动先收缩、再突破。收缩判定与突破触发替换海龟的 20 日通道入场，
#         其余（N、止损、加仓、仓位、保证金上限、移仓）全部沿用 turtle v3.2。
#   规则（squeeze_mode 选择收缩判定）：
#   - nr7: 最近一根完成日线振幅（high-low）为近 nr7_n 根最小；
#     事件高低点 = 该 bar 的 high/low
#   - boll: 最近一根完成日线的布林带宽（(上轨-下轨)/中轨）为近 bb_squeeze_n 根最小；
#     事件高低点 = 该 bar 的上轨/下轨（MA bb_n ± bb_k×标准差）
#   - boll 为持续状态：带宽创近 bb_squeeze_n 根最小即激活，此后每个交易日都用当日
#     当前上下轨作为触发价位（不冻结、不按次日过期），直到开仓成功才解除；
#     新的收口日会重新激活
#   - box: 近 box_n 根日线箱高（最高-最低）≤ box_k×N 视为收缩；箱体随日滚动，
#     条件成立的每一天都可用当日箱体上/下沿触发（k 按箱高/N 分布校准，默认 3.5）
#   - 触发（nr7 为收缩 bar 之后一个交易日内有效）：盘中分钟级突破事件高点 → 开多 1 unit；
#     跌破事件低点 → 开空（受 fixed_dir 限制）。每个有效事件只触发一次
#   - 出场: 盘中触及"前 exit_n 根日线反向极值"（多头跌破最低价）→ 全部平仓；
#     初始止损 = 入场价 ∓ stop_dist_n×N
#   - 加仓: 顺向每 add_step_n×N 加 1 unit，最多 max_units 个；加仓后整仓止损移至最新 unit 入场价 ∓ 2N
#   - 仓位: 1 unit = 账户权益 × unit_risk ÷ (N × 合约乘数)，向下取整（最低 1 手）
#   - 保证金占用上限: 整仓保证金 ≤ 当前权益 × margin_cap_pct（默认 10%，按剩余额度开满；0 = 不限）
#   - fixed_dir: 1=只做多 / -1=只做空 / 0=双向（跟随突破方向）
#   - 不迁入 STALL 并行入场、s1_skip 跳过规则（均绑定 20 日通道口径）
#   工程: 主力移仓 / 夜盘分钟检查 / 停板等待 沿用既有框架
# 回测设置: 期货账户 / 100 万 / 分钟频率
# ============================================================
from jqdata import *
import datetime
import pandas as pd

# ==================== 参数（集中，initialize 时拷入 g.params） ====================
PARAMS = {
    'symbol': 'P',                # 品种代码（主力解析/合约前缀）
    'exchange': 'XDCE',           # 交易所后缀（仅基准合约用）
    'fixed_dir': 1,               # 固定方向（分段已知行情）：1=只做多 / -1=只做空 / 0=双向
    'squeeze_mode': 'nr7',        # 收缩判定模式：nr7 / boll / box
    'nr7_n': 7,                   # NR7 收缩窗口（最近 n 根日线振幅最小）
    'bb_n': 20,                   # 布林均线/标准差周期
    'bb_k': 2.0,                  # 布林标准差倍数
    'bb_squeeze_n': 20,           # 布林收口窗口（带宽为近 n 根最小）
    'box_n': 20,                  # 箱体周期（近 n 根日线最高-最低）
    'box_k': 3.5,                 # 箱体收缩阈值（箱高 ≤ k×N）
    'n_period': 20,               # N（ATR）周期（日线）
    'n_smooth': 20,               # N 的平滑系数（经典 N=(19*PDN+TR)/20）
    'exit_n': 10,                 # 反向突破出场通道周期（系统1=10）
    'unit_risk': 0.01,            # 1 unit 风险预算（账户权益比例）
    'margin_cap_pct': 0.10,       # 保证金占用上限（当前权益比例；0=不限，每档按剩余额度开满）
    'max_units': 4,               # 最大 unit 数（含首仓）
    'add_step_n': 0.5,            # 顺向每 N×该值 加 1 unit
    'stop_dist_n': 2.0,           # 初始/加仓后止损距离（N 倍数）
    'signal_time': '14:45',       # 14:45 做主力切换检查与 DAILY 日志（交易本身全天分钟级）
    'night_start': '21:00',       # 夜盘任务注册起始时刻（空串 = 无夜盘品种）
    'night_end': '23:00',         # 夜盘任务注册结束时刻
    'multiplier': 10,             # 合约乘数
    'margin_rate': 0.12,          # 保证金率（回测设定值）
}

# ==================== 日志辅助 ====================


def log_pfx():
    c = getattr(g, 'current_contract', None)
    return '[' + (c.split('.')[0] if c else PARAMS['symbol']) + ']'


def trade_mark(side, reason):
    """开/加多=🔴、平多=🔶、开/加空=🟢、平空=🔷"""
    if reason in ('entry', 'add'):
        return '🔴' if side == 'long' else '🟢'
    return '🔶' if side == 'long' else '🔷'


def daily_log_separator(ctx):
    log.info(f"{'*' * 80}")

# ==================== 纯函数 ====================


def calc_n(highs, lows, closes, n_period, n_smooth):
    """经典 N：TR 序列先取前 n_period 个均值初始化，再按 N=((s-1)N+TR)/s 递推到最新"""
    if len(closes) < n_period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    n_val = sum(trs[:n_period]) / n_period
    for i in range(n_period, len(trs)):
        n_val = ((n_smooth - 1) * n_val + trs[i]) / n_smooth
    return n_val


def dir_to_side(direction):
    return 'long' if direction == 1 else 'short'


def side_to_dir(side):
    return 1 if side == 'long' else -1


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


def unit_lots(ctx, n_val):
    """经典 unit 手数：权益 × unit_risk ÷ (N × 乘数)，向下取整（最低 1 手）"""
    p = g.params
    denom = n_val * p['multiplier']
    if denom <= 0:
        return 1
    return max(1, int(ctx.portfolio.total_value * p['unit_risk'] / denom))

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


def load_bars(ctx, contract):
    """日线序列（前若干根已完成日线，不含当日）"""
    p = g.params
    need = max(60, p['n_period'] * 3, p['exit_n'], p['nr7_n'] + 1,
               p['bb_n'] + p['bb_squeeze_n'] + 1, p['box_n'] + p['n_period'] + 1)
    hist = attribute_history(contract, need + 1, '1d', ['high', 'low', 'close'])
    if hist is None or len(hist) == 0:
        return None
    return hist


def bar_levels(ctx):
    """每根新日线计算并缓存：N、反向出场通道、收缩事件（口径=截至最近一根已完成日线）"""
    p = g.params
    hist = load_bars(ctx, g.current_contract)
    if hist is None or len(hist) == 0:
        return None
    key = (g.current_contract, hist.index[-1])
    if getattr(g, 'lv_key', None) == key and g.lv is not None:
        return g.lv
    highs = list(hist['high'].values)
    lows = list(hist['low'].values)
    closes = list(hist['close'].values)
    n_val = calc_n(highs, lows, closes, p['n_period'], p['n_smooth'])
    if n_val is None or n_val <= 0:
        return None
    is_sq = False
    sq_high, sq_low = highs[-1], lows[-1]
    if p['squeeze_mode'] == 'nr7':
        win = p['nr7_n']
        ranges = [highs[i] - lows[i] for i in range(len(highs) - win, len(highs))]
        is_sq = len(ranges) == win and ranges[-1] <= min(ranges)
    elif p['squeeze_mode'] == 'boll':
        bn, k, sn = p['bb_n'], p['bb_k'], p['bb_squeeze_n']
        if len(closes) >= bn + sn - 1:
            widths = []
            for end in range(len(closes) - sn, len(closes)):
                w = closes[end - bn + 1:end + 1]
                m = sum(w) / bn
                sd = (sum((x - m) ** 2 for x in w) / bn) ** 0.5
                widths.append(2 * k * sd / m if m > 0 else 0)
            m = sum(closes[-bn:]) / bn
            sd = (sum((x - m) ** 2 for x in closes[-bn:]) / bn) ** 0.5
            is_sq = widths[-1] <= min(widths)
            sq_high, sq_low = m + k * sd, m - k * sd
    elif p['squeeze_mode'] == 'box':
        bn = p['box_n']
        if len(highs) >= bn:
            sq_high, sq_low = max(highs[-bn:]), min(lows[-bn:])
            is_sq = sq_high - sq_low <= p['box_k'] * n_val
    lv = {'n': n_val,
          'exit_long': min(lows[-p['exit_n']:]), 'exit_short': max(highs[-p['exit_n']:]),
          'is_sq': is_sq, 'sq_high': sq_high, 'sq_low': sq_low}
    g.lv_key = key
    g.lv = lv
    return lv

# ==================== 收缩事件（日线口径，仅次日有效） ====================


def squeeze_event(ctx, lv):
    """按新完成日线刷新事件状态，返回当日有效的触发高低点，否则 None。
    nr7：仅收缩 bar 之后一个交易日有效；
    boll：收口激活后持续有效，每日轨道取当日当前值，开仓成功才解除"""
    if lv is None:
        return None
    if g.sq_key != g.lv_key:
        g.sq_key = g.lv_key
        g.sq_fired = False
        if g.params['squeeze_mode'] == 'boll':
            if lv['is_sq']:
                g.boll_armed = True
                log.info(f"{log_pfx()} BOLL_ARM high={lv['sq_high']:.1f} low={lv['sq_low']:.1f}")
            g.sq = {'high': lv['sq_high'], 'low': lv['sq_low']} if g.boll_armed else None
        else:
            g.sq = {'high': lv['sq_high'], 'low': lv['sq_low']} if lv['is_sq'] else None
            if g.sq is not None:
                tag = g.params['squeeze_mode'].upper()
                log.info(f"{log_pfx()} {tag}_SET high={g.sq['high']:.1f} low={g.sq['low']:.1f}")
    return g.sq

# ==================== 订单封装 ====================


def has_open_order(contract):
    for o in get_open_orders().values():
        if o.security == contract:
            return True
    return False


def warn_once(key, msg):
    if g.warned.get(key) != msg:
        log.warn(msg)
        g.warned[key] = msg


def open_unit(ctx, direction, n_val, reason):
    """市价开 1 个 unit；建立/刷新整仓止损 = 最新 unit 入场价 ∓ stop_dist_n×N"""
    p = g.params
    c = g.current_contract
    if has_open_order(c):
        warn_once('open_' + c, f"{log_pfx()} ❌ WARN open pending exists {c}")
        return False
    side = dir_to_side(direction)
    price = get_current_data()[c].last_price
    lots = fit_lots_to_margin(unit_lots(ctx, n_val), price, p['multiplier'], p['margin_rate'],
                              ctx.subportfolios[0].available_cash)
    lots = fit_lots_to_cap(ctx, lots, price)
    if lots < 1:
        warn_once('open_skip_' + c, f"{log_pfx()} ❌ WARN open skip margin insufficient/cap {c}")
        return False
    o = order(c, lots, side=side)
    if o is None or o.filled < lots:
        warn_once('open_' + c, f"{log_pfx()} ❌ WARN open unfilled (limit board?) {c} lots={lots}")
        return False
    stop_line = o.price - direction * p['stop_dist_n'] * n_val
    g.legs.append({'contract': c, 'side': side, 'lots': lots, 'entry_price': o.price,
                   'n_at_entry': n_val, 'stop_line': stop_line})
    g.stop_line = stop_line
    log.info(f"{log_pfx()} ✅{trade_mark(side, reason)} {reason.upper()} {side} {c} {lots}@{o.price:.1f} "
             f"N={n_val:.1f} stop={stop_line:.1f} units={len(g.legs)} equity={ctx.portfolio.total_value:.0f}")
    g.warned.pop('open_' + c, None)
    return True


def close_all(ctx, reason):
    """全部平仓（止损/反向突破出场）；未成交的 leg 留待下一 bar 继续"""
    if not g.legs:
        return
    trade_pnl = 0.0
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
        pnl = (o.price - leg['entry_price']) * leg['lots'] * g.params['multiplier'] * side_to_dir(leg['side'])
        trade_pnl += pnl
        log.info(f"{log_pfx()} ✅{trade_mark(leg['side'], reason)} CLOSE {leg['side']} {c} {leg['lots']}@{o.price:.1f} "
                 f"pnl={pnl:.0f} reason={reason} entry={leg['entry_price']:.1f} stop={leg['stop_line']:.1f}")
        g.legs.remove(leg)
        g.warned.pop('close_' + c, None)
    if not g.legs:
        g.stop_line = None
        log.info(f"{log_pfx()} ✅ 清仓完成，当前空仓 trade_pnl={trade_pnl:.0f}")


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
    if g.migrating is not None:
        return
    log.warn(f"{log_pfx()} STATE_MISMATCH tracked={tracked} actual={actual} -> rebuild from actual")
    g.legs = []
    for (c, side), amt in actual.items():
        d = bar_levels(ctx)
        n_val = d['n'] if d is not None else 0.0
        price = get_current_data()[c].last_price
        stop_line = price - side_to_dir(side) * g.params['stop_dist_n'] * n_val
        g.legs.append({'contract': c, 'side': side, 'lots': amt, 'entry_price': price,
                       'n_at_entry': n_val, 'stop_line': stop_line})
        g.stop_line = stop_line

# ==================== 分钟检查（止损/出场/加仓/收缩突破入场） ====================


def squeeze_minute_checks(ctx, bar, lv, ev):
    """收缩突破核心：共享 2N 止损 → 反向突破出场 → 加仓 → 收缩 bar 极值突破入场"""
    if g.migrating is not None:
        return
    p = g.params
    if bar is None:
        return
    if g.legs:
        side = g.legs[0]['side']
        direction = side_to_dir(side)
        # 1) 共享止损（2N，含加仓后的上移）
        if g.stop_line is not None:
            hit = bar['low'] <= g.stop_line if direction == 1 else bar['high'] >= g.stop_line
            if hit:
                log.info(f"{log_pfx()} STOP_HIT {side} stop={g.stop_line:.1f} "
                         f"bar_low={bar['low']:.1f} bar_high={bar['high']:.1f}")
                close_all(ctx, 'stop')
                return
        # 2) 反向突破出场
        exit_line = lv['exit_long'] if direction == 1 else lv['exit_short']
        hit_exit = bar['low'] <= exit_line if direction == 1 else bar['high'] >= exit_line
        if hit_exit:
            log.info(f"{log_pfx()} EXIT_BREAKOUT {side} line={exit_line:.1f} "
                     f"bar_low={bar['low']:.1f} bar_high={bar['high']:.1f}")
            close_all(ctx, 'exit_breakout')
            return
        # 3) 加仓：顺向每 add_step_n×N 加 1 unit
        if len(g.legs) < p['max_units']:
            last = g.legs[-1]
            step = p['add_step_n'] * lv['n']
            reach = bar['high'] >= last['entry_price'] + step if direction == 1 \
                else bar['low'] <= last['entry_price'] - step
            if reach:
                open_unit(ctx, direction, lv['n'], 'add')
        return
    # 4) 入场：收缩 bar 极值突破（fixed_dir 限定方向；每事件仅一次）
    if ev is None or g.sq_fired:
        return
    tag = p['squeeze_mode'].upper()
    if p['fixed_dir'] in (0, 1) and bar['high'] >= ev['high']:
        if open_unit(ctx, 1, lv['n'], 'entry'):
            g.sq_fired = True
            g.boll_armed = False
            g.sq = None
            log.info(f"{log_pfx()} {tag}_BREAK long level={ev['high']:.1f}")
        return
    if p['fixed_dir'] in (0, -1) and bar['low'] <= ev['low']:
        if open_unit(ctx, -1, lv['n'], 'entry'):
            g.sq_fired = True
            g.boll_armed = False
            g.sq = None
            log.info(f"{log_pfx()} {tag}_BREAK short level={ev['low']:.1f}")

# ==================== 移仓 ====================


def check_dominant(ctx):
    """主力切换检查：连续 2 个交易日确认；有持仓启动移仓，空仓仅更新记录"""
    if g.migrating is not None:
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
    total = sum([leg['lots'] for leg in g.legs])
    g.migrating = {'old': g.current_contract, 'new': new_contract, 'side': side, 'total': total,
                   'new_price': None, 'old_price': None}
    log.info(f"{log_pfx()} MIGRATE start {g.current_contract}->{new_contract} side={side} lots={total}")
    retry_migration(ctx)


def retry_migration(ctx):
    """推进移仓：每 bar 重试未成交腿；两腿全成交后统一更新 legs 并重设止损"""
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
        o = order(m['old'], -m['total'], side=m['side'])
        if o is None or o.filled < m['total']:
            warn_once('mig_old', f"{log_pfx()} WARN migrate close unfilled {m['old']} lots={m['total']}")
            return
        m['old_price'] = o.price
        g.warned.pop('mig_old', None)
        pnl = sum([(o.price - leg['entry_price']) * leg['lots'] * g.params['multiplier']
                   * side_to_dir(m['side']) for leg in g.legs])
        log.info(f"{log_pfx()} MIGRATE close {m['old']} {m['side']} {m['total']}@{o.price:.1f} pnl={pnl:.0f}")
    delta = m['new_price'] - m['old_price']
    d = bar_levels(ctx)
    n_val = d['n'] if d is not None else (g.legs[0]['n_at_entry'] if g.legs else 0.0)
    direction = side_to_dir(m['side'])
    for leg in g.legs:
        leg['contract'] = m['new']
        leg['entry_price'] = m['new_price']
        leg['n_at_entry'] = n_val
        leg['stop_line'] = m['new_price'] - direction * g.params['stop_dist_n'] * n_val
    g.stop_line = m['new_price'] - direction * g.params['stop_dist_n'] * n_val
    g.current_contract = m['new']
    g.migrating = None
    log.info(f"{log_pfx()} MIGRATE done {m['old']}->{m['new']} delta={delta:.1f} stop={g.stop_line:.1f}")

# ==================== 分钟单入口 ====================


def risk_and_signal_routine(ctx):
    """分钟单入口：一致性校验 → 收缩事件刷新 → 收缩突破检查（止损/出场/加仓/入场）→ 14:45 移仓检查与 DAILY"""
    try:
        p = g.params
        if g.current_contract is None:
            g.current_contract = get_dominant_future(p['symbol'])
            log.info(f"{log_pfx()} dominant init {g.current_contract}")
        check_state_consistency(ctx)
        lv = bar_levels(ctx)
        bar = current_minute_bar(ctx, g.current_contract)
        ev = squeeze_event(ctx, lv)
        if lv is not None:
            squeeze_minute_checks(ctx, bar, lv, ev)
        retry_migration(ctx)
        if ctx.current_dt.strftime('%H:%M') != p['signal_time']:
            return
        if g.last_signal_date == ctx.current_dt.date():
            return
        check_dominant(ctx)
        if lv is not None:
            sq_txt = f"SQH={ev['high']:.1f} SQL={ev['low']:.1f}" if ev is not None else 'SQH=- SQL=-'
            log.info(f"{log_pfx()} DAILY {g.current_contract} N={lv['n']:.1f} {sq_txt} "
                     f"units={len(g.legs)} stop={(g.stop_line if g.stop_line is not None else 0):.1f}")
        g.last_signal_date = ctx.current_dt.date()
    except Exception as e:
        log.error(f"{log_pfx()} minute routine error: {e}")

# ==================== 初始化 ====================


def initialize(ctx):
    try:
        init_cash = ctx.portfolio.starting_cash
        set_subportfolios([SubPortfolioConfig(cash=init_cash, type='futures')])
        set_option('use_real_price', True)
        set_option('avoid_future_data', True)
        set_option('futures_margin_rate', PARAMS['margin_rate'])
        set_order_cost(OrderCost(open_commission=0.000023, close_commission=0.000023,
                                 close_today_commission=0.000023, open_tax=0, close_tax=0,
                                 min_commission=0), type='futures', ref=PARAMS['symbol'])
        set_slippage(PriceRelatedSlippage(0.0003), type='futures')
        set_benchmark(f"{PARAMS['symbol']}9999.{PARAMS['exchange']}")
        log.set_level('order', 'error')
        log.set_level('system', 'error')
        log.set_level('strategy', 'info')

        g.params = dict(PARAMS)
        g.current_contract = None
        g.legs = []
        g.stop_line = None
        g.lv = None
        g.lv_key = None
        g.sq = None
        g.sq_key = None
        g.sq_fired = False
        g.boll_armed = False
        g.last_signal_date = None
        g.dominant_counter = {'contract': None, 'days': 0}
        g.migrating = None
        g.warned = {}

        param_lines = '\n'.join(f"  {k:<23}: {v}" for k, v in sorted(g.params.items()))
        log.info('PARAMS:\n%s' % param_lines)
        run_daily(daily_log_separator, time=PARAMS['night_start'] or '09:00')
        run_daily(risk_and_signal_routine, time='every_bar')
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
