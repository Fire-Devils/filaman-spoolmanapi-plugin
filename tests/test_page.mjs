import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import vm from 'node:vm'

const page = readFileSync(new URL('../spoolmanapi/page.html', import.meta.url), 'utf8')
const scripts = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1])

function runPage(language, fetch) {
  const elements = new Map()
  function element() {
    return {
      disabled: false, checked: false, value: '', dataset: {}, style: {}, textContent: '',
      classList: { add() {}, remove() {} },
      addEventListener(name, handler) { this[name] = handler },
      querySelectorAll() { return [] },
      children: [], appendChild(child) { this.children.push(child) }, setAttribute() {},
    }
  }
  const heading = { dataset: { i18n: 'ip.title' }, textContent: '' }
  const document = {
    cookie: '', documentElement: { lang: '', setAttribute() {} },
    getElementById(id) {
      if (!elements.has(id)) {
        const el = element()
        el.disabled = new RegExp(`id="${id}"[^>]*disabled`).test(page)
        elements.set(id, el)
      }
      return elements.get(id)
    },
    querySelectorAll(selector) { return selector === '[data-i18n]' ? [heading] : [] }, createElement: element,
  }
  const context = {
    document, fetch, localStorage: { getItem: key => key === 'lang' ? language : null },
    navigator: { language: 'en', clipboard: { writeText: async () => {} } },
    window: { location: { origin: 'https://example.test' } },
    setTimeout() {},
  }
  for (const script of scripts) vm.runInNewContext(script, context)
  return { context, document, elements, heading }
}

test('all visible and scripted strings exist in all three languages', () => {
  const { context } = runPage('en', async () => { throw new Error('offline') })
  const keys = [
    ...page.matchAll(/data-i18n(?:-html|-title|-placeholder)?="([^"]+)"/g),
    ...scripts.join('\n').matchAll(/\bt\('([^']+)'\)/g),
  ].map(match => match[1])
  for (const language of ['en', 'de', 'fr']) {
    for (const key of keys) assert.ok(context.translations[language][key], `${language}.${key}`)
  }
  const french = runPage('fr', async () => { throw new Error('offline') })
  assert.equal(french.document.documentElement.lang, 'fr')
  assert.equal(french.heading.textContent, "Contrôle d'accès IP")
  assert.equal(runPage('unknown', async () => { throw new Error('offline') }).document.documentElement.lang, 'en')
})

test('save remains disabled if settings cannot load', async () => {
  const { elements } = runPage('en', async () => { throw new Error('offline') })
  assert.equal(elements.get('btn-save').disabled, true)
  await new Promise(setImmediate)
  assert.equal(elements.get('btn-save').disabled, true)
  assert.equal(elements.get('settings-controls').disabled, true)
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

test('all settings controls stay frozen until initial settings load', async () => {
  let finishLoad
  const { elements } = runPage('en', () => new Promise(resolve => { finishLoad = resolve }))
  assert.equal(elements.get('settings-controls')?.disabled, true)
  finishLoad({ ok: true, json: async () => ({ ip_filter_enabled: false, allowed_ips: [] }) })
  await new Promise(setImmediate)
  assert.equal(elements.get('settings-controls').disabled, false)
})

for (const outcome of ['success', 'validation error', 'network error']) {
  test(`all settings controls stay frozen during save and recover after ${outcome}`, async () => {
    let finishSave
    const { elements } = runPage('en', async (_url, options) => {
      if (options?.method !== 'PUT') {
        return { ok: true, json: async () => ({ ip_filter_enabled: true, allowed_ips: ['192.0.2.1'] }) }
      }
      return new Promise((resolve, reject) => {
        finishSave = () => outcome === 'network error' ? reject(new Error('offline')) : resolve({
          ok: outcome === 'success', status: outcome === 'success' ? 200 : 422,
          json: async () => ({ ip_filter_enabled: true, allowed_ips: ['192.0.2.1'] }),
        })
      })
    })
    await new Promise(setImmediate)
    const saving = elements.get('btn-save').click()
    assert.equal(elements.get('settings-controls')?.disabled, true)
    finishSave()
    await saving
    assert.equal(elements.get('settings-controls').disabled, false)
    assert.equal(elements.get('btn-save').disabled, false)
    assert.equal(elements.get('toggle-ip-filter').checked, true)
  })
}
