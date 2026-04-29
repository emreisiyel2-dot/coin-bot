import Sparkline from './Sparkline'

const STATUS_CONFIG = {
  ACTIVE_LONG: { label: 'AKTİF LONG', color: '#00ff9c', bg: 'rgba(0,255,156,0.12)', glow: '#00ff9c' },
  ACTIVE_SHORT: { label: 'AKTİF SHORT', color: '#ff4d4f', bg: 'rgba(255,77,79,0.12)', glow: '#ff4d4f' },
  no_signal: { label: 'SİNYAL YOK', color: '#666', bg: 'rgba(102,102,102,0.12)', glow: null },
  blocked: { label: 'BLOKLANDI', color: '#fa8c16', bg: 'rgba(250,140,22,0.12)', glow: null },
}

function formatPrice(price) {
  if (price === 0) return '0.0000'
  if (price >= 1000) return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (price >= 1) return price.toFixed(2)
  if (price >= 0.01) return price.toFixed(4)
  return price.toFixed(6)
}

function formatPnl(pct) {
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

function getSparkColor(signal) {
  if (signal === 'ACTIVE_LONG') return '#00ff9c'
  if (signal === 'ACTIVE_SHORT') return '#ff4d4f'
  return '#555'
}

function getMovement(sparkline) {
  if (!sparkline || sparkline.length < 2) return { direction: 'flat', pct: 0 }
  const last = sparkline[sparkline.length - 1]
  const prev = sparkline[sparkline.length - 2]
  if (prev === 0) return { direction: 'flat', pct: 0 }
  const pct = ((last - prev) / prev) * 100
  if (pct > 0.001) return { direction: 'up', pct }
  if (pct < -0.001) return { direction: 'down', pct }
  return { direction: 'flat', pct: 0 }
}

export default function CoinCard({ symbol }) {
  const status = STATUS_CONFIG[symbol.signal] || STATUS_CONFIG.no_signal
  const isActive = symbol.signal === 'ACTIVE_LONG' || symbol.signal === 'ACTIVE_SHORT'
  const sparkColor = getSparkColor(symbol.signal)
  const movement = getMovement(symbol.sparkline)

  const cardStyle = {}
  if (isActive) {
    cardStyle.borderColor = status.glow
    cardStyle.boxShadow = `0 0 20px ${status.glow}33, inset 0 0 20px ${status.glow}11`
  } else if (movement.direction === 'up') {
    cardStyle.borderColor = 'rgba(0,255,156,0.25)'
    cardStyle.boxShadow = '0 0 12px rgba(0,255,156,0.06), inset 0 0 12px rgba(0,255,156,0.03)'
  } else if (movement.direction === 'down') {
    cardStyle.borderColor = 'rgba(255,77,79,0.25)'
    cardStyle.boxShadow = '0 0 12px rgba(255,77,79,0.06), inset 0 0 12px rgba(255,77,79,0.03)'
  }

  const moveColor = movement.direction === 'up' ? '#00ff9c' : movement.direction === 'down' ? '#ff4d4f' : '#666'
  const moveArrow = movement.direction === 'up' ? '↑' : movement.direction === 'down' ? '↓' : ''
  const movePct = movement.direction === 'flat' ? '' : `${moveArrow}${Math.abs(movement.pct) < 0.01 ? movement.pct.toFixed(4) : movement.pct.toFixed(2)}%`

  return (
    <div className="coin-card" style={cardStyle}>
      <div className="coin-card-header">
        <span className="coin-symbol">{symbol.symbol.replace('/USDT', '')}
          <span className="coin-pair">/USDT</span>
        </span>
        <div className="coin-card-badges">
          {movePct && (
            <span className="move-badge" style={{ color: moveColor }}>
              {movePct}
            </span>
          )}
          <span className="status-badge" style={{ color: status.color, background: status.bg }}>
            {status.label}
          </span>
        </div>
      </div>

      <div className="coin-price">{formatPrice(symbol.last_price)}</div>

      <div className="coin-sparkline">
        <Sparkline data={symbol.sparkline} color={sparkColor} width={180} height={44} />
      </div>

      <div className="coin-meta">
        <span>Skor <strong>{symbol.score}</strong></span>
        <span>RSI <strong>{symbol.rsi?.toFixed(1)}</strong></span>
        <span>ATR <strong>{symbol.atr_pct?.toFixed(1)}%</strong></span>
      </div>

      <div className="coin-movement-row">
        <span className="coin-movement-label">Son hareket</span>
        <span className="coin-movement-value" style={{ color: moveColor }}>
          {movement.direction === 'flat' ? '—' : `${movement.direction === 'up' ? '+' : ''}${movement.pct.toFixed(3)}%`}
        </span>
      </div>

      {isActive && symbol.entry_price && (
        <div className="coin-position">
          <div className="position-row">
            <span>Giriş</span>
            <span>{formatPrice(symbol.entry_price)}</span>
          </div>
          <div className="position-row">
            <span>Güncel</span>
            <span>{formatPrice(symbol.last_price)}</span>
          </div>
          <div className="position-row pnl">
            <span>K/Z</span>
            <span style={{ color: symbol.pnl_pct >= 0 ? '#00ff9c' : '#ff4d4f' }}>
              {formatPnl(symbol.pnl_pct)}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
