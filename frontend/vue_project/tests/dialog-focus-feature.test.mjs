import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getDialogFocusableElements,
  restoreDialogFocus,
  trapDialogTab,
} from '../src/utils/dialogFocus.js'

function focusTarget(name, attributes = {}) {
  return {
    name,
    focusCalls: 0,
    getAttribute(key) {
      return attributes[key] ?? null
    },
    focus() {
      this.focusCalls += 1
    },
  }
}

function keyEvent({ shiftKey = false } = {}) {
  return {
    key: 'Tab',
    shiftKey,
    prevented: false,
    preventDefault() {
      this.prevented = true
    },
  }
}

test('dialog focus list excludes aria-disabled controls', () => {
  const enabled = focusTarget('enabled')
  const disabled = focusTarget('disabled', { 'aria-disabled': 'true' })
  const container = { querySelectorAll: () => [enabled, disabled] }

  assert.deepEqual(getDialogFocusableElements(container), [enabled])
})

test('dialog tab trap wraps both edges and recovers focus that escaped', () => {
  const first = focusTarget('first')
  const middle = focusTarget('middle')
  const last = focusTarget('last')
  const members = new Set([first, middle, last])
  const container = {
    querySelectorAll: () => [first, middle, last],
    contains: (element) => members.has(element),
  }

  const forward = keyEvent()
  assert.equal(trapDialogTab(forward, container, last), true)
  assert.equal(forward.prevented, true)
  assert.equal(first.focusCalls, 1)

  const backward = keyEvent({ shiftKey: true })
  assert.equal(trapDialogTab(backward, container, first), true)
  assert.equal(last.focusCalls, 1)

  const escaped = keyEvent()
  assert.equal(trapDialogTab(escaped, container, focusTarget('outside')), true)
  assert.equal(first.focusCalls, 2)

  const ordinary = keyEvent()
  assert.equal(trapDialogTab(ordinary, container, middle), false)
  assert.equal(ordinary.prevented, false)
})

test('dialog focus restoration ignores detached targets', () => {
  const opener = focusTarget('opener')
  opener.isConnected = true
  assert.equal(restoreDialogFocus(opener), true)
  assert.equal(opener.focusCalls, 1)

  opener.isConnected = false
  assert.equal(restoreDialogFocus(opener), false)
  assert.equal(opener.focusCalls, 1)
})
