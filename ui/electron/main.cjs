// 共风 GoFor Label Print · Electron 桌面壳
// 用法：
//   cd ui && npm run build
//   electron43 ui/electron/main.js                 # 生产：加载 dist/index.html
//   GOFOR_DEV=1 electron43 ui/electron/main.js     # 开发：加载 http://127.0.0.1:5173
// 同时会自动拉起 server（python3 -m gongfeng_print.server --port 17600）。
const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('node:child_process')
const path = require('node:path')
const fs = require('node:fs')
const http = require('node:http')

const REPO = path.resolve(__dirname, '..', '..')
const SERVER_DIR = path.join(REPO, 'server')
const DIST_INDEX = path.join(__dirname, '..', 'dist', 'index.html')
const DEV_URL = 'http://127.0.0.1:5173'
const API = 'http://127.0.0.1:17600/api/health'

let serverProc = null
let win = null

function apiUp() {
  return new Promise((resolve) => {
    const req = http.get(API, { timeout: 600 }, (res) => { res.resume(); resolve(res.statusCode === 200) })
    req.on('error', () => resolve(false))
    req.on('timeout', () => { req.destroy(); resolve(false) })
  })
}

async function startServer() {
  if (await apiUp()) return
  serverProc = spawn('python3', ['-m', 'gongfeng_print.server', '--port', '17600'], {
    cwd: SERVER_DIR, stdio: 'ignore', detached: false,
  })
  serverProc.on('error', (err) => console.error('[gofor] 启动后端失败:', err.message))
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 250))
    if (await apiUp()) return
  }
  console.error('[gofor] 后端 10s 内未就绪（UI 仍可打开，状态栏会提示离线）')
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280, height: 880, minWidth: 1024, minHeight: 700,
    title: '共风 GoFor Label Print · 标签设计器',
    backgroundColor: '#0f1115',
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  })
  const dev = process.env.GOFOR_DEV === '1'
  if (dev) {
    win.loadURL(DEV_URL)
  } else if (fs.existsSync(DIST_INDEX)) {
    win.loadFile(DIST_INDEX)
  } else {
    dialog.showMessageBox({ message: '未找到 ui/dist，先执行 npm run build。已回退到开发服务器。' })
    win.loadURL(DEV_URL)
  }
  win.on('closed', () => { win = null })
}

app.whenReady().then(async () => {
  await startServer()
  createWindow()
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() })
})

app.on('window-all-closed', () => {
  if (serverProc) { try { serverProc.kill() } catch (e) { /* noop */ } }
  if (process.platform !== 'darwin') app.quit()
})
