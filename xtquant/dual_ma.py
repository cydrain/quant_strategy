# coding:gbk
# author: CaiYudong

import pandas as pd
import time
from datetime import datetime
from enum import Enum, unique
from xtquant import xtdata, xttrader, xtconstant
from xtquant.xttype import StockAccount

#_SYMBOL = 'RB9999.XSGE' # 默认交易品种(螺纹钢主力)
_SYMBOL = 'ru2509.SF'
_FAST_MA = 5
_SLOW_MA = 20
_KLINE_PERIOD = '1d'

@unique
class RunMode(Enum):
    BACKTEST = 0
    SIMULATE = 1
    REALTIME = 2

class DualMAStrategy:
    def __init__(self, mode=RunMode.BACKTEST, config=None):
        """
        双均线策略
        :param mode: 运行模式 - BACKTEST/SIMULATE/REALTIME
        :param config: 配置字典，包含账户信息等
        """
        # 默认配置
        self.default_config = {
            'symbol': _SYMBOL,
            'fast_ma': _FAST_MA,
            'slow_ma': _SLOW_MA,
            'period': '1d',                 # K线周期
            'capital': 1000000,             # 初始资金(回测用)
            'slippage': 0.001,              # 滑点(回测用)
            'commission_rate': 0.0002,      # 手续费率
            'max_position': 10,             # 最大持仓手数
            'sim_account': 1035916,         # 模拟账户
            'real_account': 'REAL_ACCOUNT', # 实盘账户
            'client_id': '18121046190'      # 客户端ID
        }

        # 合并用户配置
        self.config = {**self.default_config, **(config or {})}
        self.mode = mode

        # 初始化变量
        self.position = 0  # 当前持仓(正数表示多仓，负数表示空仓)
        self.trade_log = []  # 交易记录
        self.balance = self.config['capital']  # 资金余额
        self.equity_curve = []  # 权益曲线
        self.history_data = pd.DataFrame()  # 历史数据存储
        self.last_tick = None  # 最新tick数据

        # 初始化交易接口(模拟和实盘需要)
        if self.mode in [RunMode.SIMULATE, RunMode.REALTIME]:
            account = self.config['sim_account'] if self.mode == RunMode.SIMULATE else self.config['real_account']
            self.trader = xttrader.XtQuantTrader(self.config['client_id'])
            self.account = StockAccount(account, 'FUTURE')  # 期货账户

        # 订阅行情(模拟和实盘需要)
        if self.mode in [RunMode.SIMULATE, RunMode.REALTIME]:
            xtdata.subscribe_quote([self.config['symbol']], callback=self.on_tick)

    # ------------------------ 核心策略逻辑 ------------------------
    def calculate_ma(self, prices):
        """计算双均线"""
        if len(prices) < self.config['slow_ma']:
            return None, None

        fast_ma = prices[-self.config['fast_ma']:].mean()
        slow_ma = prices[-self.config['slow_ma']:].mean()
        return fast_ma, slow_ma

    def generate_signal(self, prices):
        """生成交易信号"""
        fast_ma, slow_ma = self.calculate_ma(prices)
        if fast_ma is None:
            return None

        # 多头信号: 快线上穿慢线且无多仓
        if fast_ma > slow_ma and self.position <= 0:
            return 'BUY'
        # 空头信号: 快线下穿慢线且无空仓
        elif fast_ma < slow_ma and self.position >= 0:
            return 'SELL'
        return None

    # ------------------------ 订单管理 ------------------------
    def place_order(self, signal, price):
        """下单函数(根据不同模式处理)"""
        if self.mode == RunMode.BACKTEST:
            # 回测模式 - 直接记录成交
            return self.backtest_order(signal, price)
        else:
            # 模拟/实盘模式 - 实际下单
            return self.live_order(signal, price)

    def backtest_order(self, signal, price):
        """回测下单"""
        # 计算实际成交价(考虑滑点)
        executed_price = price * (1 + self.config['slippage']) if signal == 'BUY' else price * (
                    1 - self.config['slippage'])

        # 计算手续费
        commission = abs(self.position) * price * self.config['commission_rate']

        # 更新持仓和资金
        if signal == 'BUY':
            self.position = self.config['max_position']
        else:
            self.position = -self.config['max_position']

        # 记录交易
        self.trade_log.append({
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': self.config['symbol'],
            'action': signal,
            'price': executed_price,
            'position': self.position,
            'commission': commission
        })

        # 更新资金曲线
        self.update_equity(executed_price)
        return True

    def live_order(self, signal, price):
        """实盘/模拟下单"""
        try:
            self.trader.connect()

            # 确定开平仓方向
            if (signal == 'BUY' and self.position <= 0) or (signal == 'SELL' and self.position >= 0):
                position_effect = xtconstant.OPEN  # 开仓
            else:
                position_effect = xtconstant.CLOSE  # 平仓

            # 设置限价单价格(防止滑点过大)
            order_price = price * 0.995 if signal == 'BUY' else price * 1.005

            # 下单
            order = {
                'stock_code': self.config['symbol'],
                'volume': self.config['max_position'],
                'price': order_price,
                'side': xtconstant.STOCK_BUY if signal == 'BUY' else xtconstant.STOCK_SELL,
                'position_effect': position_effect,
                'order_type': xtconstant.LIMIT_ORDER
            }

            # 执行订单
            order_id = self.trader.order_stock(self.account, order)

            # 记录交易(实际成交回报需要通过回调获取)
            self.trade_log.append({
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'symbol': self.config['symbol'],
                'action': signal,
                'price': order_price,
                'position': self.position,
                'order_id': order_id,
                'status': 'submitted'
            })

            return True
        except Exception as e:
            print(f"下单失败: {e}")
            return False
        finally:
            self.trader.disconnect()

    # ------------------------ 模式执行函数 ------------------------
    def run_backtest(self):
        """执行回测"""
        if self.mode != RunMode.BACKTEST:
            print("当前不是回测模式!")
            return False

        print(f"开始回测 {self.config['symbol']} {self.config['backtest_start']} 至 {self.config['backtest_end']}")

        xtdata.download_history_data(self.config['symbol'], self.config['period'], self.config['backtest_start'], self.config['backtest_end'], True)
        # 获取历史数据
        # self.history_data = xtdata.get_market_data_ex(
        #     stock_list=[self.config['symbol']],
        #     period=self.config['period'],
        #     start_time=self.config['backtest_start'],
        #     end_time=self.config['backtest_end']
        # )['close'].iloc[:, 0]
        data = xtdata.get_market_data_ex(
            stock_list=[self.config['symbol']],
            # stock_list=['000001.SZ'],
            period=self.config['period'],
            start_time=self.config['backtest_start'],
            end_time=self.config['backtest_end'],
            count=300
        )
        print(data)

        # 逐K线执行策略
        for i in range(self.config['slow_ma'], len(self.history_data)):
            current_prices = self.history_data.iloc[:i]
            signal = self.generate_signal(current_prices)

            if signal:
                current_price = self.history_data.iloc[i]
                self.place_order(signal, current_price)

        print("回测完成!")
        return True

    def on_tick(self, tick_data):
        """行情回调函数(用于模拟和实盘)"""
        if self.mode not in [RunMode.SIMULATE, RunMode.REALTIME]:
            return

        self.last_tick = tick_data[self.config['symbol']]
        current_price = self.last_tick['lastPrice']

        # 更新历史数据
        self.update_history(current_price)

        # 生成交易信号
        signal = self.generate_signal(self.history_data['close'])

        if signal:
            self.place_order(signal, current_price)

    def update_history(self, price):
        """更新历史数据"""
        now = datetime.now()
        new_data = pd.DataFrame({'close': [price]}, index=[now])

        if self.history_data.empty:
            self.history_data = new_data
        else:
            self.history_data = pd.concat([self.history_data, new_data])

    def update_equity(self, current_price):
        """更新权益曲线"""
        position_value = self.position * current_price
        self.equity_curve.append({
            'time': datetime.now(),
            'balance': self.balance,
            'position': self.position,
            'position_value': position_value,
            'total': self.balance + position_value
        })

    # ------------------------ 绩效分析 ------------------------
    def analyze(self):
        """分析策略绩效"""
        if not self.trade_log:
            print("没有交易记录可分析")
            return

        df = pd.DataFrame(self.trade_log)

        # 计算每次交易的盈亏
        df['pnl'] = 0.0
        for i in range(1, len(df)):
            if df.iloc[i]['action'] != df.iloc[i - 1]['action']:
                pct_change = (df.iloc[i]['price'] - df.iloc[i - 1]['price']) / df.iloc[i - 1]['price']
                df.loc[i, 'pnl'] = pct_change * df.iloc[i - 1]['position'] * self.config['max_position']

        # 计算累计收益率
        df['cumulative_return'] = (1 + df['pnl']).cumprod() - 1

        # 打印结果
        print("\n===== 策略绩效报告 =====")
        print(f"交易品种: {self.config['symbol']}")
        print(f"模式: {'回测' if self.mode == RunMode.BACKTEST else '模拟' if self.mode == RunMode.SIMULATE else '实盘'}")
        print(f"总交易次数: {len(df)}")
        print(f"累计收益率: {df['cumulative_return'].iloc[-1]:.2%}")
        print(f"最大回撤: {self.calculate_max_drawdown(df):.2%}")
        print("\n最近5笔交易:")
        print(df.tail(5))

        return df

    def calculate_max_drawdown(self, df):
        """计算最大回撤"""
        if 'cumulative_return' not in df.columns:
            return 0
        cum_returns = df['cumulative_return']
        peak = cum_returns.expanding().max()
        drawdown = (cum_returns - peak) / (peak + 1e-8)
        return drawdown.min()

    # ------------------------ 实用函数 ------------------------
    def get_current_position(self):
        """获取当前持仓"""
        if self.mode == RunMode.BACKTEST:
            return self.position
        else:
            try:
                self.trader.connect()
                positions = self.trader.query_stock_positions(self.account)
                for pos in positions:
                    if pos.stock_code == self.config['symbol']:
                        return pos.volume if pos.side == 1 else -pos.volume
                return 0
            finally:
                self.trader.disconnect()


# ======================= 使用示例 =======================
if __name__ == "__main__":
    # 示例1: 回测模式
    print("===== 回测模式示例 =====")
    backtest_config = {
        'symbol': _SYMBOL,
        'backtest_start': '20240101',
        'backtest_end': '20250531',
        'period': '1d'
    }
    backtest_strategy = DualMAStrategy(mode=RunMode.BACKTEST, config=backtest_config)
    backtest_strategy.run_backtest()
    backtest_strategy.analyze()

    # 示例2: 模拟模式 (需要在QMT环境中运行)
    # print("\n===== 模拟模式示例 =====")
    # simulate_config = {
    #     'symbol': _SYMBOL,
    #     'period': '1m',
    #     'max_position': 1
    # }
    # simulate_strategy = DualMAStrategy(mode=RunMode.SIMULATE, config=simulate_config)
    # print("模拟交易已启动...")
    # time.sleep(60)  # 模拟运行1分钟
    # simulate_strategy.analyze()

    # 示例3: 实盘模式 (需要真实账户)
    # print("\n===== 实盘模式示例 =====")
    # realtime_config = {
    #     'symbol': _SYMBOL,
    #     'period': '1m',
    #     'max_position': 1,
    #     'real_account': '您的实盘账户'
    # }
    # realtime_strategy = DualMAStrategy(mode=RunMode.REALTIME, config=realtime_config)
    # print("实盘交易已启动...请谨慎操作!")
