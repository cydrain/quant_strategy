# ============================================================
# 棕榈油 P 经典海龟策略 v3.2（聚宽回测 · 单周期版 + 极值停滞 / NR 收缩并行入场）
#   指标周期（N、四通道）由 bar_freq 指定：15m/30m/60m 由分钟数据聚合仅用已完成 bar，
#   1d 走日线口径、不含当日。触发仍为盘中分钟级。
#   规则（Faith《海龟交易法则》系统 1 口径）：
#   - N = TR 的平滑均值，经典递归 N=(19*PDN+TR)/20（参数 n_period / n_smooth）
#   - 入场: 盘中触及"前 entry_n 根 bar 最高价"（多头；空头为最低价）→ 市价入场 1 unit
#   - 并行入场（stall_period>0）：连续 stall_period 根 bar 极值停滞（多头为
#     未创新低；空头镜像为未创新高）→ 入场，与突破入场并列、先达成者先入场。
#     判定（bar_freq 口径，与指标周期一致）：某根 bar 极值刷新此前 stall_period 根 bar 极值 = 一次事件；
#     自最近事件起连续 stall_period 根 bar 未创新低（即最新完成 bar 恰为事件后第 stall_period 根）
#     → 触发。按新完成 bar 边沿评判，每段仅触发一次；持仓中达成 = 错过，不补触发。
#   - 并行入场（nr_n>0）：最近已完成 nr_n 根 bar 中振幅（high-low）最小的一根 = NRn；
#     其后一个 bar 期间盘中突破该 bar 高点 → 开多 / 跌破低点 → 开空，与突破、停滞入场并列，
#     先达成者先入场，每事件仅触发一次。NR 入场同样受 s1_skip 约束：
#     上次交易盈利后跳过一次 NR 突破（该事件作废，等新 NR 事件再入场）。
#   - 出场: 盘中触及"前 exit_n 根 bar 反向极值"（多头跌破最低价）→ 全部平仓；初始止损 = 入场价 ∓ stop_dist_n×N
#   - 加仓: 顺向每 add_step_n×N 加 1 unit，最多 max_units 个；加仓后整仓止损移至最新 unit 入场价 ∓ 2N
#   - 仓位: 1 unit = 账户权益 × unit_risk ÷ (N × 合约乘数)，向下取整（最低 1 手）
#   - 保证金占用上限: 整仓保证金 ≤ 当前权益 × margin_cap_pct（默认 10%，按剩余额度开满；0 = 不限）
#   - 系统1 跳过规则: 上次交易若盈利，突破与 NR 各跳过一次（不判方向；STALL 不参与）
#     ——20 日突破记录被跳过价位并等通道刷新出新极值；NR 突破作废当前事件、等新 NR 事件再入场
#     （s1_skip 开关，默认开启）
#   - fixed_dir: 1=只做多 / -1=只做空 / 0=经典双向（跟随突破方向）
#   工程: 主力移仓（开新腿前保证金预检、不足回退先平旧；移仓中触止损/出场中止移仓并平仓）/
#         夜盘分钟检查 / 停板等待（未平完的平仓每 bar 重试直至清仓）沿用既有框架
#   沙盒口径: 盘中周期为会话锚定分桶（日盘 09:00/13:30 起、夜盘 21:00 起；
#             60m 在 11:00-11:30、14:30-15:00 会形成半根 bar，按实际长度计入）
# 回测设置: 期货账户 / 100 万 / 分钟频率
# ============================================================
from jqdata import *
import datetime
import pandas as pd

# ==================== 参数（集中，initialize 时拷入 g.params） ====================
PARAMS = {
    'symbol': 'P',                # 品种代码（主力解析/合约前缀）
    'exchange': 'XDCE',           # 交易所后缀（仅基准合约用）
    'fixed_dir': 1,               # 固定方向（分段已知行情）：1=只做多 / -1=只做空 / 0=经典双向
    'bar_freq': '1d',             # 指标周期：'15m' / '30m' / '60m' / '1d'（N、四通道均按该周期 bar 计算）
    'n_period': 20,               # N（ATR）周期（单位=bar_freq 根）
    'n_smooth': 20,               # N 的平滑系数（经典 N=(19*PDN+TR)/20）
    'entry_n': 20,                # 突破入场通道周期（系统1=20 / 系统2=55，单位=bar_freq 根）
    'exit_n': 10,                 # 反向突破出场通道周期（系统1=10 / 系统2=20，单位=bar_freq 根）
    'stall_period': 10,           # 并行入场：连续 N 根指标周期 bar 极值停滞（未创新低/新高）→入场；0=关闭
    'nr_n': 6,                    # 并行入场：最近 nr_n 根 bar 振幅最小者（NRn）的极值突破 → 入场；0=关闭
    'unit_risk': 0.01,            # 1 unit 风险预算（账户权益比例）
    'margin_cap_pct': 0.10,       # 保证金占用上限（当前权益比例；0=不限，每档按剩余额度开满）
    'max_units': 4,               # 最大 unit 数（含首仓）
    'add_step_n': 0.5,            # 顺向每 N×该值 加 1 unit
    'stop_dist_n': 2.0,           # 初始/加仓后止损距离（N 倍数）
    's1_skip': 1,                 # 系统1 跳过规则开关（默认开启；上次突破盈利则跳过一次同类突破，等新极值再入场）
    's1_scope': 'breakout',       # skip 参与范围：'breakout'=仅 20 日突破武装与消费（默认，2026-11-02 起）/'breakout_nr'=突破与 NR 各自独立/'current'=任意盈利入场都武装突破+NR（旧行为）；STALL 任何 scope 都不参与
    'signal_time': '14:45',       # 14:45 做主力切换检查与 LEVELS 日志（交易本身全天分钟级）
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
    if reason in ('entry', 'entry_stall', 'entry_nr', 'add'):
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
    """指标周期 bar 序列（前若干根已完成 bar；盘中周期由分钟数据聚合，不含未完成 bar）"""
    p = g.params
    need = max(60, p['n_period'] * 3, p['entry_n'], p['exit_n'], p['nr_n'] + 1)
    hist = attribute_history(contract, need + 1, p['bar_freq'], ['high', 'low', 'close'])
    if hist is None or len(hist) == 0:
        return None
    return hist


def bar_levels(ctx):
    """每根新 bar 计算并缓存：N、入场/出场通道（口径=截至最近一根已完成 bar）"""
    p = g.params
    hist = load_bars(ctx, g.current_contract)
    if hist is None or len(hist) == 0:
        return None
    key = (p['bar_freq'], g.current_contract, hist.index[-1])
    if getattr(g, 'lv_key', None) == key and g.lv is not None:
        return g.lv
    highs = list(hist['high'].values)
    lows = list(hist['low'].values)
    closes = list(hist['close'].values)
    n_val = calc_n(highs, lows, closes, p['n_period'], p['n_smooth'])
    if n_val is None or n_val <= 0:
        return None
    win = p['nr_n']
    is_nr = False
    if win > 0 and len(highs) >= win:
        ranges = [highs[i] - lows[i] for i in range(len(highs) - win, len(highs))]
        is_nr = ranges[-1] <= min(ranges)
    lv = {'n': n_val,
          'entry_long': max(highs[-p['entry_n']:]), 'entry_short': min(lows[-p['entry_n']:]),
          'exit_long': min(lows[-p['exit_n']:]), 'exit_short': max(highs[-p['exit_n']:]),
          'is_nr': is_nr, 'nr_high': highs[-1], 'nr_low': lows[-1]}
    g.lv_key = key
    g.lv = lv
    return lv

# ==================== 极值停滞并行入场（bar_freq 口径，与指标周期一致） ====================


def stall_state(ctx):
    """最新完成 bar 距最近一次"创新低/创新高"事件的 bar 数（事件 := 极值刷新此前 stall_period 根 bar 极值）。
    窗口内未见事件返回 None；结果按新 bar 缓存"""
    p = g.params
    if p['stall_period'] <= 0:
        return None
    n = p['stall_period']
    hist = attribute_history(g.current_contract, n * 2 + 5, p['bar_freq'], ['high', 'low'])
    if hist is None or len(hist) == 0:
        return None
    key = (p['bar_freq'], g.current_contract, hist.index[-1])
    if getattr(g, 'stall_key', None) == key and g.stall_days is not None:
        return g.stall_days
    g.stall_key = key
    g.stall_days = None
    if len(hist) < n + 2:
        return None
    lows = list(hist['low'].values)
    highs = list(hist['high'].values)
    last = len(lows) - 1
    long_cnt = short_cnt = None
    for i in range(last, n - 1, -1):
        if long_cnt is None and lows[i] < min(lows[i - n:i]):
            long_cnt = last - i
        if short_cnt is None and highs[i] > max(highs[i - n:i]):
            short_cnt = last - i
        if long_cnt is not None and short_cnt is not None:
            break
    g.stall_days = {'long': long_cnt, 'short': short_cnt}
    return g.stall_days


def stall_edge(ctx):
    """按新完成 bar 边沿评判（每根新 bar 仅一次）：计数恰达 stall_period 时返回 'long'/'short'，否则 None。
    首个 bar 键仅作基线（切片前已达成的不触发）；持仓中达成 = 错过，不补触发"""
    p = g.params
    if p['stall_period'] <= 0:
        return None
    st = stall_state(ctx)
    if st is None:
        return None
    if g.stall_seen_key is None:
        g.stall_seen_key = g.stall_key
        return None
    if g.stall_key == g.stall_seen_key:
        return None
    g.stall_seen_key = g.stall_key
    if p['fixed_dir'] in (0, 1) and st['long'] == p['stall_period']:
        return 'long'
    if p['fixed_dir'] in (0, -1) and st['short'] == p['stall_period']:
        return 'short'
    return None

# ==================== NRn 收缩并行入场（bar_freq 口径） ====================


def nr_event(ctx, lv):
    """按新完成 bar 刷新 NRn 事件：最新 bar 为近 nr_n 根振幅最小者则返回其高低点。
    事件仅在该 NRn bar 之后一个 bar 期间有效；每事件仅触发一次；nr_n=0 关闭"""
    if lv is None or g.params['nr_n'] <= 0:
        return None
    if g.nr_key != g.lv_key:
        g.nr_key = g.lv_key
        g.nr_fired = False
        g.nr = {'high': lv['nr_high'], 'low': lv['nr_low']} if lv['is_nr'] else None
        if g.nr is not None:
            log.info(f"{log_pfx()} NR_SET n={g.params['nr_n']} "
                     f"high={g.nr['high']:.1f} low={g.nr['low']:.1f}")
    return g.nr

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
        if o is not None and o.filled > 0:
            g.entry_log[(c, side)] = {'price': o.price, 'n': n_val}   # 部分成交也记录，供状态重建恢复
        warn_once('open_' + c, f"{log_pfx()} ❌ WARN open unfilled (limit board?) {c} lots={lots}")
        return False
    stop_line = o.price - direction * p['stop_dist_n'] * n_val
    if not g.legs:
        g.close_pnl_acc = 0.0      # 新仓开始，清空上一笔的累计盈亏
        g.close_retry = None
        g.trade_reason = reason    # 本笔交易的入场类型（首仓决定；加仓不改变）
    g.legs.append({'contract': c, 'side': side, 'lots': lots, 'entry_price': o.price,
                   'n_at_entry': n_val, 'stop_line': stop_line})
    g.stop_line = stop_line
    g.entry_log[(c, side)] = {'price': o.price, 'n': n_val}
    log.info(f"{log_pfx()} ✅{trade_mark(side, reason)} {reason.upper()} {side} {c} {lots}@{o.price:.1f} "
             f"N={n_val:.1f} stop={stop_line:.1f} units={len(g.legs)} equity={ctx.portfolio.total_value:.0f}")
    g.warned.pop('open_' + c, None)
    return True


def realize_leg_pnl(leg, exit_price):
    """实现一条 leg 的盈亏并计入整笔累计（分批平仓 / 移仓平旧腿共用）"""
    pnl = (exit_price - leg['entry_price']) * leg['lots'] * g.params['multiplier'] * side_to_dir(leg['side'])
    g.close_pnl_acc += pnl
    return pnl


def close_all(ctx, reason):
    """全部平仓（止损/反向突破出场）；未平完置 close_retry，由分钟检查每 bar 重试直至清仓"""
    if not g.legs:
        return
    pos_side = g.legs[0]['side']
    pos_contract = g.legs[0]['contract']
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
        pnl = realize_leg_pnl(leg, o.price)
        log.info(f"{log_pfx()} ✅{trade_mark(leg['side'], reason)} CLOSE {leg['side']} {c} {leg['lots']}@{o.price:.1f} "
                 f"pnl={pnl:.0f} reason={reason} entry={leg['entry_price']:.1f} stop={leg['stop_line']:.1f}")
        g.legs.remove(leg)
        g.warned.pop('close_' + c, None)
    if not g.legs:
        g.stop_line = None
        g.close_retry = None
        g.entry_log.pop((pos_contract, pos_side), None)
        total_pnl = g.close_pnl_acc
        scope = g.params.get('s1_scope', 'current')
        reason_in = g.trade_reason
        if g.params['s1_skip'] and total_pnl > 0:
            armed = []
            if scope == 'current' or (scope in ('breakout', 'breakout_nr') and reason_in == 'entry'):
                g.skip_next_breakout = True
                g.skip_level = None
                g.skip_side = None
                armed.append('breakout')
            if scope == 'current' or (scope == 'breakout_nr' and reason_in == 'entry_nr'):
                g.nr_skip_next = True
                armed.append('nr')
            if armed:
                log.info(f"{log_pfx()} S1_SKIP_ARM side={pos_side} reason={reason_in} scope={scope} "
                         f"armed={'+'.join(armed)} (下一次同类入场跳过，不分方向)")
            else:
                log.info(f"{log_pfx()} S1_SKIP_NOARM side={pos_side} reason={reason_in} scope={scope} "
                         f"（该入场类型不产生 skip）")
        log.info(f"{log_pfx()} ✅ 清仓完成，当前空仓 trade_pnl={total_pnl:.0f}")
        g.close_pnl_acc = 0.0
        g.trade_reason = None
    else:
        g.close_retry = reason


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
    g.close_pnl_acc = 0.0      # 重建后原入场价/已平盈亏不可知，累计盈亏清零
    g.trade_reason = None      # 入场类型同样不可知
    for (c, side), amt in actual.items():
        rec = g.entry_log.get((c, side))
        d = bar_levels(ctx)
        n_val = d['n'] if d is not None else 0.0
        if rec is not None:
            price = rec['price']            # 优先恢复真实入场价，避免现价兜底偏离
            if rec['n'] > 0:
                n_val = rec['n']
        else:
            price = get_current_data()[c].last_price
        stop_line = g.stop_line if g.stop_line is not None else \
            price - side_to_dir(side) * g.params['stop_dist_n'] * n_val
        g.legs.append({'contract': c, 'side': side, 'lots': amt, 'entry_price': price,
                       'n_at_entry': n_val, 'stop_line': stop_line})
        g.stop_line = stop_line
        g.entry_log[(c, side)] = {'price': price, 'n': n_val}

# ==================== 分钟检查（止损/出场/加仓/入场） ====================


def turtle_minute_checks(ctx, bar, lv, nr_ev):
    """海龟核心：共享 2N 止损 → 反向突破出场 → 加仓 → NR 收缩 / 通道突破入场（全部盘中分钟级）"""
    if g.migrating is not None:
        return
    p = g.params
    if bar is None:
        return
    if g.legs:
        if g.close_retry is not None:
            close_all(ctx, g.close_retry)   # 上次平仓未平完（停板等），继续重试直至清仓
            return
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
    # 4) 并行入场：NRn 收缩 bar 极值突破（价位低于 20 日通道，先判定）
    allow_long = p['fixed_dir'] in (0, 1)
    allow_short = p['fixed_dir'] in (0, -1)
    if nr_ev is not None and not g.nr_fired:
        if allow_long and bar['high'] >= nr_ev['high']:
            if g.nr_skip_next and p['s1_scope'] in ('current', 'breakout_nr'):
                g.nr_skip_next = False
                g.nr_fired = True
                log.info(f"{log_pfx()} NR_SKIP long level={nr_ev['high']:.1f} (系统1：上次交易盈利)")
                return
            if open_unit(ctx, 1, lv['n'], 'entry_nr'):
                g.nr_fired = True
                log.info(f"{log_pfx()} NR_BREAK long level={nr_ev['high']:.1f}")
            return
        if allow_short and bar['low'] <= nr_ev['low']:
            if g.nr_skip_next and p['s1_scope'] in ('current', 'breakout_nr'):
                g.nr_skip_next = False
                g.nr_fired = True
                log.info(f"{log_pfx()} NR_SKIP short level={nr_ev['low']:.1f} (系统1：上次交易盈利)")
                return
            if open_unit(ctx, -1, lv['n'], 'entry_nr'):
                g.nr_fired = True
                log.info(f"{log_pfx()} NR_BREAK short level={nr_ev['low']:.1f}")
            return
    # 5) 入场：突破前 entry_n 根 bar 极值（fixed_dir 限定方向；系统1 跳过规则）
    if allow_long and bar['high'] >= lv['entry_long']:
        if g.skip_next_breakout:
            g.skip_next_breakout = False
            g.skip_level = lv['entry_long']
            g.skip_side = 'long'
            log.info(f"{log_pfx()} BREAKOUT_SKIP long level={lv['entry_long']:.1f} (系统1：上次交易盈利)")
            return
        if g.skip_side == 'long' and g.skip_level is not None and lv['entry_long'] == g.skip_level:
            return                       # 被跳过的突破价位尚未刷新，继续等待新极值
        if g.skip_side == 'long' and g.skip_level is not None:
            log.info(f"{log_pfx()} SKIP_RELEASED long old={g.skip_level:.1f} new={lv['entry_long']:.1f}")
            g.skip_level = None
            g.skip_side = None
        open_unit(ctx, 1, lv['n'], 'entry')
        return
    if allow_short and bar['low'] <= lv['entry_short']:
        if g.skip_next_breakout:
            g.skip_next_breakout = False
            g.skip_level = lv['entry_short']
            g.skip_side = 'short'
            log.info(f"{log_pfx()} BREAKOUT_SKIP short level={lv['entry_short']:.1f} (系统1：上次交易盈利)")
            return
        if g.skip_side == 'short' and g.skip_level is not None and lv['entry_short'] == g.skip_level:
            return
        if g.skip_side == 'short' and g.skip_level is not None:
            log.info(f"{log_pfx()} SKIP_RELEASED short old={g.skip_level:.1f} new={lv['entry_short']:.1f}")
            g.skip_level = None
            g.skip_side = None
        open_unit(ctx, -1, lv['n'], 'entry')

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
    g.close_retry = None      # 移仓接管持仓，作废未完成的平仓重试
    log.info(f"{log_pfx()} MIGRATE start {g.current_contract}->{new_contract} side={side} lots={total}")
    retry_migration(ctx)


def retry_migration(ctx):
    """推进移仓：每 bar 重试未成交腿；两腿全成交后统一更新 legs 并做价差平移。
    开新腿前做保证金预检，不足则回退为"先平旧再开新"（旧腿平仓释放保证金后再开新腿）"""
    m = g.migrating
    if m is None:
        return
    if has_open_order(m['new']) or has_open_order(m['old']):
        return
    p = g.params
    if m['new_price'] is None and not m.get('close_first'):
        price = get_current_data()[m['new']].last_price
        lots = fit_lots_to_margin(m['total'], price, p['multiplier'], p['margin_rate'],
                                  ctx.subportfolios[0].available_cash)
        if lots < m['total']:
            warn_once('mig_margin', f"{log_pfx()} WARN migrate margin insufficient {m['new']} "
                                    f"lots={m['total']} afford={lots}，回退为先平旧再开新")
            m['close_first'] = True
        else:
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
        pnl = 0.0
        for leg in g.legs:
            pnl += realize_leg_pnl(leg, o.price)
        log.info(f"{log_pfx()} MIGRATE close {m['old']} {m['side']} {m['total']}@{o.price:.1f} pnl={pnl:.0f}")
    if m['new_price'] is None:   # 先平旧模式：旧腿已平、保证金释放，再开新腿
        price = get_current_data()[m['new']].last_price
        lots = fit_lots_to_margin(m['total'], price, p['multiplier'], p['margin_rate'],
                                  ctx.subportfolios[0].available_cash)
        if lots < m['total']:
            warn_once('mig_new_short', f"{log_pfx()} WARN migrate open margin insufficient {m['new']} "
                                       f"lots={m['total']} afford={lots}（继续等待）")
            return
        o = order(m['new'], m['total'], side=m['side'])
        if o is None or o.filled < m['total']:
            warn_once('mig_new', f"{log_pfx()} WARN migrate open unfilled {m['new']} lots={m['total']}")
            return
        m['new_price'] = o.price
        g.warned.pop('mig_new', None)
        log.info(f"{log_pfx()} MIGRATE open {m['new']} {m['side']} {m['total']}@{o.price:.1f}")
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
    g.entry_log.pop((m['old'], m['side']), None)
    g.entry_log[(m['new'], m['side'])] = {'price': m['new_price'], 'n': n_val}
    g.current_contract = m['new']
    g.migrating = None
    log.info(f"{log_pfx()} MIGRATE done {m['old']}->{m['new']} delta={delta:.1f} stop={g.stop_line:.1f}")

# ==================== 分钟单入口 ====================


def risk_and_signal_routine(ctx):
    """分钟单入口：一致性校验 → NR 事件刷新 → 极值停滞并行入场 → 海龟检查（止损/出场/加仓/NR/突破入场）→ 14:45 移仓检查与 LEVELS"""
    try:
        p = g.params
        if g.current_contract is None:
            g.current_contract = get_dominant_future(p['symbol'])
            log.info(f"{log_pfx()} dominant init {g.current_contract}")
        check_state_consistency(ctx)
        lv = bar_levels(ctx)
        bar = current_minute_bar(ctx, g.current_contract)
        nr_ev = nr_event(ctx, lv)
        stall_side = stall_edge(ctx)
        if stall_side is not None and not g.legs and bar is not None and lv is not None:
            direction = 1 if stall_side == 'long' else -1
            log.info(f"{log_pfx()} STALL_FIRE {stall_side}：连续{p['stall_period']}日极值停滞，入场")
            open_unit(ctx, direction, lv['n'], 'entry_stall')
        elif lv is not None:
            turtle_minute_checks(ctx, bar, lv, nr_ev)
        # 移仓期间旧腿仍持有：触止损/出场则中止移仓（取消未成交新腿，或直接平掉已开新腿）
        if g.migrating is not None and g.migrating['old_price'] is None and g.legs \
                and bar is not None and lv is not None:
            side = g.legs[0]['side']
            direction = side_to_dir(side)
            stop_hit = g.stop_line is not None and (
                bar['low'] <= g.stop_line if direction == 1 else bar['high'] >= g.stop_line)
            exit_line = lv['exit_long'] if direction == 1 else lv['exit_short']
            exit_hit = bar['low'] <= exit_line if direction == 1 else bar['high'] >= exit_line
            if stop_hit or exit_hit:
                reason = 'stop' if stop_hit else 'exit_breakout'
                m = g.migrating
                if m['new_price'] is None:
                    for o in get_open_orders().values():
                        if o.security == m['new']:
                            cancel_order(o)
                else:
                    o2 = order(m['new'], -m['total'], side=side)
                    if o2 is None or o2.filled < m['total']:
                        warn_once('mig_abort_new', f"{log_pfx()} WARN migrate abort close new unfilled {m['new']}")
                g.migrating = None
                log.info(f"{log_pfx()} MIGRATE_ABORT {reason} side={side} 移仓中触止损/出场，中止移仓并平仓")
                close_all(ctx, reason)
        retry_migration(ctx)
        if ctx.current_dt.strftime('%H:%M') != p['signal_time']:
            return
        if g.last_signal_date == ctx.current_dt.date():
            return
        check_dominant(ctx)
        if lv is not None:
            nr_txt = f"NRH={nr_ev['high']:.1f} NRL={nr_ev['low']:.1f}" if nr_ev is not None else 'NRH=- NRL=-'
            log.info(f"{log_pfx()} LEVELS[{p['bar_freq']}] {g.current_contract} N={lv['n']:.1f} "
                     f"EL={lv['entry_long']:.1f} XL={lv['exit_long']:.1f} "
                     f"ES={lv['entry_short']:.1f} XS={lv['exit_short']:.1f} {nr_txt} "
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
        g.close_pnl_acc = 0.0
        g.close_retry = None
        g.entry_log = {}
        g.lv = None
        g.lv_key = None
        g.stall_days = None
        g.stall_key = None
        g.stall_seen_key = None
        g.nr = None
        g.nr_key = None
        g.nr_fired = False
        g.nr_skip_next = False
        g.trade_reason = None
        g.last_signal_date = None
        g.skip_next_breakout = False
        g.skip_level = None
        g.skip_side = None
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
