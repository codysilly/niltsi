// Offline: execute the actual dashboard in a VM with storage/DOM stubs and no network.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const source = readFileSync(join(__dirname, '../app.js'), 'utf8');
const NEW = 'spatchin-airports', OLD = 'niltsi-airports';
const defaults = ['MEM'];
test('MEM is the only fallback, maps to KMEM, and does not write preferences', () => {
  const app = load();
  assert.deepEqual(app.airports(), ['MEM']);
  assert.equal(app.run('icao("MEM")'), 'KMEM');
  assert.equal(app.run('icao(airports[0])'), 'KMEM');
  assert.deepEqual(app.writes, []);
  assert.equal(app.stored.size, 0);
});
function load(values = {}, {readFailures = [], writeFails = false, unavailable = false} = {}) {
  const stored = new Map(Object.entries(values)), writes = [], reads = [];
  const elements = new Map();
  const context = vm.createContext({
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, {addEventListener(){}, innerHTML:'', textContent:''});
        return elements.get(id);
      },
      querySelectorAll: () => [],
    },
    setInterval(){},
    // Never contact a provider or start background work.
    fetch: () => new Promise(() => {}),
  });
  Object.defineProperty(context, 'localStorage', {get() {
    if (unavailable) throw Error('Storage denied');
    return {
      getItem(key) {
        reads.push(key);
        if (readFailures.includes(key)) throw Error('Read denied');
        return stored.get(key) ?? null;
      },
      setItem(key, value) {
        if (writeFails) throw Error('Quota exceeded');
        writes.push(key); stored.set(key, value);
      },
    };
  }});
  vm.runInContext(source, context);
  return {stored, writes, reads,
    airports: () => JSON.parse(vm.runInContext('JSON.stringify(airports)', context)),
    run: code => vm.runInContext(code, context),
  };
}
test('new preferences take precedence and do not read or overwrite legacy or SCAN', () => {
  const values = {[NEW]:'["JFK","ORD"]', [OLD]:'["LAX"]', 'spatchin-scan-v1':'{"keywords":"METAR"}'};
  const app = load(values);
  assert.deepEqual(app.airports(), ['JFK','ORD']);
  assert.deepEqual(app.reads, [NEW]);
  assert.deepEqual(app.writes, []);
  assert.deepEqual(Object.fromEntries(app.stored), values);
});
test('missing or invalid new value migrates legacy without changing its bytes', () => {
  for (const invalid of [undefined, '{broken', 'null', '{}', '"ORD"']) {
    const legacy = '["LAX", "ORD", "LAX", "jfk", 3, "JFK", "ABCD"]';
    const app = load({[OLD]:legacy, ...(invalid === undefined ? {} : {[NEW]:invalid})});
    assert.deepEqual(app.airports(), ['LAX','ORD','JFK']);
    assert.equal(app.stored.get(NEW), '["LAX","ORD","JFK"]');
    assert.equal(app.stored.get(OLD), legacy);
    assert.deepEqual(app.writes, [NEW]);
  }
});
test('validation retains order, first occurrence, uppercase-only rules and 30-airport limit', () => {
  const codes = Array.from({length:35}, (_, i) => 'A' + String.fromCharCode(65 + Math.floor(i/26)) + String.fromCharCode(65 + i%26));
  for (const key of [NEW, OLD]) {
    const app = load({[key]:JSON.stringify(['ord', null, 'AB', 'ABCD', codes[0], ...codes, codes[0]])});
    assert.deepEqual(app.airports(), codes.slice(0,30));
  }
});
test('preserves existing nonempty-array filtering and intentional empty-array behavior', () => {
  assert.deepEqual(load({[NEW]:'[null,"ord",42]', [OLD]:'["LAX"]'}).airports(), []);
  assert.deepEqual(load({[OLD]:'[null,"ord",42]'}).airports(), []);
  assert.deepEqual(load({[NEW]:'[]', [OLD]:'[]'}).airports(), []);
});
test('missing and malformed preferences use defaults without writing them', () => {
  for (const values of [{}, {[NEW]:'{broken', [OLD]:'{broken'}, {[NEW]:'false', [OLD]:'{}'}]) {
    const app = load(values);
    assert.deepEqual(app.airports(), defaults);
    assert.deepEqual(app.writes, []);
  }
});
test('subsequent saves use the new key and retain legacy for rollback', () => {
  const app = load({[OLD]:'["LAX"]'});
  app.run('airports.push("ATL");save();');
  assert.equal(app.stored.get(NEW), '["LAX","ATL"]');
  assert.equal(app.stored.get(OLD), '["LAX"]');
  assert.deepEqual(app.writes, [NEW, NEW]);
  app.run('airports=[];save();');
  assert.equal(app.stored.get(NEW), '[]');
  assert.deepEqual(load(Object.fromEntries(app.stored)).airports(), []);
});
test('read errors fall back independently; unavailable storage retains usable session defaults', () => {
  const app = load({[OLD]:'["JFK"]'}, {readFailures:[NEW]});
  assert.deepEqual(app.airports(), ['JFK']);
  assert.equal(app.stored.get(NEW), '["JFK"]');
  for (const options of [{readFailures:[NEW, OLD]}, {unavailable:true}]) {
    const denied = load({}, options);
    assert.deepEqual(denied.airports(), defaults);
    assert.doesNotThrow(() => denied.run('airports.push("ATL");save();'));
    assert.deepEqual(denied.airports(), [...defaults, 'ATL']);
  }
});
test('migration write failure keeps loaded preferences and legacy; later saves do not crash', () => {
  const app = load({[OLD]:'["JFK","ORD"]'}, {writeFails:true});
  assert.deepEqual(app.airports(), ['JFK','ORD']);
  assert.equal(app.stored.get(OLD), '["JFK","ORD"]');
  assert.equal(app.stored.has(NEW), false);
  assert.doesNotThrow(() => app.run('airports.push("ATL");save();'));
  assert.deepEqual(app.airports(), ['JFK','ORD','ATL']);
});
