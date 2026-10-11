import test from 'node:test';
import assert from 'node:assert/strict';
import { latestRequest } from '../src/request.js';
const deferred = () => { let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve}; };
const ok = value => ({ok:true,json:async()=>value});
test('newer response wins even when old fetch ignores cancellation', async () => {
  const pending = [], accepted = [];
  const client = latestRequest((_, options) => { const d = deferred(); pending.push({...d, options}); return d.promise; });
  const a = client.run('/api/curve','first',v=>accepted.push(v),assert.fail);
  const b = client.run('/api/curve','second',v=>accepted.push(v),assert.fail);
  assert.equal(pending[0].options.signal.aborted,true);
  pending[1].resolve(ok('new')); await b; pending[0].resolve(ok('old')); await a;
  assert.deepEqual(accepted,['new']);
});
test('explicit cancel prevents completion; failed request can recover', async () => {
  const d = deferred(), accepted = [], errors = [];
  let calls = 0;
  const client = latestRequest(() => ++calls === 1 ? d.promise : calls === 2 ? Promise.resolve({ok:false,json:async()=>({error:'MISSING_EVIDENCE'})}) : Promise.resolve(ok('recovered')));
  const run = () => client.run('/api/curve','{}',v=>accepted.push(v),e=>errors.push(e));
  const cancelled = run(); client.cancel(); d.resolve(ok('cancelled')); await cancelled;
  await run(); await run();
  assert.deepEqual(accepted,['recovered']);assert.deepEqual(errors,['MISSING_EVIDENCE']);
});
