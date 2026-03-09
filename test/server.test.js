import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from '../src/server.js';

let server;
let baseUrl;

async function api(path, { method = 'GET', token, body } = {}) {
  const res = await fetch(`${baseUrl}${path}`, {
    method,
    headers: {
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(body ? { 'content-type': 'application/json' } : {})
    },
    ...(body ? { body: JSON.stringify(body) } : {})
  });
  return { status: res.status, data: await res.json() };
}

async function login(code) {
  const { data } = await api('/auth/wechat/login', { method: 'POST', body: { code } });
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

  const roomRes = await api('/rooms/create', {
    method: 'POST',
    token: tokenA,
    body: { maxPlayers: 2, initialCash: 1500, seed: 42, maxRounds: 10 }
  });
  assert.equal(roomRes.status, 200);

  const joinRes = await api('/rooms/join', {
    method: 'POST',
    token: tokenB,
    body: { roomCode: roomRes.data.roomCode }
  });
  assert.equal(joinRes.status, 200);

  const startRes = await api('/rooms/start', {
    method: 'POST',
    token: tokenA,
    body: { roomId: roomRes.data.id }
  });
  assert.equal(startRes.status, 200);
  const matchId = startRes.data.matchId;

  const rollRes = await api(`/matches/${matchId}/roll-dice`, { method: 'POST', token: tokenA });
  assert.equal(rollRes.status, 200);
  assert.ok(rollRes.data.tile);

  const endRes = await api(`/matches/${matchId}/end-turn`, { method: 'POST', token: tokenA });
  assert.equal(endRes.status, 200);

  const snapshotRes = await api(`/matches/${matchId}/snapshot`, { token: tokenA });
  assert.equal(snapshotRes.status, 200);
  assert.equal(snapshotRes.data.players.length, 2);
  assert.equal(snapshotRes.data.seed, 42);
  assert.ok(snapshotRes.data.currentPlayerId);
});

test('matchmaking can form 2-player game', async () => {
  const tokenC = await login('c01');
  const tokenD = await login('d01');

  const queued = await api('/matchmaking/join', {
    method: 'POST',
    token: tokenC,
    body: { mode: 'classic', targetPlayers: 2 }
  });
  assert.equal(queued.data.status, 'queued');

  const matched = await api('/matchmaking/join', {
    method: 'POST',
    token: tokenD,
    body: { mode: 'classic', targetPlayers: 2 }
  });
  assert.equal(matched.data.status, 'matched');
  assert.equal(matched.data.playerIds.length, 2);
});

test('match finishes when maxRounds reached', async () => {
  const tokenA = await login('e01');
  const tokenB = await login('f01');

  const room = await api('/rooms/create', {
    method: 'POST',
    token: tokenA,
    body: { maxPlayers: 2, initialCash: 1500, seed: 7, maxRounds: 1 }
  });
  await api('/rooms/join', { method: 'POST', token: tokenB, body: { roomCode: room.data.roomCode } });
  const start = await api('/rooms/start', { method: 'POST', token: tokenA, body: { roomId: room.data.id } });
  const matchId = start.data.matchId;

  await api(`/matches/${matchId}/roll-dice`, { method: 'POST', token: tokenA });
  await api(`/matches/${matchId}/end-turn`, { method: 'POST', token: tokenA });
  await api(`/matches/${matchId}/roll-dice`, { method: 'POST', token: tokenB });
  const endB = await api(`/matches/${matchId}/end-turn`, { method: 'POST', token: tokenB });

  assert.equal(endB.data.status, 'finished');
  assert.ok(endB.data.winnerPlayerId);

  const blocked = await api(`/matches/${matchId}/roll-dice`, { method: 'POST', token: tokenA });
  assert.equal(blocked.status, 409);
  assert.equal(blocked.data.error, 'MATCH_FINISHED');
});
