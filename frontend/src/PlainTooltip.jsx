import { useState } from 'react'
import { Info } from 'lucide-react'

export function PlainTooltip({ text, children }) {
  const [open, setOpen] = useState(false)
  return (
    <span className={`plain-tooltip ${open ? 'is-open' : ''}`}>
      <span className="plain-tooltip-content">{children}</span>
      <button
        type="button"
        aria-label="What does this mean?"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Info size={13} />
      </button>
      <span className="plain-tooltip-popover" role="tooltip">{text}</span>
    </span>
  )
}

