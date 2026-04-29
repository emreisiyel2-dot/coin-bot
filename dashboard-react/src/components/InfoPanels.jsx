const fmt = (v, decimals = 2) =>
  v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(decimals)

const fmtPct = (v) =>
  v == null || !Number.isFinite(Number(v)) ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`

const REGIME_TR = { bearish: 'DÜŞÜŞ TRENDİ', bullish: 'YÜKSELİŞ TRENDİ', neutral: 'NÖTR' }

function Panel({ title, icon, children, wide }) {
  return (
    <div className={`info-panel${wide ? ' info-panel-wide' : ''}`}>
      <div className="info-panel-title">{icon && <span className="panel-icon">{icon}</span>}{title}</div>
      <div className="info-panel-body">{children}</div>
    </div>
  )
}

function Row({ label, value, color, big }) {
  return (
    <div className="info-row">
      <span className="info-label">{label}</span>
      <span className={`info-value${big ? ' info-value-big' : ''}`} style={color ? { color } : undefined}>
        {value ?? '—'}
      </span>
    </div>
  )
}

export function HealthBar({ data }) {
  const updated = data.updated_utc
  const regime = data.market_regime
  const mode = data.loop_mode ? 'AUTO' : 'MANUEL'
  const scanCount = data.total_scans_today || data.symbols_scanned_count || 0
  const isAlive = !!updated

  const timeStr = updated
    ? new Date(updated).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '—'

  return (
    <div className="health-bar">
      <div className="health-item">
        <span className="health-dot" style={{ background: isAlive ? '#00ff9c' : '#ff4d4f' }} />
        <span className="health-label">Bot Durumu</span>
        <span className="health-value">{isAlive ? 'Çalışıyor' : 'Veri bekleniyor'}</span>
      </div>
      <div className="health-item">
        <span className="health-label">Son Güncelleme</span>
        <span className="health-value">{timeStr}</span>
      </div>
      <div className="health-item">
        <span className="health-label">Aktif Mod</span>
        <span className="health-value">{mode}</span>
      </div>
      <div className="health-item">
        <span className="health-label">Aktif Rejim</span>
        <span className="health-value" style={{ color: regime === 'bullish' ? '#00ff9c' : regime === 'bearish' ? '#ff4d4f' : '#aaa' }}>
          {REGIME_TR[regime] || regime?.toUpperCase() || '—'}
        </span>
      </div>
      <div className="health-item">
        <span className="health-label">Taranan Coin</span>
        <span className="health-value">{scanCount.toLocaleString()}</span>
      </div>
    </div>
  )
}

export function PortfolioPanel({ data }) {
  const cash = data.cash
  const equity = data.equity ?? data.peak_equity
  const dailyPnl = data.daily_pnl
  const startEquity = data.daily_start_equity
  const perf = data.strategy_performance || {}
  const maxDD = Object.values(perf).reduce((m, s) => Math.max(m, s.max_drawdown || 0), 0)

  const startingCapital = startEquity ?? equity ?? 10000
  const totalPnl = data.pnl_by_side
    ? (data.pnl_by_side.long || 0) + (data.pnl_by_side.short || 0)
    : 0

  const openAllocTotal = data.per_strategy_open_allocation
    ? Object.values(data.per_strategy_open_allocation).reduce((a, b) => a + (b || 0), 0)
    : null

  const returnPct = equity != null && startingCapital > 0
    ? ((equity - startingCapital) / startingCapital) * 100
    : null

  const pnlColor = totalPnl >= 0 ? '#00ff9c' : '#ff4d4f'
  const dailyColor = dailyPnl != null ? (dailyPnl >= 0 ? '#00ff9c' : '#ff4d4f') : undefined
  const returnColor = returnPct != null ? (returnPct >= 0 ? '#00ff9c' : '#ff4d4f') : undefined

  return (
    <Panel title="Portföy Özeti" icon="💰" wide>
      <div className="portfolio-grid">
        <Row label="Başlangıç Sermayesi" value={`${fmt(startingCapital)} USDT`} />
        <Row label="Güncel Bakiye" value={equity != null ? `${fmt(equity)} USDT` : '—'} big />
        <Row label="Nakit" value={cash != null ? `${fmt(cash)} USDT` : '—'} />
        <Row label="Açık Pozisyon Tutarı" value={openAllocTotal != null ? `${fmt(openAllocTotal)} USDT` : '—'} />
        <Row label="Toplam Kâr/Zarar" value={`${totalPnl >= 0 ? '+' : ''}${fmt(totalPnl)} USDT`} color={pnlColor} big />
        <Row label="Günlük Kâr/Zarar" value={dailyPnl != null ? `${fmt(dailyPnl)} USDT` : '—'} color={dailyColor} />
        <Row label="Maksimum Düşüş" value={maxDD > 0 ? `${fmt(maxDD)} USDT` : '—'} color="#ff4d4f" />
        <Row label="Getiri %" value={returnPct != null ? fmtPct(returnPct) : '—'} color={returnColor} big />
      </div>
    </Panel>
  )
}

export function PositionStatusPanel({ data }) {
  const openPos = (data.long_positions || 0) + (data.short_positions || 0)
  const openAlloc = data.per_strategy_open_allocation
  const openAllocTotal = openAlloc ? Object.values(openAlloc).reduce((a, b) => a + (b || 0), 0) : null
  const cash = data.cash
  const dailyPnl = data.daily_pnl
  const startEquity = data.daily_start_equity
  const perf = data.strategy_performance || {}
  const maxDD = Object.values(perf).reduce((m, s) => Math.max(m, s.max_drawdown || 0), 0)
  const killSwitch = data.strategy_kill_switch
  const dailyLoss = data.daily_realized_loss

  const ddPct = startEquity > 0 && maxDD > 0 ? (maxDD / startEquity) * 100 : 0

  let riskStatus, riskColor
  if (killSwitch) {
    riskStatus = 'Tehlikeli'; riskColor = '#ff4d4f'
  } else if (ddPct >= 2 || (dailyLoss != null && startEquity > 0 && (dailyLoss / startEquity) >= 0.02)) {
    riskStatus = 'Tehlikeli'; riskColor = '#ff4d4f'
  } else if (dailyPnl != null && dailyPnl < 0) {
    riskStatus = 'Dikkat'; riskColor = '#fa8c16'
  } else {
    riskStatus = 'Güvenli'; riskColor = '#00ff9c'
  }

  return (
    <Panel title="Pozisyon Durumu" icon="📊">
      <Row label="Açık Pozisyon" value={openPos} color={openPos > 0 ? '#00ff9c' : undefined} />
      <Row label="Kullanılan Tutar" value={openAllocTotal != null ? `${fmt(openAllocTotal)} USDT` : '—'} />
      <Row label="Kullanılabilir Nakit" value={cash != null ? `${fmt(cash)} USDT` : '—'} />
      <Row label="Maks. Pozisyon Oranı" value={data.max_position_pct != null ? `${fmt(data.max_position_pct * 100, 0)}%` : '—'} />
      <Row label="Günlük Zarar Koruması" value={dailyLoss != null ? `${fmt(dailyLoss)} USDT` : '—'}
        color={dailyLoss != null && dailyLoss > 0 ? '#ff4d4f' : undefined} />
      <Row label="Risk Durumu" value={riskStatus} color={riskColor} />
    </Panel>
  )
}

export function StrategyPerfPanel({ data }) {
  const strategies = data.active_strategies
  const perf = data.strategy_performance || {}
  const alloc = data.strategy_allocations || {}
  const base = data.base_allocations || {}

  const statusMap = {
    insufficient_data: { label: 'Veri yetersiz', color: '#888' },
    ok: { label: 'Aktif', color: '#00ff9c' },
    blocked: { label: 'Kısıtlandı', color: '#fa8c16' },
    kill: { label: 'Kısıtlandı', color: '#ff4d4f' },
  }

  const renderStrategy = (name) => {
    const p = perf[name] || {}
    const status = p.status ? (statusMap[p.status] || { label: p.status, color: '#888' }) : { label: '—', color: '#888' }
    const weight = alloc[name] ?? base[name] ?? null

    return (
      <div key={name} className="strategy-entry">
        <div className="strategy-header">
          <span className="strategy-name">{name}</span>
          <span className="strategy-status" style={{ color: status.color }}>{status.label}</span>
        </div>
        <div className="strategy-details">
          <span>İşlem: {p.total_trades ?? '—'}</span>
          <span>Win: {p.win_rate != null ? `${fmt(p.win_rate * 100, 0)}%` : '—'}</span>
          <span>K/Z: {p.total_pnl != null ? `${fmt(p.total_pnl)}` : '—'}</span>
          {weight != null && <span>Ağırlık: {fmt(weight * 100, 0)}%</span>}
        </div>
      </div>
    )
  }

  const strategyNames = Array.isArray(strategies) && strategies.length > 0
    ? strategies
    : [...new Set([...Object.keys(perf), ...Object.keys(alloc), ...Object.keys(base)])]

  return (
    <Panel title="Strateji Performansı" icon="⚡">
      {strategyNames.length > 0
        ? strategyNames.map(renderStrategy)
        : <div className="info-empty">Aktif strateji yok</div>
      }
    </Panel>
  )
}

export function ScannerPanel({ data }) {
  const counts = data.scanner_dashboard?.counts
  const total = counts?.total_scanned ?? data.total_scans_today ?? data.symbols_scanned_count ?? 0
  const open = counts?.open_positions ?? (data.long_positions || 0) + (data.short_positions || 0)
  const noSignal = counts?.no_signal ?? 0
  const blocked = counts?.blocked_signals ?? 0
  const accepted = counts?.accepted_signals ?? 0

  return (
    <Panel title="Tarama Özeti" icon="📡">
      <Row label="Toplam Taranan" value={total.toLocaleString()} />
      <Row label="Açık Pozisyon" value={open} color={open > 0 ? '#00ff9c' : undefined} />
      <Row label="Kabul Edildi" value={accepted} color={accepted > 0 ? '#00ff9c' : undefined} />
      <Row label="Sinyal Yok" value={noSignal} color="#666" />
      <Row label="Bloklandı" value={blocked} color={blocked > 0 ? '#fa8c16' : undefined} />
      {data.symbols_blocked_by_liquidity != null && (
        <Row label="Likidite Bloğu" value={data.symbols_blocked_by_liquidity} color="#fa8c16" />
      )}
    </Panel>
  )
}

export function ActiveTradesSection({ trades }) {
  if (!trades || trades.length === 0) {
    return (
      <div className="active-trades-section">
        <div className="section-title">Açık İşlemler</div>
        <div className="info-empty">Şu anda açık işlem yok.</div>
      </div>
    )
  }

  return (
    <div className="active-trades-section">
      <div className="section-title">Açık İşlemler ({trades.length})</div>
      <div className="active-trades-grid">
        {trades.map(t => {
          const isLong = t.signal === 'ACTIVE_LONG' || t.side === 'long'
          const sideColor = isLong ? '#00ff9c' : '#ff4d4f'
          const sideLabel = isLong ? 'LONG' : 'SHORT'
          const pnlPct = t.pnl_pct ?? t.unrealized_pnl_pct
          const pnlUsdt = t.unrealized_pnl

          return (
            <div key={t.symbol} className="active-trade-card" style={{ borderColor: `${sideColor}33` }}>
              <div className="at-header">
                <span className="at-symbol">{t.symbol?.replace('/USDT', '')}/USDT</span>
                <span className="at-side" style={{ color: sideColor, background: `${sideColor}15` }}>{sideLabel}</span>
              </div>
              <div className="at-body">
                <div className="at-row"><span>Strateji</span><span>{t.strategy || '—'}</span></div>
                <div className="at-row"><span>Giriş</span><span>{t.entry_price != null ? fmt(t.entry_price) : '—'}</span></div>
                <div className="at-row"><span>Güncel</span><span>{t.last_price != null ? fmt(t.last_price) : t.current_price != null ? fmt(t.current_price) : '—'}</span></div>
                <div className="at-row"><span>TP</span><span>{t.tp != null ? fmt(t.tp) : '—'}</span></div>
                <div className="at-row"><span>SL</span><span>{t.sl != null ? fmt(t.sl) : '—'}</span></div>
                <div className="at-row"><span>Büyüklük</span><span>{t.size_usdt != null ? `${fmt(t.size_usdt)} USDT` : '—'}</span></div>
              </div>
              <div className="at-pnl">
                <span>Anlık K/Z</span>
                <span style={{ color: (pnlUsdt || 0) >= 0 ? '#00ff9c' : '#ff4d4f' }}>
                  {pnlUsdt != null ? `${pnlUsdt >= 0 ? '+' : ''}${fmt(pnlUsdt)} USDT` : '—'}
                  {pnlPct != null ? ` (${fmtPct(pnlPct)})` : ''}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function TradeHistoryPanel({ data }) {
  const bots = data.bots || {}
  const recentTrades = []

  for (const [botName, botData] of Object.entries(bots)) {
    if (botData.trade_log && Array.isArray(botData.trade_log)) {
      for (const trade of botData.trade_log) {
        recentTrades.push({ ...trade, bot: botName })
      }
    }
  }

  recentTrades.sort((a, b) => (b.time || '').localeCompare(a.time || ''))
  const closed = recentTrades.filter(t => t.action === 'CLOSE' || t.action === 'PARTIAL_CLOSE').slice(0, 5)

  if (closed.length === 0) {
    return (
      <Panel title="Son İşlemler" icon="📋">
        <div className="info-empty">Henüz gösterilecek son işlem verisi yok.</div>
      </Panel>
    )
  }

  return (
    <Panel title="Son İşlemler" icon="📋">
      {closed.map((t, i) => (
        <div key={i} className="trade-entry">
          <div className="trade-header">
            <span className="trade-symbol">{t.symbol?.replace('/USDT', '')}</span>
            <span className="trade-time">
              {t.time ? new Date(t.time).toLocaleString('tr-TR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'}
            </span>
          </div>
          <div className="trade-details">
            <span className="trade-bot">{t.bot}</span>
            <span className="trade-side" style={{ color: t.side === 'long' ? '#00ff9c' : '#ff4d4f' }}>
              {t.side === 'long' ? 'Long' : 'Short'}
            </span>
            {t.reason && <span className="trade-reason">{t.reason}</span>}
          </div>
          <div className="trade-pnl-row">
            <span className="trade-pnl" style={{ color: (t.pnl || 0) >= 0 ? '#00ff9c' : '#ff4d4f' }}>
              {t.pnl != null ? `${t.pnl >= 0 ? '+' : ''}${fmt(t.pnl)} USDT` : '—'}
            </span>
            {t.pnl_pct != null && (
              <span className="trade-pnl-pct" style={{ color: t.pnl_pct >= 0 ? '#00ff9c' : '#ff4d4f' }}>
                {fmtPct(t.pnl_pct)}
              </span>
            )}
          </div>
        </div>
      ))}
    </Panel>
  )
}
