/**
 * Landing screen shown before any message has been sent: brand mark +
 * headline + Composer + quick-action prompt pills.
 */
import { BarChart3, GitBranch, Workflow } from 'lucide-react'
import { DonutMark } from './icons/DonutMark.tsx'
import { Composer, type ComposerAttachment } from './Composer.tsx'
import type { AttachmentKind, DrawMode, ModelOption } from '../types.ts'

const PILLS = [
  { label: 'Flowchart', icon: Workflow, prompt: 'Draw a flowchart for ' },
  { label: 'Workflow', icon: GitBranch, prompt: 'Draw a workflow diagram for ' },
  { label: 'Chart', icon: BarChart3, prompt: 'Draw a bar chart of ' },
]

interface WelcomeScreenProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  attachment?: ComposerAttachment | null
  onAttachFile: (file: File, kind: AttachmentKind) => void
  onRemoveAttachment: () => void
  onPickPrompt: (prompt: string) => void
  models: ModelOption[]
  modelId: string
  onModelChange: (id: string) => void
  mode: DrawMode
  onModeChange: (mode: DrawMode) => void
}

/** Input: composer state/handlers + onPickPrompt for the quick-action pills. Output: the centered welcome layout. */
export function WelcomeScreen({
  value,
  onChange,
  onSubmit,
  attachment,
  onAttachFile,
  onRemoveAttachment,
  onPickPrompt,
  models,
  modelId,
  onModelChange,
  mode,
  onModeChange,
}: WelcomeScreenProps) {
  return (
    <div className="flex h-full min-h-0 flex-col items-center justify-center overflow-y-auto px-4 py-10">
      <div className="w-full max-w-2xl">
        <div className="mb-8 flex items-center justify-center gap-3">
          <DonutMark size={34} />
          <h1 className="text-center font-serif text-3xl tracking-tight text-stone-800 sm:text-4xl dark:text-stone-100">
            What should we draw?
          </h1>
        </div>

        <Composer
          value={value}
          onChange={onChange}
          onSubmit={onSubmit}
          placeholder="Describe a diagram, or attach an image to recreate"
          attachment={attachment}
          onAttachFile={onAttachFile}
          onRemoveAttachment={onRemoveAttachment}
          models={models}
          modelId={modelId}
          onModelChange={onModelChange}
          mode={mode}
          onModeChange={onModeChange}
        />

        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          {PILLS.map(({ label, icon: Icon, prompt }) => (
            <button
              key={label}
              type="button"
              onClick={() => onPickPrompt(prompt)}
              className="flex items-center gap-2 rounded-full bg-stone-100 px-3.5 py-2 text-sm font-medium text-stone-600 transition hover:bg-stone-200 hover:text-stone-800 dark:bg-stone-800 dark:text-stone-300 dark:hover:bg-stone-700"
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
