import { useState, useEffect, useCallback, useMemo } from 'react'
import CoinCard from './components/CoinCard'
import {
  PortfolioPanel,
  PositionStatusPanel,
  StrategyPerfPanel,
  ScannerPanel,
  ActiveTradesSection,
  TradeHistoryPanel,
  HealthBar,
} from './components/InfoPanels'
import './App.css'

function getMovementPct(sparkline) {
  if (!sparkline || sparkline.length < 2) return 0
  const last = sparkline[sparkline.length - 1]
  const prev = sparkline[sparkline.length - 2]
  if (prev === 0) return 0
  return ((last - prev) / prev) * 100
}

const REGIME_TR = { bearish: 'DÜŞÜŞ TRENDİ', bullish: 'YÜKSELİŞ TRENDİ', neutral: 'NÖTR' }

export default function App() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch('/dashboard_summary.json')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, 3000)
    return () => clearInterval(id)
  }, [fetchData])

  const sortedSymbols = useMemo(() => {
    const symbols = data?.scanner_dashboard?.symbols || []
    if (symbols.length === 0) return symbols
    console.log('Dashboard symbols:', symbols.length)

    return [...symbols].sort((a, b) => {
      const aActive = a.signal === 'ACTIVE_LONG' || a.signal === 'ACTIVE_SHORT'
      const bActive = b.signal === 'ACTIVE_LONG' || b.signal === 'ACTIVE_SHORT'
      if (aActive !== bActive) return bActive ? 1 : -1

      const aNoSignal = a.signal === 'no_signal'
      const bNoSignal = b.signal === 'no_signal'
      if (aNoSignal !== bNoSignal) return aNoSignal ? 1 : -1

      const aMove = Math.abs(getMovementPct(a.sparkline))
      const bMove = Math.abs(getMovementPct(b.sparkline))
      if (bMove !== aMove) return bMove - aMove

      return (b.score || 0) - (a.score || 0)
    })
  }, [data])

  const activeTrades = useMemo(() => {
    const symbols = data?.scanner_dashboard?.symbols || []
    return symbols.filter(s => s.signal === 'ACTIVE_LONG' || s.signal === 'ACTIVE_SHORT')
  }, [data])

  if (error) {
    return (
      <div className="app">
        <div className="error-banner">Veri yüklenemedi: {error}</div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="app">
        <div className="loading">Yükleniyor...</div>
      </div>
    )
  }

  return (
    <div className="app">
      <HealthBar data={data} />

      <section className="money-panels">
        <PortfolioPanel data={data} />
        <PositionStatusPanel data={data} />
        <StrategyPerfPanel data={data} />
        <ScannerPanel data={data} />
      </section>

      <section className="history-panels">
        <ActiveTradesSection trades={activeTrades} />
        <TradeHistoryPanel data={data} />
      </section>

      <main className="grid">
        {sortedSymbols.length === 0
          ? <div className="empty-state">Tarama verisi henüz yok</div>
          : sortedSymbols.map(s => (
              <CoinCard key={s.symbol} symbol={s} />
            ))
        }
      </main>
    </div>
  )
}
