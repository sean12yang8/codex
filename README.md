# codex

这是一个基于微信小游戏中国风大富翁方案落地的 **MVP 服务端原型**。

## 已实现（MVP 原型）

- 微信登录接口（模拟 `wx.login` code 换取用户与 token）
- 自动匹配（2/4 人）
- 自建房 + 房间码加入 + 房主开局
- 基础对局回合：掷骰子、落点结算（含事件格收益/损失）、买地、结束回合
- 对局快照接口（包含 seed / winner / maxRounds）

> 当前为内存态实现，便于你先验证核心流程；后续可替换为 MySQL + Redis + WebSocket 网关。

## 快速启动

```bash
npm start
```

默认端口 `3000`。

## 运行测试

```bash
npm test
```

## 主要接口

- `POST /auth/wechat/login`
- `POST /matchmaking/join`
- `POST /matchmaking/cancel`
- `POST /rooms/create`（支持 `maxRounds` 与 `seed`）
- `POST /rooms/join`
- `POST /rooms/start`
- `POST /matches/:id/roll-dice`
- `POST /matches/:id/buy-property`
- `POST /matches/:id/end-turn`
- `GET /matches/:id/snapshot`

## 方案文档

- `docs/wechat-monopoly-design.md`
