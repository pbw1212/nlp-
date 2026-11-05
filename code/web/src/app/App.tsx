import type { ViewId } from '@/app/pages'
import { AppShell } from '@/components/layout/AppShell'
import { ApiKeyDialog } from '@/components/ApiKeyDialog'
import { ChatPage } from '@/features/chat/ChatPage'
import { CollaborationPage } from '@/features/collaboration/CollaborationPage'
import { ContextPage } from '@/features/context/ContextPage'
import { GovernancePage } from '@/features/governance/GovernancePage'
import { MemoryPage } from '@/features/memory/MemoryPage'
import { RetrievalPage } from '@/features/retrieval/RetrievalPage'
import { TaskPage } from '@/features/tasks/TaskPage'
import { AppStateProvider, useAppState } from '@/state/AppState'

export function App() {
  return (
    <AppStateProvider>
      <AppShellWithPage />
    </AppStateProvider>
  )
}

/**
 * 页面分发。
 *
 * 七个界面全部已接入真实后端数据（对话 / 记忆 / 任务 / 上下文 / 证据 / 协作 / 治理）。
 *
 * `flush` 只给对话页：它需要占满中栏并自己管理滚动。
 * `epoch` 变化（执行模式切换等全局变化）时整页重新挂载、重新取数，
 * 避免残留旧模式的会话/能力状态（例如惰性会话变成切不回来的幽灵）。
 */
function AppShellWithPage() {
  const { view, epoch, apiKeyDialogOpen, closeApiKeyDialog } = useAppState()
  const isChat = view === 'chat'

  return (
    <>
      <AppShell flush={isChat}>
        <PageHost key={`${view}-${epoch}`} view={view} />
      </AppShell>
      {apiKeyDialogOpen && <ApiKeyDialog onClose={closeApiKeyDialog} />}
    </>
  )
}

function PageHost({ view }: { view: ViewId }) {
  if (view === 'chat') return <ChatPage />
  if (view === 'memory') return <MemoryPage />
  if (view === 'tasks') return <TaskPage />
  if (view === 'context') return <ContextPage />
  if (view === 'retrieval') return <RetrievalPage />
  if (view === 'collaboration') return <CollaborationPage />
  if (view === 'governance') return <GovernancePage />
  return null
}
