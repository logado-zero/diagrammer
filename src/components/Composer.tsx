/**
 * Shared message input bar used by both WelcomeScreen and ChatView:
 * textarea, an attach popover (Image / Text / Sheet), the draw-mode
 * picker, the model picker, and a send button — all three popovers are
 * button+popover controls using the same useDismissablePopover interaction.
 */
import { useRef, useState, type ChangeEvent, type ComponentType, type KeyboardEvent } from 'react'
import {
  ArrowUp,
  BarChart3,
  Check,
  ChevronDown,
  FileSpreadsheet,
  FileText,
  ImagePlus,
  Plus,
  Wand2,
  Workflow,
  X,
} from 'lucide-react'
import type { AttachmentKind, DrawMode, ModelOption, PendingAttachment } from '../types.ts'
import { useDismissablePopover } from '../lib/useDismissablePopover.ts'

/**
 * Everything the composer needs that a parent screen passes straight
 * through. WelcomeScreen and ChatView each used to re-declare these ten
 * fields, destructure them, and forward them one by one — so adding a
 * composer prop was a five-file change.
 */
export interface ComposerControls {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  attachment?: PendingAttachment | null
  onAttachFile: (file: File, kind: AttachmentKind) => void
  onRemoveAttachment: () => void
  models: ModelOption[]
  modelId: string
  onModelChange: (id: string) => void
  mode: DrawMode
  onModeChange: (mode: DrawMode) => void
}

interface ComposerProps extends ComposerControls {
  /** Set per screen, not forwarded from App. */
  placeholder: string
  disabled?: boolean
}

// The panel shape all three composer menus share (attach, draw mode, model).
// Each supplies its own width and anchor edge; everything else is identical,
// and was written out three times.
const MENU_PANEL =
  'absolute bottom-full z-20 mb-2 overflow-hidden rounded-2xl border border-stone-200 bg-white py-1.5 shadow-lg shadow-black/10 dark:border-stone-700 dark:bg-stone-800 dark:shadow-black/40'

const MODE_OPTIONS: { id: DrawMode; label: string; icon: ComponentType<{ size?: number }>; description: string }[] = [
  { id: 'auto', label: 'Auto', icon: Wand2, description: 'Let the model decide what to draw' },
  { id: 'diagram', label: 'Flowchart', icon: Workflow, description: 'Always draw a flowchart' },
  { id: 'chart', label: 'Data Chart', icon: BarChart3, description: 'Always draw a chart' },
]

const ATTACH_OPTIONS: {
  kind: AttachmentKind
  label: string
  icon: ComponentType<{ size?: number }>
  accept: string
}[] = [
  { kind: 'image', label: 'Image', icon: ImagePlus, accept: 'image/*' },
  { kind: 'text', label: 'Text', icon: FileText, accept: '.txt,text/plain' },
  { kind: 'sheet', label: 'Sheet', icon: FileSpreadsheet, accept: '.xlsx,.csv' },
]

/** Input: draft value/attachment + model/mode selection + handlers (ComposerProps). Output: the input bar; Enter submits, Shift+Enter inserts a newline. */
export function Composer({
  value,
  onChange,
  onSubmit,
  placeholder,
  disabled,
  attachment,
  onAttachFile,
  onRemoveAttachment,
  models,
  modelId,
  onModelChange,
  mode,
  onModeChange,
}: ComposerProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const pickedKindRef = useRef<AttachmentKind>('image')
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const modelMenuRef = useDismissablePopover<HTMLDivElement>(modelMenuOpen, () => setModelMenuOpen(false))
  const [modeMenuOpen, setModeMenuOpen] = useState(false)
  const modeMenuRef = useDismissablePopover<HTMLDivElement>(modeMenuOpen, () => setModeMenuOpen(false))
  const [attachMenuOpen, setAttachMenuOpen] = useState(false)
  const attachMenuRef = useDismissablePopover<HTMLDivElement>(attachMenuOpen, () => setAttachMenuOpen(false))

  const canSubmit = !disabled && (value.trim().length > 0 || !!attachment)

  /** Submits on Enter, unless Shift is held (newline) or there's nothing to send. */
  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (canSubmit) onSubmit()
    }
  }

  /** Opens the hidden file input configured for the picked attach kind. */
  function openFilePicker(kind: AttachmentKind, accept: string) {
    pickedKindRef.current = kind
    if (fileInputRef.current) fileInputRef.current.accept = accept
    fileInputRef.current?.click()
    setAttachMenuOpen(false)
  }

  /** Reads the picked file from the hidden file input and clears its value so the same file can be re-picked later. */
  function handleFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) onAttachFile(file, pickedKindRef.current)
    e.target.value = ''
  }

  const selectedModel = models.find((m) => m.id === modelId)
  const selectedMode = MODE_OPTIONS.find((m) => m.id === mode) ?? MODE_OPTIONS[0]

  return (
    <div className="relative rounded-[28px] border border-stone-200/70 bg-stone-100 dark:border-stone-700 dark:bg-stone-800">
      {attachment && (
        <div className="flex items-center gap-2 border-b border-stone-200/70 px-4 pb-3 pt-3 dark:border-stone-700">
          <div className="relative">
            {attachment.kind === 'image' && attachment.previewUrl ? (
              <img src={attachment.previewUrl} alt="Attached" className="h-14 w-14 rounded-lg object-cover" />
            ) : (
              <div className="flex h-14 items-center gap-2 rounded-lg border border-stone-200 bg-white px-3 text-stone-600 dark:border-stone-600 dark:bg-stone-700 dark:text-stone-300">
                {attachment.kind === 'sheet' ? <FileSpreadsheet size={18} /> : <FileText size={18} />}
                <span className="max-w-[10rem] truncate text-sm">{attachment.name}</span>
              </div>
            )}
            <button
              type="button"
              onClick={onRemoveAttachment}
              aria-label="Remove attachment"
              className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-stone-700 text-white hover:bg-stone-900"
            >
              <X size={12} />
            </button>
          </div>
        </div>
      )}

      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        rows={1}
        className="block max-h-48 w-full resize-none bg-transparent px-5 pr-12 pt-4 text-[15px] leading-6 text-stone-800 placeholder:text-stone-400 focus:outline-none disabled:opacity-60 dark:text-stone-100 dark:placeholder:text-stone-500"
      />

      <div className="flex items-center justify-between px-3 pb-3 pt-2">
        <div className="relative" ref={attachMenuRef}>
          <button
            type="button"
            onClick={() => setAttachMenuOpen((open) => !open)}
            aria-haspopup="listbox"
            aria-expanded={attachMenuOpen}
            aria-label="Attach file"
            className="flex h-9 w-9 items-center justify-center rounded-full text-stone-500 transition hover:bg-stone-200 dark:text-stone-400 dark:hover:bg-stone-700"
          >
            <Plus size={20} />
          </button>

          {attachMenuOpen && (
            <ul
              role="listbox"
              aria-label="Attach file"
              className={`${MENU_PANEL} left-0 w-44`}
            >
              {ATTACH_OPTIONS.map((opt) => (
                <li key={opt.kind} role="option">
                  <button
                    type="button"
                    onClick={() => openFilePicker(opt.kind, opt.accept)}
                    className="flex w-full items-center gap-2.5 px-3.5 py-2 text-left text-sm font-medium text-stone-900 transition hover:bg-stone-100 dark:text-stone-50 dark:hover:bg-stone-700"
                  >
                    <opt.icon size={15} />
                    {opt.label}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileChange} />

        <div className="flex items-center gap-1 text-stone-500 dark:text-stone-400">
          <div className="relative" ref={modeMenuRef}>
            <button
              type="button"
              onClick={() => setModeMenuOpen((open) => !open)}
              aria-haspopup="listbox"
              aria-expanded={modeMenuOpen}
              aria-label="Draw mode"
              title={selectedMode.description}
              className="flex items-center gap-1 rounded-full px-2.5 py-1.5 text-sm font-semibold text-stone-700 transition hover:bg-stone-200 dark:text-stone-200 dark:hover:bg-stone-700"
            >
              <selectedMode.icon size={14} />
              <span>{selectedMode.label}</span>
              <ChevronDown size={14} />
            </button>

            {modeMenuOpen && (
              <ul
                role="listbox"
                aria-label="Draw mode"
                className={`${MENU_PANEL} left-0 w-56`}
              >
                {MODE_OPTIONS.map((m) => {
                  const selected = m.id === mode
                  return (
                    <li key={m.id} role="option" aria-selected={selected}>
                      <button
                        type="button"
                        onClick={() => {
                          onModeChange(m.id)
                          setModeMenuOpen(false)
                        }}
                        className="flex w-full items-center justify-between gap-3 px-3.5 py-2 text-left transition hover:bg-stone-100 dark:hover:bg-stone-700"
                      >
                        <span className="flex items-center gap-2">
                          <m.icon size={15} />
                          <span>
                            <span className="block text-sm font-medium text-stone-900 dark:text-stone-50">
                              {m.label}
                            </span>
                            <span className="mt-0.5 block text-xs text-stone-500 dark:text-stone-400">
                              {m.description}
                            </span>
                          </span>
                        </span>
                        {selected && <Check size={16} className="mt-0.5 shrink-0 text-teal-500 dark:text-teal-400" />}
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>

          <div className="relative" ref={modelMenuRef}>
            <button
              type="button"
              onClick={() => setModelMenuOpen((open) => !open)}
              aria-haspopup="listbox"
              aria-expanded={modelMenuOpen}
              aria-label="Model"
              title={selectedModel?.description}
              className="flex items-center gap-1 rounded-full px-2.5 py-1.5 text-sm font-semibold text-stone-700 transition hover:bg-stone-200 dark:text-stone-200 dark:hover:bg-stone-700"
            >
              <span>{selectedModel?.shortLabel ?? 'Model'}</span>
              <ChevronDown size={14} />
            </button>

            {modelMenuOpen && (
              <ul
                role="listbox"
                aria-label="Model"
                className={`${MENU_PANEL} right-0 w-64`}
              >
                {models.map((m) => {
                  const selected = m.id === modelId
                  return (
                    <li key={m.id} role="option" aria-selected={selected}>
                      <button
                        type="button"
                        disabled={!m.available}
                        onClick={() => {
                          onModelChange(m.id)
                          setModelMenuOpen(false)
                        }}
                        className={`flex w-full items-start justify-between gap-3 px-3.5 py-2 text-left transition ${
                          m.available
                            ? 'hover:bg-stone-100 dark:hover:bg-stone-700'
                            : 'cursor-not-allowed opacity-40'
                        }`}
                      >
                        <span>
                          <span className="block text-sm font-medium text-stone-900 dark:text-stone-50">
                            {m.label}
                          </span>
                          {(m.description || !m.available) && (
                            <span className="mt-0.5 block text-xs text-stone-500 dark:text-stone-400">
                              {m.available ? m.description : 'Unavailable'}
                            </span>
                          )}
                        </span>
                        {selected && (
                          <Check size={16} className="mt-0.5 shrink-0 text-teal-500 dark:text-teal-400" />
                        )}
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>


          <button
            type="button"
            onClick={onSubmit}
            disabled={!canSubmit}
            aria-label="Send message"
            title="Send message"
            className={`flex h-9 w-9 items-center justify-center rounded-full transition ${
              canSubmit
                ? 'bg-stone-800 text-white hover:bg-stone-900 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-white'
                : 'cursor-not-allowed bg-stone-200 text-stone-400 dark:bg-stone-700 dark:text-stone-500'
            }`}
          >
            <ArrowUp size={18} />
          </button>
        </div>
      </div>
    </div>
  )
}
