import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from '@/app/App'
import { applyInitialTheme } from '@/lib/theme'
import '@/styles/index.css'

// 先在 React 挂载前把主题写到 <html>，避免首屏闪一下默认深色
applyInitialTheme()

const container = document.getElementById('root')
if (container === null) {
  throw new Error('缺少 #root 挂载点')
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
