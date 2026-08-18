import type { ChatMessage } from '../types.ts'

// Only used for local visual QA via `?demo=1` — never triggered in normal use.
export const DEMO_MESSAGES: ChatMessage[] = [
  {
    id: 'demo-u1',
    role: 'user',
    text: 'Draw a flowchart for how we onboard a new customer, and a chart of signups by month.',
  },
  {
    id: 'demo-a1',
    role: 'assistant',
    text: "Here's the onboarding flow and this year's signups.",
    diagrams: [
      {
        title: 'Customer onboarding',
        mermaid: [
          'flowchart TD',
          '    start["Sign up"]:::start',
          '    verify["Verify email"]:::process',
          '    plan{"Choose plan?"}:::decision',
          '    paid[/"Collect payment"/]:::io',
          '    setup["Set up workspace"]:::process',
          '    done["Onboarded"]:::endNode',
          '    start --> verify',
          '    verify --> plan',
          '    plan -->|Paid| paid',
          '    plan -->|Free| setup',
          '    paid --> setup',
          '    setup --> done',
        ].join('\n'),
      },
    ],
    charts: [
      {
        title: 'Signups by month',
        option: {
          tooltip: { trigger: 'axis' },
          xAxis: { type: 'category', data: ['Jan', 'Feb', 'Mar', 'Apr', 'May'] },
          yAxis: { type: 'value' },
          series: [{ type: 'bar', name: 'signups', data: [120, 180, 150, 240, 300] }],
        },
      },
    ],
  },
]
