/** Sticky top bar for ChatView, showing the derived chat title. */

/**
 * Input: title. Output: the header bar.
 *
 * This used to be a <button> with a chevron and no onClick — focusable,
 * announced as a control, and doing nothing when activated. If a title
 * dropdown ever exists, it comes back as a real one.
 */
export function ChatHeader({ title }: { title: string }) {
  return (
    <header className="z-10 flex shrink-0 items-center gap-1 border-b border-stone-200/80 px-4 py-3.5 sm:px-6 dark:border-stone-800">
      <h2 className="max-w-[60vw] truncate px-2 py-1 text-[15px] font-medium tracking-tight text-stone-800 sm:max-w-md dark:text-stone-100">
        {title}
      </h2>
    </header>
  )
}
