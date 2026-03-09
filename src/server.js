import http from 'node:http';
import { MemoryStore } from './store.js';
import { buyProperty, createMatch, endTurn, rollDice, snapshot } from './game-engine.js';

const store = new MemoryStore();

function json(res, status, data) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let raw = '';
    req.on('data', (chunk) => (raw += chunk));
    req.on('end', () => {
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new Error('INVALID_JSON'));
      }
    });
  });
}

function getPlayer(req) {
  const auth = req.headers.authorization || '';
  const token = auth.startsWith('Bearer ') ? auth.slice(7) : '';
  return store.getPlayerByToken(token);
}

function requirePlayer(req, res) {
  const player = getPlayer(req);
  if (!player) {
    json(res, 401, { error: 'UNAUTHORIZED' });
    return null;
  }
  return player;
}

function matchPlayers(queue, n) {
  if (queue.length < n) return null;
  return queue.splice(0, n);
}

function createServer() {
  return http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');

      if (req.method === 'POST' && url.pathname === '/auth/wechat/login') {
        const body = await parseBody(req);
        if (!body.code) return json(res, 400, { error: 'MISSING_CODE' });
        const openid = `wx_${body.code}`;
        const player = store.getOrCreatePlayer({ openid, nickname: body.nickname, avatarUrl: body.avatarUrl });
        const token = store.issueToken(player.id);
        return json(res, 200, { token, player });
      }

      if (req.method === 'POST' && url.pathname === '/matchmaking/join') {
        const player = requirePlayer(req, res);
        if (!player) return;
        const body = await parseBody(req);
        const targetPlayers = Number(body.targetPlayers || 4);
        if (![2, 4].includes(targetPlayers)) return json(res, 400, { error: 'INVALID_TARGET_PLAYERS' });
        const mode = body.mode || 'classic';
        const key = `${mode}:${targetPlayers}`;
        const queue = store.ensureQueue(key);
        if (!queue.includes(player.id)) queue.push(player.id);

        const ready = matchPlayers(queue, targetPlayers);
        if (!ready) return json(res, 200, { status: 'queued', mode, targetPlayers });

        const room = store.createRoom({ ownerPlayerId: ready[0], maxPlayers: targetPlayers, initialCash: 1500, turnTimeSec: 30, source: 'matchmaking' });
        room.playerIds = ready;
        room.status = 'playing';
        const matchId = `m_${Date.now()}`;
        const match = createMatch({ matchId, room, playerIds: ready });
        store.matches.set(matchId, match);
        return json(res, 200, { status: 'matched', roomId: room.id, matchId, playerIds: ready });
      }

      if (req.method === 'POST' && url.pathname === '/matchmaking/cancel') {
        const player = requirePlayer(req, res);
        if (!player) return;
        for (const queue of store.matchQueues.values()) {
          const idx = queue.indexOf(player.id);
          if (idx >= 0) queue.splice(idx, 1);
        }
        return json(res, 200, { status: 'cancelled' });
      }

      if (req.method === 'POST' && url.pathname === '/rooms/create') {
        const player = requirePlayer(req, res);
        if (!player) return;
        const body = await parseBody(req);
        const maxPlayers = Number(body.maxPlayers || 4);
        if (maxPlayers < 2 || maxPlayers > 4) return json(res, 400, { error: 'INVALID_MAX_PLAYERS' });
        const room = store.createRoom({
          ownerPlayerId: player.id,
          maxPlayers,
          initialCash: Number(body.initialCash || 1500),
          turnTimeSec: Number(body.turnTimeSec || 30),
          source: 'custom_room'
        });
        return json(res, 200, room);
      }

      if (req.method === 'POST' && url.pathname === '/rooms/join') {
        const player = requirePlayer(req, res);
        if (!player) return;
        const { roomCode } = await parseBody(req);
        const room = [...store.rooms.values()].find((r) => r.roomCode === roomCode);
        if (!room) return json(res, 404, { error: 'ROOM_NOT_FOUND' });
        if (room.status !== 'waiting') return json(res, 409, { error: 'ROOM_NOT_WAITING' });
        if (room.playerIds.length >= room.maxPlayers) return json(res, 409, { error: 'ROOM_FULL' });
        if (!room.playerIds.includes(player.id)) room.playerIds.push(player.id);
        return json(res, 200, room);
      }

      if (req.method === 'POST' && url.pathname === '/rooms/start') {
        const player = requirePlayer(req, res);
        if (!player) return;
        const { roomId } = await parseBody(req);
        const room = store.rooms.get(roomId);
        if (!room) return json(res, 404, { error: 'ROOM_NOT_FOUND' });
        if (room.ownerPlayerId !== player.id) return json(res, 403, { error: 'NOT_OWNER' });
        if (room.playerIds.length < 2) return json(res, 409, { error: 'NOT_ENOUGH_PLAYERS' });
        room.status = 'playing';
        const matchId = `m_${Date.now()}`;
        const match = createMatch({ matchId, room, playerIds: room.playerIds });
        store.matches.set(matchId, match);
        return json(res, 200, { roomId, matchId, playerIds: room.playerIds });
      }

      const matchIdMatch = url.pathname.match(/^\/matches\/([^/]+)\/(roll-dice|buy-property|end-turn)$/);
      if (req.method === 'POST' && matchIdMatch) {
        const player = requirePlayer(req, res);
        if (!player) return;
        const [, matchId, action] = matchIdMatch;
        const match = store.matches.get(matchId);
        if (!match) return json(res, 404, { error: 'MATCH_NOT_FOUND' });
        try {
          if (action === 'roll-dice') return json(res, 200, rollDice(match, player.id));
          if (action === 'buy-property') return json(res, 200, buyProperty(match, player.id));
          return json(res, 200, endTurn(match, player.id));
        } catch (err) {
          return json(res, 409, { error: err.message });
        }
      }

      const snapshotMatch = url.pathname.match(/^\/matches\/([^/]+)\/snapshot$/);
      if (req.method === 'GET' && snapshotMatch) {
        const player = requirePlayer(req, res);
        if (!player) return;
        const [, matchId] = snapshotMatch;
        const match = store.matches.get(matchId);
        if (!match) return json(res, 404, { error: 'MATCH_NOT_FOUND' });
        return json(res, 200, snapshot(match));
      }

      json(res, 404, { error: 'NOT_FOUND' });
    } catch (err) {
      json(res, 400, { error: err.message || 'BAD_REQUEST' });
    }
  });
}

if (process.argv[1] && new URL(`file://${process.argv[1]}`).href === import.meta.url) {
  const port = Number(process.env.PORT || 3000);
  createServer().listen(port, () => {
    console.log(`wechat-monopoly-mvp listening on :${port}`);
  });
}

export { createServer, store };
