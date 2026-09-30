import { useEffect, useState } from 'react'

const API = import.meta.env.VITE_API_BASE ?? 'http://localhost:18080'
type KB = { id: string; name: string; description: string }
type Doc = { id: string; original_filename: string; status: string; chunk_count: number; failure_reason: string }
type Citation = { document_id: string; document_name: string; chunk_id: number | null; chunk_index: number | null; content: string; source_type?: string }
type Chat = { answer: string; citations: Citation[]; answer_mode?: string; planner_mode?: string; tool_calls?: { tool_name: string; latency_ms: number }[] }

let accessToken = ''

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers)
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  const response = await fetch(API + path, { ...options, headers })
  const body = await response.json()
  if (!response.ok) throw new Error(body.error ?? body.detail ?? `HTTP ${response.status}`)
  return body as T
}

export default function App() {
  const [bases, setBases] = useState<KB[]>([])
  const [selected, setSelected] = useState<string>('')
  const [documents, setDocuments] = useState<Doc[]>([])
  const [name, setName] = useState('')
  const [question, setQuestion] = useState('')
  const [chat, setChat] = useState<Chat | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [authenticated, setAuthenticated] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  async function refreshBases() {
    const items = await request<KB[]>('/knowledge-bases')
    setBases(items)
    setSelected(current => current || items[0]?.id || '')
  }
  async function refreshDocuments(id: string) {
    if (id) setDocuments(await request<Doc[]>(`/knowledge-bases/${id}/documents`))
    else setDocuments([])
  }
  useEffect(() => { if (authenticated) refreshBases().catch(e => setError(e.message)) }, [authenticated])
  useEffect(() => { if (authenticated) refreshDocuments(selected).catch(e => setError(e.message)); setChat(null) }, [selected, authenticated])
  useEffect(() => {
    if (!authenticated || !selected || !documents.some(doc => doc.status === 'pending' || doc.status === 'processing')) return
    const timer = window.setInterval(() => refreshDocuments(selected).catch(e => setError(e.message)), 2000)
    return () => window.clearInterval(timer)
  }, [authenticated, selected, documents])

  async function signIn(event: React.SyntheticEvent, create: boolean) {
    event.preventDefault(); setError('')
    try {
      if (create) await request('/auth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) })
      const session = await request<{ access_token: string }>('/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) })
      accessToken = session.access_token; setAuthenticated(true); setPassword('')
    } catch (e) { setError((e as Error).message) }
  }

  async function createBase(event: React.FormEvent) {
    event.preventDefault(); setError('')
    try {
      const created = await request<KB>('/knowledge-bases', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
      setName(''); await refreshBases(); setSelected(created.id)
    } catch (e) { setError((e as Error).message) }
  }
  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; if (!file || !selected) return
    setError(''); setBusy(true)
    try {
      const form = new FormData(); form.append('file', file)
      await request<Doc>(`/knowledge-bases/${selected}/documents`, { method: 'POST', body: form })
      await refreshDocuments(selected)
    } catch (e) { setError((e as Error).message) }
    finally { setBusy(false); event.target.value = '' }
  }
  async function ask(event: React.SyntheticEvent, agent = false) {
    event.preventDefault(); if (!selected) return
    setError(''); setBusy(true); setChat(null)
    try { setChat(await request<Chat>(`/knowledge-bases/${selected}/${agent ? 'agent' : 'chat'}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) })) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }

  return <main>
    <h1>Enterprise AI Knowledge Platform</h1>
    <p className="notice">使用知识库成员权限访问文档。默认开发配置使用测试 Provider；本地模型配置见 README。</p>
    {error && <p className="error" role="alert">{error}</p>}
    {!authenticated && <section><h2>登录或注册</h2><form onSubmit={e => signIn(e, false)}>
      <input aria-label="邮箱" type="email" value={email} onChange={e => setEmail(e.target.value)} required />
      <input aria-label="密码" type="password" value={password} onChange={e => setPassword(e.target.value)} required minLength={12} />
      <button type="submit">登录</button><button type="button" onClick={e => signIn(e, true)}>注册并登录</button>
    </form></section>}
    {authenticated && <>
    <section><h2>知识库</h2>
      <form onSubmit={createBase}><input aria-label="新知识库名称" placeholder="知识库名称" value={name} onChange={e => setName(e.target.value)} required maxLength={120} /><button>创建</button></form>
      <select aria-label="选择知识库" value={selected} onChange={e => setSelected(e.target.value)}><option value="">选择知识库</option>{bases.map(base => <option key={base.id} value={base.id}>{base.name}</option>)}</select>
    </section>
    {selected && <><section><h2>文档</h2><input aria-label="上传文档" type="file" accept=".pdf,.docx,.txt,.md,.markdown" onChange={upload} disabled={busy} />
      <button type="button" onClick={() => refreshDocuments(selected).catch(e => setError(e.message))}>刷新状态</button>
      <ul>{documents.map(doc => <li key={doc.id}><strong>{doc.original_filename}</strong> — {doc.status}，{doc.chunk_count} chunks {doc.failure_reason && <span className="error">{doc.failure_reason}</span>}</li>)}</ul>
    </section><section><h2>知识库问答</h2><form onSubmit={e => ask(e)}><textarea aria-label="问题" placeholder="输入问题" value={question} onChange={e => setQuestion(e.target.value)} required maxLength={2000} /><button disabled={busy}>{busy ? '处理中…' : '提问'}</button><button type="button" disabled={busy} onClick={e => ask(e, true)}>Agent 工具问答</button></form>
      {chat && <div><p>{chat.answer}</p><small>回答模式：{chat.answer_mode ?? chat.planner_mode}</small>{chat.tool_calls && <p>工具：{chat.tool_calls.map(t => `${t.tool_name} (${t.latency_ms} ms)`).join('，')}</p>}<h3>引用来源</h3>{chat.citations.length === 0 ? <p>无引用</p> : <ol>{chat.citations.map((c, index) => <li key={`${c.document_id}-${c.chunk_id}-${index}`}><strong>{c.document_name}</strong> · {c.source_type === 'document_metadata' ? '文档元数据' : `chunk #${c.chunk_index}`}<blockquote>{c.content}</blockquote></li>)}</ol>}</div>}
    </section></>}
    </>}
  </main>
}
