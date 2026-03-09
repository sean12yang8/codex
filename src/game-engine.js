import { BOARD_TILES, PURCHASABLE_TYPES } from './board.js';

const byId = new Map(BOARD_TILES.map((t) => [t.id, t]));

function createRng(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function totalAssets(match, playerId) {
  const player = match.players.find((p) => p.playerId === playerId);
  const propertiesValue = [...match.properties.values()]
    .filter((p) => p.playerId === playerId)
    .reduce((sum, p) => sum + (byId.get(p.tileId)?.price || 0), 0);
  return (player?.cash || 0) + propertiesValue;
}

function updateWinner(match) {
  const active = match.players.filter((p) => !p.bankrupt);
  if (active.length === 1) {
    match.status = 'finished';
    match.winnerPlayerId = active[0].playerId;
    return;
  }

  if (match.round > match.maxRounds) {
    const ranked = [...match.players]
      .map((p) => ({ playerId: p.playerId, assets: totalAssets(match, p.playerId) }))
      .sort((a, b) => b.assets - a.assets);
    match.status = 'finished';
    match.winnerPlayerId = ranked[0]?.playerId || null;
  }
}

export function createMatch({ matchId, room, playerIds }) {
  const players = playerIds.map((playerId, idx) => ({
    playerId,
    cash: room.initialCash,
    position: 0,
    bankrupt: false,
    turnOrder: idx
  }));

  const seed = Number(room.seed ?? Date.now()) >>> 0;

  return {
    id: matchId,
    roomId: room.id,
    status: 'playing',
    seed,
    rng: createRng(seed),
    maxRounds: Number(room.maxRounds || 30),
    round: 1,
    currentTurn: 0,
    lastDice: null,
    winnerPlayerId: null,
    players,
    properties: new Map(),
    logs: [`game_started:seed=${seed}`]
  };
}

function nextActiveTurn(match, fromTurn) {
  for (let i = 1; i <= match.players.length; i++) {
    const idx = (fromTurn + i) % match.players.length;
    if (!match.players[idx].bankrupt) return idx;
  }
  return fromTurn;
}

export function rollDice(match, playerId) {
  if (match.status !== 'playing') throw new Error('MATCH_FINISHED');
  const current = match.players[match.currentTurn];
  if (current.playerId !== playerId) throw new Error('NOT_YOUR_TURN');
  const dice = Math.floor(match.rng() * 6) + 1;
  current.position = (current.position + dice) % BOARD_TILES.length;
  match.lastDice = { playerId, value: dice };
  match.logs.push(`dice:${playerId}:${dice}`);
  return settleTile(match, current, dice);
}

function settleTile(match, playerState, dice) {
  const tile = byId.get(playerState.position);
  const owner = match.properties.get(tile.id);
  const result = { tile, action: 'none' };

  if (tile.type === 'tax') {
    playerState.cash -= tile.amount;
    result.action = 'paid_tax';
    result.amount = tile.amount;
  } else if (tile.type === 'event') {
    const gain = Math.floor(match.rng() * 2) === 0 ? 80 : -80;
    playerState.cash += gain;
    result.action = gain > 0 ? 'event_gain' : 'event_loss';
    result.amount = gain;
  } else if (PURCHASABLE_TYPES.has(tile.type)) {
    if (!owner) {
      result.action = 'can_buy';
    } else if (owner.playerId !== playerState.playerId) {
      const fee = calcToll(match, tile, owner, dice);
      playerState.cash -= fee;
      const ownerState = match.players.find((p) => p.playerId === owner.playerId);
      ownerState.cash += fee;
      result.action = 'paid_toll';
      result.amount = fee;
      result.toPlayerId = owner.playerId;
    }
  }

  if (playerState.cash < 0) {
    playerState.bankrupt = true;
    result.bankrupt = true;
  }

  updateWinner(match);
  if (match.status === 'finished') result.gameOver = true;

  match.logs.push(`settle:${playerState.playerId}:${tile.id}:${result.action}`);
  return result;
}

function calcToll(match, tile, owner, dice) {
  if (tile.type === 'utility') return (tile.tollMultiplier || 8) * dice;
  if (tile.type === 'airport') {
    const ownCount = [...match.properties.values()].filter((p) => p.playerId === owner.playerId && p.type === 'airport').length;
    return tile.toll * Math.max(1, ownCount);
  }
  return tile.toll || 30;
}

export function buyProperty(match, playerId) {
  if (match.status !== 'playing') throw new Error('MATCH_FINISHED');
  const current = match.players[match.currentTurn];
  if (current.playerId !== playerId) throw new Error('NOT_YOUR_TURN');
  const tile = byId.get(current.position);
  if (!PURCHASABLE_TYPES.has(tile.type)) throw new Error('NOT_PURCHASABLE_TILE');
  if (match.properties.has(tile.id)) throw new Error('ALREADY_OWNED');
  if (current.cash < tile.price) throw new Error('NOT_ENOUGH_CASH');

  current.cash -= tile.price;
  match.properties.set(tile.id, { tileId: tile.id, type: tile.type, playerId });
  match.logs.push(`buy:${playerId}:${tile.id}`);
  return { tileId: tile.id, price: tile.price };
}

export function endTurn(match, playerId) {
  if (match.status !== 'playing') throw new Error('MATCH_FINISHED');
  const current = match.players[match.currentTurn];
  if (current.playerId !== playerId) throw new Error('NOT_YOUR_TURN');
  match.currentTurn = nextActiveTurn(match, match.currentTurn);
  if (match.currentTurn === 0) match.round += 1;
  updateWinner(match);
  match.logs.push(`turn:${match.players[match.currentTurn].playerId}`);
  return {
    nextPlayerId: match.players[match.currentTurn].playerId,
    round: match.round,
    status: match.status,
    winnerPlayerId: match.winnerPlayerId
  };
}

export function snapshot(match) {
  return {
    id: match.id,
    roomId: match.roomId,
    status: match.status,
    seed: match.seed,
    maxRounds: match.maxRounds,
    round: match.round,
    winnerPlayerId: match.winnerPlayerId,
    currentPlayerId: match.players[match.currentTurn].playerId,
    lastDice: match.lastDice,
    players: match.players,
    properties: [...match.properties.values()],
    logs: match.logs.slice(-20)
  };
}
