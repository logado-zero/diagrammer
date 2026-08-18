/** Sticky top bar for ChatView, showing the derived chat title. */
import { ChevronDown } from 'lucide-react'

/** Input: title. Output: the header bar (the chevron is currently decorative — no dropdown behind it yet). */
export function ChatHeader({ title }: { title: string }) {
  return (
    <header className="z-10 flex shrink-0 items-center gap-1 border-b border-stone-200/80 px-4 py-3.5 sm:px-6 dark:border-stone-800">
      <button
        type="button"
        className="-ml-2 flex items-center gap-1.5 rounded-lg px-2 py-1 text-[15px] font-medium tracking-tight text-stone-800 transition hover:bg-stone-100 dark:text-stone-100 dark:hover:bg-stone-800"
      >
        <span className="max-w-[60vw] truncate sm:max-w-md">{title}</span>
        <ChevronDown size={14} className="shrink-0 text-stone-400" />
      </button>
    </header>
  )
}
