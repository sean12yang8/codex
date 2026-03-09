import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from '../src/server.js';

let server;
let baseUrl;

async function login(code) {
  const r = await fetch(`${baseUrl}/auth/wechat/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ code })
  });
  const data = await r.json();
  return data.token;
}

test.before(async () => {
  server = createServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const { port } = server.address();
  baseUrl = `http://127.0.0.1:${port}`;
});

test.after(async () => {
  await new Promise((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())));
});

test('custom room flow start match and play one turn', async () => {
  const tokenA = await login('a01');
  const tokenB = await login('b01');

  const roomRes = await fetch(`${baseUrl}/rooms/create`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${tokenA}`,
      'content-type': 'application/json'
    },
    body: JSON.stringify({ maxPlayers: 2, initialCash: 1500 })
  });
  assert.equal(roomRes.status, 200);
  const room = await roomRes.json();

  const joinRes = await fetch(`${baseUrl}/rooms/join`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${tokenB}`,
      'content-type': 'application/json'
    },
    body: JSON.stringify({ roomCode: room.roomCode })
  });
  assert.equal(joinRes.status, 200);

  const startRes = await fetch(`${baseUrl}/rooms/start`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${tokenA}`,
      'content-type': 'application/json'
    },
    body: JSON.stringify({ roomId: room.id })
  });
  assert.equal(startRes.status, 200);
  const { matchId } = await startRes.json();

  const rollRes = await fetch(`${baseUrl}/matches/${matchId}/roll-dice`, {
    method: 'POST',
    headers: { authorization: `Bearer ${tokenA}` }
  });
  assert.equal(rollRes.status, 200);

  const endRes = await fetch(`${baseUrl}/matches/${matchId}/end-turn`, {
    method: 'POST',
    headers: { authorization: `Bearer ${tokenA}` }
  });
  assert.equal(endRes.status, 200);

  const snapshotRes = await fetch(`${baseUrl}/matches/${matchId}/snapshot`, {
    headers: { authorization: `Bearer ${tokenA}` }
  });
  assert.equal(snapshotRes.status, 200);
  const snap = await snapshotRes.json();
  assert.equal(snap.players.length, 2);
  assert.ok(snap.currentPlayerId);
});

test('matchmaking can form 2-player game', async () => {
  const tokenC = await login('c01');
  const tokenD = await login('d01');

  const queued = await fetch(`${baseUrl}/matchmaking/join`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${tokenC}`,
      'content-type': 'application/json'
    },
    body: JSON.stringify({ mode: 'classic', targetPlayers: 2 })
  });
  const qData = await queued.json();
  assert.equal(qData.status, 'queued');

  const matched = await fetch(`${baseUrl}/matchmaking/join`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${tokenD}`,
      'content-type': 'application/json'
    },
    body: JSON.stringify({ mode: 'classic', targetPlayers: 2 })
  });
  const mData = await matched.json();
  assert.equal(mData.status, 'matched');
  assert.equal(mData.playerIds.length, 2);
});
