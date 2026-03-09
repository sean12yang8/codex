import crypto from 'node:crypto';

const randId = (prefix) => `${prefix}_${crypto.randomUUID().slice(0, 8)}`;

export class MemoryStore {
  constructor() {
    this.players = new Map();
    this.playersByOpenid = new Map();
    this.tokens = new Map();
    this.matchQueues = new Map();
    this.rooms = new Map();
    this.matches = new Map();
  }

  getOrCreatePlayer({ openid, nickname, avatarUrl }) {
    let playerId = this.playersByOpenid.get(openid);
    if (!playerId) {
      playerId = randId('p');
      const player = {
        id: playerId,
        openid,
        nickname: nickname || `玩家${this.players.size + 1}`,
        avatarUrl: avatarUrl || '',
        rankScore: 1000,
        createdAt: new Date().toISOString()
      };
      this.players.set(playerId, player);
      this.playersByOpenid.set(openid, playerId);
    }
    return this.players.get(playerId);
  }

  issueToken(playerId) {
    const token = crypto.randomBytes(24).toString('base64url');
    this.tokens.set(token, playerId);
    return token;
  }

  getPlayerByToken(token) {
    const playerId = this.tokens.get(token);
    return playerId ? this.players.get(playerId) : null;
  }

  ensureQueue(key) {
    if (!this.matchQueues.has(key)) this.matchQueues.set(key, []);
    return this.matchQueues.get(key);
  }

  createRoom({ ownerPlayerId, maxPlayers, initialCash, turnTimeSec, maxRounds, seed, source }) {
    const roomId = randId('r');
    const roomCode = `${Math.floor(100000 + Math.random() * 900000)}`;
    const room = {
      id: roomId,
      roomCode,
      ownerPlayerId,
      maxPlayers,
      initialCash,
      turnTimeSec,
      source,
      maxRounds,
      seed,
      status: 'waiting',
      playerIds: [ownerPlayerId],
      createdAt: new Date().toISOString()
    };
    this.rooms.set(roomId, room);
    return room;
  }
}
