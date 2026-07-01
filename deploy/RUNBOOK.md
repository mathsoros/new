# 晨报 routine 上线 Runbook（按顺序执行）

在 **Windows 常驻机**上执行。每步一条命令；标 ☐ 的是检查点。
（这套不在 CI 沙箱里跑——沙箱够不到这台机、且数据源主机被沙箱出口封了。）

前置一次性：把分支拉到部署机
```powershell
cd E:\
git clone -b claude/pensive-goldberg-abgddl https://github.com/mathsoros/new.git market-data-mcp
# 已克隆过则： cd E:\market-data-mcp; git pull
```

---

## 一键编排（推荐）

改好 `deploy\deploy.ps1` 顶部 CONFIG（`$TunnelId` 填你现有 gbrain/oura 隧道 id；域名/端口默认 market.popcult.win:8790），然后：
```powershell
cd E:\market-data-mcp
.\deploy\deploy.ps1
```
脚本按序跑 STEP 1/2/3/5，并在 STEP 4、6 停下让你做手动动作（编辑 cloudflared config、在 claude.ai 注册 connector）。

---

## 或者手动逐步

**STEP 1 — venv + 依赖**
```powershell
cd E:\market-data-mcp
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```
☐ 无报错

**STEP 2 — 配 API key**
```powershell
copy .env.example .env
notepad .env   # 填 FRED_API_KEY / FINNHUB_API_KEY；确认 MCP_TRANSPORT=http, MCP_AUTH=oauth, PUBLIC_URL=https://market.popcult.win
```
☐ 两个 key 已填，未带引号

**STEP 3 — 真实数据冒烟（验收 #3/#4/#5）**
```powershell
.\.venv\Scripts\python -m tests.live_smoke
```
☐ 看到 `LIVE SMOKE: PASS`：沪深300/PBOC中间价/DR007/10Y CGB + DGS10/DGS2 + S&P500(或SPY)/VIX 都有值

**STEP 4 — Cloudflare Tunnel ingress（复用现有隧道）**
在 `~/.cloudflared/config.yml` 的 404 catch-all 之上加：
```yaml
  - hostname: market.popcult.win
    service: http://localhost:8790
```
```powershell
cloudflared tunnel route dns <tunnelId> market.popcult.win
Stop-ScheduledTask -TaskName cloudflared-tunnel; Start-ScheduledTask -TaskName cloudflared-tunnel
```
☐ 隧道重启无误

**STEP 5 — 注册常驻进程并启动**
```powershell
.\deploy\register-scheduled-task.ps1
Start-ScheduledTask -TaskName market-data-mcp
```
☐ 本地 `POST http://127.0.0.1:8790/mcp` 返回 **401 + WWW-Authenticate**（开了 OAuth 时这是健康信号，不是错误）

**STEP 6 — claude.ai 注册 connector + 挂到晨报 routine**
1. claude.ai → Settings → Connectors → Add custom connector → URL `https://market.popcult.win`
2. 走完 OAuth/DCR 握手（若像 brain.popcult.win 那样只放行 Anthropic IP，握手时临时把自己公网 IP 加白名单，连上后删）
3. 编辑「每日金融市场晨报」routine → 勾上 `market-data-mcp` connector（gbrain/oura 保持启用）
4. 晨报 prompt 不用改（数据层已 MCP 优先、web search 兜底）

☐ 手动跑一次 routine 验证：数字带 GMT+8+源时区时间戳；取不到的（DXY/consensus）显示 `—` 不编造；market-data+gbrain+oura 同一次运行都可用

---

## 排障速查（来自 gbrain MCP playbook 四坑）
| 现象 | 对应坑 / 解法 |
|---|---|
| `/health` 或首请求超时挂死 | 坑#1 多实例抢锁 → 确保单实例；重启前按端口强杀旧进程 |
| 经 Cloudflare 的 OAuth 端点失败、本机 curl 正常 | 坑#2 trust proxy → 本部署用 uvicorn 无此问题；cloudflared 保持 loopback |
| `400 invalid_client` | 坑#3 机密客户端 secret → FastMCP OAuth provider 内部处理，无需自改 |
| 连接器 "Couldn't reach" / CF 530 | 坑#4 网络层 → 对照同隧道 gbrain 是否也挂；都挂=网络/隧道，重启 cloudflared |
| 重启后第一个请求空响应 | 冷启动竞态 → 重试即可 |
