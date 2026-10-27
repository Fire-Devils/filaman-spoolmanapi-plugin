import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'

const page = readFileSync(new URL('../spoolmanapi/page.html', import.meta.url), 'utf8')
const script = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1]

function runPage(language, fetch) {
  const elements = new Map()
  function element() {
    return {
      disabled: true, checked: false, value: '', dataset: {}, style: {}, textContent: '',
      classList: { add() {}, remove() {} },
      addEventListener(name, handler) { this[name] = handler },
      querySelectorAll() { return [] },
      children: [], appendChild(child) { this.children.push(child) }, setAttribute() {},
    }
  }
  const heading = { dataset: { i18n: 'accessTitle' }, textContent: '' }
  const document = {
    cookie: '', documentElement: { lang: '' },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element())
      return elements.get(id)
    },
    querySelectorAll(selector) { return selector === '[data-i18n]' ? [heading] : [] }, createElement: element,
  }
  const context = {
    document, fetch, localStorage: { getItem: () => language },
    navigator: { clipboard: { writeText: async () => {} } },
    window: { location: { origin: 'https://example.test' } },
    setTimeout() {},
  }
  vm.runInNewContext(script, context)
  return { context, document, elements, heading }
}

test('all visible and scripted strings exist in all three languages', () => {
  const { context } = runPage('en', async () => { throw new Error('offline') })
  const keys = [
    ...page.matchAll(/data-i18n(?:-title|-placeholder)?="([^"]+)"/g),
    ...script.matchAll(/tr\('([^']+)'\)/g),
  ].map(match => match[1])
  for (const language of ['en', 'de', 'fr']) {
    for (const key of keys) assert.ok(context.translations[language][key], `${language}.${key}`)
  }
  const french = runPage('fr', async () => { throw new Error('offline') })
  assert.equal(french.document.documentElement.lang, 'fr')
  assert.equal(french.heading.textContent, 'Contrôle d’accès par IP')
  assert.equal(runPage('unknown', async () => { throw new Error('offline') }).document.documentElement.lang, 'en')
})

test('save remains disabled if settings cannot load', async () => {
  assert.match(page, /id="btn-save"[^>]*disabled/)
  const { elements } = runPage('en', async () => { throw new Error('offline') })
  await new Promise(setImmediate)
  assert.equal(elements.get('btn-save').disabled, true)
})

test('save becomes available after settings load', async () => {
  const { elements } = runPage('en', async () => ({
    ok: true, json: async () => ({ ip_filter_enabled: true, allowed_ips: ['192.0.2.1'] }),
  }))
  await new Promise(setImmediate)
  assert.equal(elements.get('toggle-ip-filter').checked, true)
  assert.equal(elements.get('btn-save').disabled, false)
})

test('invalid IP feedback uses the selected language', async () => {
  const { elements } = runPage('fr', async (_url, options) => options?.method === 'PUT'
    ? { ok: false, status: 422, json: async () => ({ detail: [] }) }
    : { ok: true, json: async () => ({ ip_filter_enabled: false, allowed_ips: [] }) })
  await new Promise(setImmediate)
  await elements.get('btn-save').click()
  assert.match(elements.get('toast-container').children.at(-1).textContent, /Saisissez une adresse IP/)
})
