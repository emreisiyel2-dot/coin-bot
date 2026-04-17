import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SimulatorConfig:
    taker_fee_pct: float = 0.001
    maker_fee_pct: float = 0.0008
    spread_pct: float = 0.0004          # toplam spread; her yön %0.02
    atr_slippage_factor: float = 0.05   # ATR/price'ın bu oranı kadar slippage
    volume_impact_factor: float = 0.01  # qty/volume başına price impact
    use_taker: bool = True


@dataclass
class ExecutionResult:
    effective_price: float
    fee: float
    slippage_pct: float
    spread_cost: float


class ExecutionSimulator:
    """
    Gerçekçi emir dolumu:
      - ATR-bazlı volatilite slippage
      - Volume impact (büyük emir → daha fazla etki)
      - Bid/ask spread
      - Maker / taker fee
    """

    def __init__(self, config: SimulatorConfig | None = None) -> None:
        self._config = config or SimulatorConfig()

    def simulate_fill(
        self,
        side: str,           # "BUY" | "SELL"
        signal_price: float,
        atr: float,
        volume: float,
        quantity: float,
    ) -> ExecutionResult:
        cfg = self._config

        # Volatilite slippage (ATR / fiyat oranına bağlı)
        vol_slippage = cfg.atr_slippage_factor * (atr / signal_price) if signal_price > 0 else 0.0

        # Volume impact (büyük emir ince piyasada daha pahalı)
        vol_impact = cfg.volume_impact_factor * (quantity / max(volume, 1e-9))

        total_slippage_pct = vol_slippage + vol_impact
        half_spread = cfg.spread_pct / 2
        fee_rate = cfg.taker_fee_pct if cfg.use_taker else cfg.maker_fee_pct

        if side == "BUY":
            effective_price = signal_price * (1 + total_slippage_pct + half_spread)
        else:  # SELL
            effective_price = signal_price * (1 - total_slippage_pct - half_spread)

        fee = effective_price * quantity * fee_rate
        spread_cost = signal_price * half_spread

        logger.debug(
            "simulate_fill | %s | signal=%.2f fill=%.2f slippage=%.4f%% fee=%.4f",
            side, signal_price, effective_price, total_slippage_pct * 100, fee,
        )

        return ExecutionResult(
            effective_price=effective_price,
            fee=fee,
            slippage_pct=total_slippage_pct,
            spread_cost=spread_cost,
        )
