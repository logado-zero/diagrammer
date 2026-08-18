/** Renders assistant reply text as GFM markdown (bold/headings/lists/tables/code), styled to match the app's dark theme. */
import { memo } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Module constants, not inline literals: this renders on every streamed token,
// and a fresh `components` object / `remarkPlugins` array each time makes
// react-markdown rebuild its whole renderer per token instead of just
// reparsing the text that actually changed.
const PLUGINS = [remarkGfm]

const COMPONENTS: Components = {
  p: ({ children }) => <p className="whitespace-pre-wrap">{children}</p>,
  h1: ({ children }) => <h1 className="pt-1 text-xl font-semibold text-stone-900 dark:text-white">{children}</h1>,
  h2: ({ children }) => <h2 className="pt-1 text-lg font-semibold text-stone-900 dark:text-white">{children}</h2>,
  h3: ({ children }) => <h3 className="pt-1 text-base font-semibold text-stone-900 dark:text-white">{children}</h3>,
  strong: ({ children }) => <strong className="font-semibold text-stone-900 dark:text-white">{children}</strong>,
  ul: ({ children }) => <ul className="list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="pl-1">{children}</li>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-orange-600 underline dark:text-orange-400">
      {children}
    </a>
  ),
  hr: () => <hr className="border-stone-200 dark:border-stone-700" />,
  code: ({ className, children }) => {
    const isBlock = /language-/.test(className ?? '')
    if (isBlock) {
      return <code className={className}>{children}</code>
    }
    return (
      <code className="rounded-md bg-orange-100 px-1.5 py-0.5 font-mono text-[13px] text-orange-800 dark:bg-orange-500/15 dark:text-orange-300">
        {children}
      </code>
    )
  },
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-xl border border-stone-200 bg-stone-50 p-3 font-mono text-[13px] leading-relaxed text-stone-800 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto rounded-xl border border-stone-200 dark:border-stone-700">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-orange-50 dark:bg-stone-800">{children}</thead>,
  th: ({ children }) => (
    <th className="border-b border-stone-200 px-3 py-2 font-semibold text-stone-900 dark:border-stone-700 dark:text-stone-50">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-stone-100 px-3 py-2 text-stone-700 last:border-b-0 dark:border-stone-800 dark:text-stone-200">
      {children}
    </td>
  ),
  tr: ({ children }) => (
    <tr className="odd:bg-transparent even:bg-stone-50 dark:even:bg-stone-800/40">{children}</tr>
  ),
}

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="max-w-none space-y-3 text-[15px] leading-relaxed text-stone-800 dark:text-stone-100">
      <ReactMarkdown remarkPlugins={PLUGINS} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  )
})
