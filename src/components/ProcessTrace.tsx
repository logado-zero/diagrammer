/**
 * Collapsible "Process Trace" box: shows real agent-step labels as they stream
 * in — see server/agent.py's trace events around the subagent pass, plus each
 * provider's "Searching the web…"/"Searching memory…" status lines.
 *
 * Status labels only. What a memory search actually returned is a `retrieval`
 * event that server/main.py files into agent_logs and never relays, so it
 * cannot reach this box. MessageBubble.tsx keeps the box mounted after the turn
 * ends; the labels are persisted with the message, so they survive a reload.
 */
import { useState } from 'react'
import { Check, ChevronDown, CircleDot, Loader2 } from 'lucide-react'

/**
 * Input: the trace labels collected so far, and whether the turn has finished.
 * Output: an expandable step list — spinning and open while the turn runs,
 * checked and collapsed once it ends.
 */
export function ProcessTrace({ steps, done }: { steps: string[]; done?: boolean }) {
  // null means "follow `done`". A plain useState(!done) initializer would latch
  // the value from the first render and never collapse when the turn ends; this
  // way an explicit click still wins from then on.
  const [override, setOverride] = useState<boolean | null>(null)
  const open = override ?? !done

  return (
    <div className="mb-3 w-full max-w-md overflow-hidden rounded-xl border border-stone-200 bg-stone-50 dark:border-stone-700 dark:bg-stone-800/60">
      <button
        type="button"
        onClick={() => setOverride(!open)}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-sm font-medium text-stone-700 dark:text-stone-200"
      >
        {done ? (
          <Check size={14} className="text-teal-500 dark:text-teal-400" />
        ) : (
          <Loader2 size={14} className="animate-spin text-stone-400 dark:text-stone-500" />
        )}
        <span>Process Trace</span>
        <span className="rounded-full bg-stone-200 px-1.5 py-0.5 text-xs font-semibold text-stone-600 dark:bg-stone-700 dark:text-stone-300">
          {steps.length}
        </span>
        <ChevronDown
          size={14}
          className={`ml-auto text-stone-400 transition-transform dark:text-stone-500 ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <ul className="flex flex-col gap-1.5 border-t border-stone-200 px-3.5 py-2.5 dark:border-stone-700">
          {steps.map((label, i) => (
            <li key={i} className="flex items-center gap-2 text-xs text-stone-600 dark:text-stone-300">
              <CircleDot size={11} className="shrink-0 text-teal-500 dark:text-teal-400" />
              {/* Memory hit lines carry a title and turn number and can be long — wrap rather than clip. */}
              <span className="min-w-0 break-words">{label}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
