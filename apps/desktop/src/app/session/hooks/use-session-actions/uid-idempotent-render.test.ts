import { describe, expect, it } from 'vitest'

import { graftRefreshedTailOntoBackfill } from '@/app/chat/transcript-backfill'
import { type ChatMessage, chatMessageText } from '@/lib/chat-messages'

import { preserveLocalPendingTurnMessages } from './utils'

const msg = (id: string, role: ChatMessage['role'], text: string, extra: Partial<ChatMessage> = {}): ChatMessage =>
  ({ id, role, parts: [{ type: 'text', text }], ...extra }) as ChatMessage

// The 2026-10-22 duplicate-bubble shape, replayed at the reconcile layer: a
// foreign-bridge (patch 0010) turn streamed live into a session the desktop had
// open — its tool-round narration sealed as its own interim bubble, the reply
// streamed into the live stream bubble, and the terminal frame carried a
// persisted_turn receipt (address-only: a foreign process cannot prove the
// pre-turn prefix). When the canonical DB page lands, hydration FOLDS the
// narration row and the reply row into one assistant bubble, so no text guard
// can pair the stream bubble (reply only) with the fold (narration + reply):
// not identical, and neither extends the other. Without a durable key the
// stream bubble is re-appended and the reply paints twice. Its message_uid —
// stamped from the receipt — is that key.
const UID = 'd1e2f3a4b5c6478890a1b2c3d4e5f607'

const previousWindow = (): ChatMessage[] => [
  msg('older-user', 'user', 'earlier question', { rowId: 100 }),
  msg('older-assistant', 'assistant', 'earlier answer', { rowId: 101 }),
  msg('user-9-x', 'user', 'did the background wake land?'),
  msg('assistant-interim-9-1', 'assistant', 'Checking the logs first.', { interim: true, pending: false }),
  msg('assistant-stream-9-0', 'assistant', 'All clear, the wake landed clean.', {
    pending: false,
    persistedTurn: { row_ids: [44827, 44828], complete: false, final_assistant_row_id: 44828, message_uid: UID }
  })
]

/** The canonical hydrated page: the turn's rows folded into one bubble. */
const canonicalPage = (): ChatMessage[] => [
  msg('s-older-user', 'user', 'earlier question', { rowId: 100 }),
  msg('s-older-assistant', 'assistant', 'earlier answer', { rowId: 101 }),
  msg('s-user', 'user', 'did the background wake land?', { rowId: 44827 }),
  msg('s-assistant', 'assistant', 'Checking the logs first.\n\nAll clear, the wake landed clean.', {
    rowId: 44828,
    messageUid: UID
  })
]

describe('idempotent render by message_uid (foreign-bridge duplicate bubble)', () => {
  it('a uid-receipted streamed bubble is absorbed by the canonical folded row', () => {
    const merged = preserveLocalPendingTurnMessages(canonicalPage(), previousWindow())

    // One reply on screen: the canonical fold. The stream bubble and the
    // sealed interim are both represented by it — no second "All clear".
    const replies = merged.filter(message => message.role === 'assistant' && !message.hidden)
    expect(replies.map(reply => chatMessageText(reply).trim())).toEqual([
      'earlier answer',
      'Checking the logs first.\n\nAll clear, the wake landed clean.'
    ])
    expect(replies.at(-1)?.rowId).toBe(44828)
    expect(replies.at(-1)?.messageUid).toBe(UID)
  })

  it('the graft leg keeps one canonical copy when the window overlaps the page', () => {
    const merged = graftRefreshedTailOntoBackfill(
      canonicalPage().slice(2),
      previousWindow().filter(message => message.id !== 'older-assistant')
    )

    expect(merged.filter(m => chatMessageText(m).includes('All clear'))).toHaveLength(1)
  })

  it('distinct replies with distinct uids never absorb each other — failure control', () => {
    // A deliberately-duplicated event: the page carries a DIFFERENT reply under
    // a DIFFERENT uid while the streamed bubble is still uid-keyed to its own
    // receipt. Identity, not prose, must decide — and here it must decide these
    // are two replies, keeping both.
    const next = [...canonicalPage(), msg('s-later', 'assistant', 'A different reply entirely.', {
      rowId: 44900,
      messageUid: '0f1e2d3c4b5a69788796a5b4c3d2e1f0'
    })]

    const merged = preserveLocalPendingTurnMessages(next, previousWindow())

    const texts = merged
      .filter(message => message.role === 'assistant' && !message.hidden)
      .map(message => chatMessageText(message).trim())

    expect(texts).toEqual([
      'earlier answer',
      'Checking the logs first.\n\nAll clear, the wake landed clean.',
      'A different reply entirely.'
    ])
  })

  it('a uid-less streamed bubble keeps the legacy guards (no regression on old backends)', () => {
    // Older backends emit no receipt: the settled bubble carries no uid and the
    // pre-existing text guards alone decide. This exact shape (interim-sealed
    // narration vs folded canonical text) leaks through them — that IS the
    // 2026-10-22 bug on backends without the receipt — but the uid path must
    // not make it worse: the canonical row stays exactly once either way.
    const legacyPrevious = [
      ...previousWindow().slice(0, 4),
      msg('assistant-stream-9-0', 'assistant', 'All clear, the wake landed clean.', { pending: false })
    ]

    const merged = preserveLocalPendingTurnMessages(canonicalPage(), legacyPrevious)

    expect(merged.filter(m => chatMessageText(m).includes('All clear, the wake landed clean.')).length).toBeGreaterThanOrEqual(1)
    expect(merged.filter(m => m.rowId === 44828)).toHaveLength(1)
  })
})
