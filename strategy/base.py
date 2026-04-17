from abc import ABC, abstractmethod

import pandas as pd

from strategy.signals import Signal


class BaseStrategy(ABC):
    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Son kapalı bara bakarak BUY / SELL / HOLD sinyali üret."""
        ...
