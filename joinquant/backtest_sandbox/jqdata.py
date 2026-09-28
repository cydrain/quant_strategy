# -*- coding: utf-8 -*-
"""
聚宽 API 本地模拟层（沙盒）
让 joinquant/ 下的策略文件**不做任何修改**即可在本地逐分钟回放。

- 数据来源：local_data/ 目录（研究环境导出的 daily_*.csv / min1_*.csv / dominant.csv / trade_days.csv）
- 撮合模型（拟合自聚宽回测日志 27/27 成交样本）：
    买入成交价 = 当前分钟 bar 收盘价；卖出成交价 = 当前分钟 bar 收盘价 − 一个最小变动价位(tick)
- 账户模型（对账验证）：权益 = 起始资金 + Σ已实现盈亏 − Σ手续费 + Σ浮动盈亏
    手续费 = 成交额 × 费率；保证金 = 最新价 × 手数 × 乘数 × 保证金率；可用资金 = 权益 − 保证金占用
- 只提供 04 策略用到的 API 子集；未知 API 直接 AttributeError，暴露缺口而非静默容错。
"""
import datetime as dt
import glob
import os

import numpy as np
import pandas as pd

# ==================== g / context / log ====================


class _Bag:
    """聚宽 g / context 这类自由属性对象"""


g = _Bag()
context = _Bag()


class _Logger:
    _LV = {'debug': 10, 'info': 20, 'warning': 30, 'warn': 30, 'error': 40, 'critical': 50}

    def __init__(self):
        self._fh = None
        self._lv = {'strategy': 20, 'order': 40, 'system': 40}
        self._first = True

    def _bind(self, path, level='info'):
        self._fh = open(path, 'w', encoding='utf-8')
        self._lv['strategy'] = self._LV[level]
        self._first = True

    def set_level(self, category, level):
        self._lv[category] = self._LV.get(str(level).lower(), 20)

    def _emit(self, name, no, msg):
        if self._fh is None or no < self._lv.get('strategy', 20):
            return
        ts = getattr(context, 'current_dt', dt.datetime(2026, 1, 1)).strftime('%Y-%m-%d %H:%M:%S')
        # 聚宽导出日志：记录之间以空行分隔，首条前与文件末尾无空行
        if self._first:
            self._first = False
        else:
            self._fh.write('\n')
        self._fh.write(f"{ts} - {name:<5} - {msg}\n")
        self._fh.flush()

    def debug(self, msg): self._emit('DEBUG', 10, msg)
    def info(self, msg): self._emit('INFO', 20, msg)
    def warn(self, msg): self._emit('WARN', 30, msg)
    def warning(self, msg): self._emit('WARN', 30, msg)
    def error(self, msg): self._emit('ERROR', 40, msg)


log = _Logger()

# ==================== 数据层 ====================


class _DataStore:
    def __init__(self, root):
        self.root = root
        td = pd.read_csv(os.path.join(root, 'trade_days.csv'))
        self.trade_days = [d.date() for d in pd.to_datetime(td['date'])]
        dm = pd.read_csv(os.path.join(root, 'dominant.csv'))
        self.dominant_map = {d.date(): c for d, c in zip(pd.to_datetime(dm['date']), dm['dominant'])}
        self.daily = {}
        self.minute = {}
        self.labels = set()
        for f in sorted(glob.glob(os.path.join(root, 'daily_*.csv'))):
            base = os.path.basename(f)[len('daily_'):-len('.csv')]
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            self.daily[base] = df[~df['close'].isna()]
        for f in sorted(glob.glob(os.path.join(root, 'min1_*.csv'))):
            base = os.path.basename(f)[len('min1_'):-len('.csv')]
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            self.minute[base] = {
                'ts': df.index.values.astype('datetime64[ns]'),
                'open': df['open'].values.astype(float),
                'high': df['high'].values.astype(float),
                'low': df['low'].values.astype(float),
                'close': df['close'].values.astype(float),
            }
            self.labels.update(df.index.strftime('%H:%M').unique())

    @staticmethod
    def base(sec):
        return sec.split('.')[0]

    def minute_slice(self, sec, t, count):
        m = self.minute.get(self.base(sec))
        if m is None:
            return None
        i = int(np.searchsorted(m['ts'], np.datetime64(pd.Timestamp(t)), side='right'))
        j = max(0, i - count)
        return {k: v[j:i] for k, v in m.items()}

    def last_price(self, sec, t):
        sl = self.minute_slice(sec, t, 1)
        if sl is not None and len(sl['close']):
            return float(sl['close'][-1])
        df = self.daily.get(self.base(sec))
        if df is None:
            return None
        sub = df[df.index <= pd.Timestamp(t)]
        return float(sub['close'].iloc[-1]) if len(sub) else None

    def daily_hist(self, sec, t, count):
        df = self.daily.get(self.base(sec))
        if df is None:
            return pd.DataFrame()
        # 排除"当日"：日线索引为 00:00，须按日期比较（时间戳比较会把当天也算进来）
        return df[df.index.date < t.date()].tail(count)


_store = None

# ==================== 账户层 ====================


class _Order:
    def __init__(self, security, amount, price, filled, side):
        self.security = security
        self.amount = amount
        self.price = price        # 成交价（已含沙盒撮合模型的卖出减一跳动）
        self.filled = filled
        self.side = side


class _Account:
    def __init__(self, cash, multiplier, tick):
        self.init = float(cash)
        self.mult = multiplier
        self.tick = tick
        self.margin_rate = 0.12
        self.rate_open = 0.000023
        self.rate_close = 0.000023
        self.rate_close_today = 0.000023
        self.pos = {}             # (sec, 'long'/'short') -> {'amount', 'avg'}
        self.realized = 0.0
        self.fees = 0.0
        self.fills = []

    def order(self, sec, amount, side):
        price = _store.last_price(sec, context.current_dt)
        if price is None or not amount:
            return None
        # 买卖方向（决定成交价）：long+正 / short+负 = 买；long+负 / short+正 = 卖
        is_buy = (side == 'long') == (amount > 0)
        fill = price if is_buy else price - self.tick
        # 开/平方向（决定头寸增减）：正数量=开/加该侧，负数量=平该侧（与策略"平仓用负数量"一致）
        key = (sec, side)
        p = self.pos.get(key)
        if amount > 0:
            if p is None:
                self.pos[key] = {'amount': float(amount), 'avg': fill}
            else:
                p['avg'] = (p['avg'] * p['amount'] + fill * amount) / (p['amount'] + amount)
                p['amount'] += amount
            self.fees += fill * self.mult * abs(amount) * self.rate_open
        else:
            n = -amount
            if p is None or p['amount'] < n - 1e-9:
                log.warn(f"order close exceeds position: {sec} {side} amt={amount}")
                return None
            d = 1 if side == 'long' else -1
            self.realized += (fill - p['avg']) * n * self.mult * d
            self.fees += fill * self.mult * n * self.rate_close
            p['amount'] -= n
            if p['amount'] <= 1e-9:
                del self.pos[key]
        self.fills.append((context.current_dt, sec, amount, side, fill))
        return _Order(sec, amount, fill, abs(amount), side)

    def unrealized(self):
        v = 0.0
        for (sec, side), p in self.pos.items():
            lp = _store.last_price(sec, context.current_dt)
            if lp is None:
                lp = p['avg']
            v += (lp - p['avg']) * p['amount'] * self.mult * (1 if side == 'long' else -1)
        return v

    def margin(self):
        m = 0.0
        for (sec, side), p in self.pos.items():
            lp = _store.last_price(sec, context.current_dt)
            if lp is None:
                lp = p['avg']
            m += lp * p['amount'] * self.mult * self.margin_rate
        return m

    def total_value(self):
        return self.init + self.realized - self.fees + self.unrealized()

    def available_cash(self):
        return self.total_value() - self.margin()


_account = None


class _Position:
    def __init__(self, amount):
        self.total_amount = amount
        self.closeable_amount = amount
        self.today_amount = 0


class _SubPortfolio:
    @property
    def available_cash(self):
        return _account.available_cash()

    @property
    def long_positions(self):
        return {sec: _Position(p['amount']) for (sec, s), p in _account.pos.items() if s == 'long'}

    @property
    def short_positions(self):
        return {sec: _Position(p['amount']) for (sec, s), p in _account.pos.items() if s == 'short'}


class _Portfolio:
    def __init__(self, cash):
        self.starting_cash = cash

    @property
    def total_value(self):
        return _account.total_value()


# ==================== 聚宽 API ====================


def attribute_history(security, count, unit='1d', fields=('open', 'close', 'high', 'low', 'volume', 'money'),
                      skip_paused=True, df=True, fq='pre'):
    """日线：截至当前日期前一个交易日的最近 count 根（不含当日）"""
    if unit != '1d':
        raise NotImplementedError(f'attribute_history unit={unit} 未实现（策略仅用 1d）')
    sub = _store.daily_hist(security, context.current_dt, count)
    return sub[list(fields)].copy()


def get_bars(security, count, unit='1m', fields=None, include_now=False, end_dt=None,
             fq_ref_date=None, df=False):
    """分钟线：截至当前时刻（含）的最近 count 根，返回 numpy 结构化数组"""
    if unit != '1m':
        raise NotImplementedError(f'get_bars unit={unit} 未实现（策略仅用 1m）')
    if fields is None:
        fields = ['date', 'open', 'close', 'high', 'low', 'volume', 'money']
    sl = _store.minute_slice(security, context.current_dt, count)
    if sl is None or len(sl['ts']) == 0:
        return None
    cols = []
    for f in fields:
        if f == 'date':
            cols.append(sl['ts'])
        elif f in ('open', 'high', 'low', 'close'):
            cols.append(sl[f])
        elif f in ('volume', 'money'):
            cols.append(np.zeros(len(sl['ts'])))
        else:
            raise NotImplementedError(f'get_bars field={f} 未实现')
    return np.rec.fromarrays(cols, names=list(fields))


class _SecData:
    pass


class _CurrentData:
    def __getitem__(self, sec):
        o = _SecData()
        o.security = sec
        o.last_price = _store.last_price(sec, context.current_dt)
        o.paused = False
        o.high_limit = None
        o.low_limit = None
        return o


def get_current_data():
    return _CurrentData()


def get_dominant_future(symbol, date=None):
    d = date if date is not None else context.current_dt.date()
    if isinstance(d, str):
        d = pd.Timestamp(d).date()
    if d in _store.dominant_map:
        return _store.dominant_map[d]
    prev = [k for k in _store.dominant_map if k <= d]
    return _store.dominant_map[max(prev)] if prev else None


def get_trade_days(start_date=None, end_date=None, count=None):
    ds = _store.trade_days
    if start_date is not None:
        s = pd.Timestamp(start_date).date()
        ds = [d for d in ds if d >= s]
    if end_date is not None:
        e = pd.Timestamp(end_date).date()
        ds = [d for d in ds if d <= e]
    if count:
        ds = ds[-count:]
    return ds


def get_open_orders(security=None):
    """沙盒为即时全额成交，无挂单"""
    return {}


def order(security, amount, side='long'):
    """市价单：即时成交（买入=bar收盘价；卖出=bar收盘价−tick）"""
    return _account.order(security, amount, side)


# ==================== 配置 / 注册 ====================

_RUN_DAILY = []


def run_daily(func, time='every_bar'):
    _RUN_DAILY.append((func, time))


def set_subportfolios(x):
    pass


def set_option(name, value):
    if name == 'futures_margin_rate':
        _account.margin_rate = value


def set_order_cost(cost, type='', ref=None):
    _account.rate_open = cost.open_commission
    _account.rate_close = cost.close_commission
    _account.rate_close_today = cost.close_today_commission


def set_slippage(x, type=''):
    """成交价模型中已拟合滑点效果（卖出减一跳动），此处仅记录不做二次处理"""
    _SLIPPAGE.append((x, type))


def set_benchmark(x):
    pass


class SubPortfolioConfig:
    def __init__(self, cash, type='stock'):
        self.cash = cash
        self.type = type


class OrderCost:
    def __init__(self, open_commission=0, close_commission=0, close_today_commission=0,
                 open_tax=0, close_tax=0, min_commission=0, **kw):
        self.open_commission = open_commission
        self.close_commission = close_commission
        self.close_today_commission = close_today_commission
        self.open_tax = open_tax
        self.close_tax = close_tax
        self.min_commission = min_commission


class PriceRelatedSlippage:
    def __init__(self, x):
        self.value = x


_SLIPPAGE = []

# ==================== 沙盒装配（runner 调用） ====================


def _configure(root, out_log, cash=1_000_000, tick=5.0, multiplier=10, level='info'):
    """初始化数据层与账户层；返回数据 store 供 runner 使用"""
    global _store, _account
    _store = _DataStore(root)
    _account = _Account(cash, multiplier, tick)
    context.current_dt = dt.datetime(2026, 1, 1)
    context.portfolio = _Portfolio(cash)
    context.subportfolios = [_SubPortfolio()]
    log._bind(out_log, level=level)
    return _store


def _schedule():
    """解析 run_daily 注册：返回 (every_bar 列表, {HH:MM: [funcs]})"""
    every = [f for f, t in _RUN_DAILY if t == 'every_bar']
    timed = {}
    for f, t in _RUN_DAILY:
        if t != 'every_bar':
            timed.setdefault(t, []).append(f)
    return every, timed


def _session_grid():
    """从数据中提取分钟 bar 标签：日盘标签 / 夜盘标签"""
    labels = sorted(_store.labels)
    day = [x for x in labels if '09:00' <= x <= '15:00']
    night = [x for x in labels if '21:00' <= x <= '23:00']
    return day, night
