const state = {
  token: localStorage.getItem('token') || '',
  player: JSON.parse(localStorage.getItem('player') || 'null'),
  room: null,
  match: null,
  view: 'lobby'
};

const viewEl = document.getElementById('view');
const userBar = document.getElementById('userBar');

const boardNames = [
  '启程门','长安','机缘签','洛阳','漕运税','运河电站','扬州','长安机场','朝廷令','苏州','大理寺','杭州',
  '机缘签','成都','盐铁税','临安机场','广州','朝廷令','泉州','都江堰电站','茶馆','临安','机缘签','紫禁城'
];

function setAuth(token, player) {
  state.token = token;
  state.player = player;
  localStorage.setItem('token', token);
  localStorage.setItem('player', JSON.stringify(player));
}

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: {
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
      ...(body ? { 'Content-Type': 'application/json' } : {})
    },
    ...(body ? { body: JSON.stringify(body) } : {})
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'REQUEST_FAILED');
  return data;
}

function renderUserBar() {
  userBar.innerHTML = state.player
    ? `<span class="badge">${state.player.nickname}</span><button id="logoutBtn" class="secondary">退出</button>`
    : '';
  const logout = document.getElementById('logoutBtn');
  if (logout) logout.onclick = () => {
    localStorage.clear();
    location.reload();
  };
}

function render() {
  renderUserBar();
  if (!state.token) return renderLogin();
  if (state.view === 'matching') return renderMatching();
  if (state.view === 'room') return renderRoom();
  if (state.view === 'board') return renderBoard();
  return renderLobby();
}

function renderLogin() {
  viewEl.innerHTML = `
    <section class="card">
      <h3>登录</h3>
      <p class="small">输入任意 code 模拟 wx.login</p>
      <input id="codeInput" placeholder="code，例如 u1001" />
      <input id="nameInput" placeholder="昵称（可选）" />
      <button id="loginBtn">登录</button>
      <div id="loginErr" class="small"></div>
    </section>`;

  document.getElementById('loginBtn').onclick = async () => {
    const code = document.getElementById('codeInput').value.trim();
    const nickname = document.getElementById('nameInput').value.trim();
    if (!code) return;
    try {
      const data = await api('/auth/wechat/login', { method: 'POST', body: { code, nickname } });
      setAuth(data.token, data.player);
      state.view = 'lobby';
      render();
    } catch (e) {
      document.getElementById('loginErr').textContent = e.message;
    }
  };
}

function renderLobby() {
  viewEl.innerHTML = `
    <section class="card">
      <h3>大厅</h3>
      <div>
        <select id="matchPlayers"><option value="2">2人匹配</option><option value="4">4人匹配</option></select>
        <button id="startMatchBtn">开始匹配</button>
      </div>
      <div>
        <select id="roomPlayers"><option value="2">2人房</option><option value="3">3人房</option><option value="4">4人房</option></select>
        <button id="createRoomBtn">创建房间</button>
      </div>
      <div>
        <input id="joinCode" placeholder="输入6位房间码" />
        <button id="joinRoomBtn">加入房间</button>
      </div>
      <p class="small">提示：开两个浏览器窗口，使用不同 code 登录即可联调。</p>
    </section>`;

  document.getElementById('startMatchBtn').onclick = async () => {
    const targetPlayers = Number(document.getElementById('matchPlayers').value);
    const data = await api('/matchmaking/join', { method: 'POST', body: { mode: 'classic', targetPlayers } });
    if (data.status === 'matched') {
      state.match = { id: data.matchId };
      state.view = 'board';
      await refreshSnapshot();
    } else {
      state.view = 'matching';
      state.matchingConfig = { targetPlayers };
    }
    render();
  };

  document.getElementById('createRoomBtn').onclick = async () => {
    const maxPlayers = Number(document.getElementById('roomPlayers').value);
    state.room = await api('/rooms/create', {
      method: 'POST',
      body: { maxPlayers, maxRounds: 20, initialCash: 1500 }
    });
    state.view = 'room';
    render();
  };

  document.getElementById('joinRoomBtn').onclick = async () => {
    const roomCode = document.getElementById('joinCode').value.trim();
    state.room = await api('/rooms/join', { method: 'POST', body: { roomCode } });
    state.view = 'room';
    render();
  };
}

function renderMatching() {
  viewEl.innerHTML = `
    <section class="card">
      <h3>匹配中...</h3>
      <p class="small">目标人数：${state.matchingConfig.targetPlayers}</p>
      <button id="pollMatchBtn">刷新匹配状态</button>
      <button id="cancelMatchBtn" class="secondary">取消匹配</button>
    </section>`;

  document.getElementById('pollMatchBtn').onclick = async () => {
    const data = await api('/matchmaking/join', {
      method: 'POST',
      body: { mode: 'classic', targetPlayers: state.matchingConfig.targetPlayers }
    });
    if (data.status === 'matched') {
      state.match = { id: data.matchId };
      state.view = 'board';
      await refreshSnapshot();
      render();
    }
  };

  document.getElementById('cancelMatchBtn').onclick = async () => {
    await api('/matchmaking/cancel', { method: 'POST' });
    state.view = 'lobby';
    render();
  };
}

function renderRoom() {
  viewEl.innerHTML = `
    <section class="card">
      <h3>房间</h3>
      <p>房间码：<strong>${state.room.roomCode}</strong></p>
      <p>人数：${state.room.playerIds.length}/${state.room.maxPlayers}</p>
      <button id="refreshRoomBtn">刷新房间</button>
      <button id="startRoomBtn">房主开始</button>
      <button id="backLobbyBtn" class="secondary">回大厅</button>
      <pre>${JSON.stringify(state.room, null, 2)}</pre>
    </section>`;

  document.getElementById('refreshRoomBtn').onclick = async () => {
    state.room = await api('/rooms/join', { method: 'POST', body: { roomCode: state.room.roomCode } });
    render();
  };

  document.getElementById('startRoomBtn').onclick = async () => {
    const data = await api('/rooms/start', { method: 'POST', body: { roomId: state.room.id } });
    state.match = { id: data.matchId };
    state.view = 'board';
    await refreshSnapshot();
    render();
  };

  document.getElementById('backLobbyBtn').onclick = () => {
    state.view = 'lobby';
    render();
  };
}

async function refreshSnapshot() {
  state.match = await api(`/matches/${state.match.id}/snapshot`);
}

function renderBoard() {
  const me = state.match.players.find((p) => p.playerId === state.player.id);
  const isMyTurn = state.match.currentPlayerId === state.player.id;

  viewEl.innerHTML = `
    <section class="card">
      <h3>棋盘对局</h3>
      <p>
        <span class="badge">回合: ${state.match.round}/${state.match.maxRounds}</span>
        <span class="badge">当前玩家: ${state.match.currentPlayerId}</span>
        <span class="badge">状态: ${state.match.status}</span>
      </p>
      <p class="small">我的位置: ${me?.position ?? '-'} | 我的现金: ${me?.cash ?? '-'}</p>
      <button id="refreshBtn">刷新快照</button>
      <button id="rollBtn" ${isMyTurn ? '' : 'disabled'}>掷骰子</button>
      <button id="buyBtn" ${isMyTurn ? '' : 'disabled'}>购买当前地块</button>
      <button id="endBtn" ${isMyTurn ? '' : 'disabled'}>结束回合</button>
      <button id="backLobbyBtn" class="secondary">回大厅</button>
      <div id="actionMsg" class="small"></div>
    </section>
    <section class="card">
      <h4>24 格棋盘</h4>
      <div class="grid">
        ${boardNames.map((name, idx) => `<div class="tile">#${idx}<br/>${name}</div>`).join('')}
      </div>
    </section>
    <section class="card">
      <h4>玩家状态</h4>
      <pre>${JSON.stringify(state.match.players, null, 2)}</pre>
    </section>
    <section class="card">
      <h4>最近日志</h4>
      <pre>${JSON.stringify(state.match.logs, null, 2)}</pre>
    </section>`;

  document.getElementById('refreshBtn').onclick = async () => { await refreshSnapshot(); render(); };
  document.getElementById('backLobbyBtn').onclick = () => { state.view = 'lobby'; render(); };

  document.getElementById('rollBtn').onclick = async () => {
    try {
      const data = await api(`/matches/${state.match.id}/roll-dice`, { method: 'POST' });
      document.getElementById('actionMsg').textContent = `落点: ${data.tile.name}, 动作: ${data.action}`;
      await refreshSnapshot();
      render();
    } catch (e) { document.getElementById('actionMsg').textContent = e.message; }
  };

  document.getElementById('buyBtn').onclick = async () => {
    try {
      const data = await api(`/matches/${state.match.id}/buy-property`, { method: 'POST' });
      document.getElementById('actionMsg').textContent = `购买成功: 地块#${data.tileId}, 花费${data.price}`;
      await refreshSnapshot();
      render();
    } catch (e) { document.getElementById('actionMsg').textContent = e.message; }
  };

  document.getElementById('endBtn').onclick = async () => {
    try {
      const data = await api(`/matches/${state.match.id}/end-turn`, { method: 'POST' });
      document.getElementById('actionMsg').textContent = `下一位: ${data.nextPlayerId}`;
      await refreshSnapshot();
      render();
    } catch (e) { document.getElementById('actionMsg').textContent = e.message; }
  };
}

render();
