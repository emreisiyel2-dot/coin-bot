import { useMemo } from 'react'

export default function Sparkline({ data, width = 120, height = 40, color = '#00ff9c' }) {
  const path = useMemo(() => {
    if (!data || data.length < 2) return ''
    const min = Math.min(...data)
    const max = Math.max(...data)
    const range = max - min || 1
    const step = width / (data.length - 1)

    return data
      .map((v, i) => {
        const x = i * step
        const y = height - ((v - min) / range) * (height - 4) - 2
        return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
      })
      .join(' ')
  }, [data, width, height])

  if (!data || data.length < 2) return null

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
