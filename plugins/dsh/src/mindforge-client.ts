/**
 * MindForge REST API Client v0.3.0
 * --------------------------------
 * Thin HTTP wrapper with retry logic and connection resilience.
 */

export interface MindForgeConfig {
  host: string
  port: number
  autoStart: boolean
  mindforgePath?: string
  pythonPath?: string
  dbPath?: string
  maxRetries?: number
  retryDelay?: number
}

export interface MemoryEntry {
  id: string
  content: string
  category: string
  tags: string[]
  importance: string
  layer: string
  created_at: number
  updated_at: number
  access_count: number
  starred: boolean
  [key: string]: unknown
}

export interface SearchResult {
  id: string
  content: string
  category: string
  relevance_score: number
  tags: string[]
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
  total: number
}

export interface StatsResponse {
  total: number
  db_size_bytes: number
  by_importance: Record<string, number>
  by_layer: Record<string, number>
  top_categories: Record<string, number>
  top_tags: Record<string, number>
  starred_count: number
}

export interface HealthResponse {
  status: string
  integrity_check: string
  total_memories: number
  db_size_bytes: number
  recommendations: string[]
}

export class MindForgeClient {
  private baseUrl: string
  private config: MindForgeConfig
  private maxRetries: number
  private retryDelay: number

  constructor(config: MindForgeConfig) {
    this.config = config
    this.baseUrl = `http://${config.host}:${config.port}`
    this.maxRetries = config.maxRetries ?? 2
    this.retryDelay = config.retryDelay ?? 500
  }

  private async request<T>(
    path: string,
    options: { method?: string; body?: unknown; retries?: number } = {}
  ): Promise<T> {
    const { method = 'GET', body, retries = this.maxRetries } = options
    const url = `${this.baseUrl}${path}`

    const init: RequestInit = {
      method,
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(10000),
    }

    if (body !== undefined) {
      init.body = JSON.stringify(body)
    }

    let lastError: Error | null = null
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const res = await fetch(url, init)
        if (!res.ok) {
          const text = await res.text().catch(() => res.statusText)
          throw new Error(`HTTP ${res.status}: ${text}`)
        }
        return res.json() as Promise<T>
      } catch (err) {
        lastError = err as Error
        if (attempt < retries) {
          await new Promise(r => setTimeout(r, this.retryDelay * (attempt + 1)))
        }
      }
    }
    throw lastError || new Error('Request failed')
  }

  async health(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/api/health')
  }

  async isRunning(): Promise<boolean> {
    try {
      await this.request('/api/health', { retries: 0 })
      return true
    } catch {
      return false
    }
  }

  async addMemory(params: {
    content: string
    category?: string
    tags?: string[]
    importance?: string
  }): Promise<MemoryEntry> {
    return this.request<MemoryEntry>('/api/memories', {
      method: 'POST',
      body: params,
    })
  }

  async getMemory(id: string): Promise<MemoryEntry> {
    return this.request<MemoryEntry>(`/api/memories/${id}`)
  }

  async updateMemory(
    id: string,
    params: { content?: string; importance?: string; tags?: string[] }
  ): Promise<{ status: string; id: string }> {
    return this.request(`/api/memories/${id}`, {
      method: 'PUT',
      body: params,
    })
  }

  async deleteMemory(id: string): Promise<{ status: string; id: string }> {
    return this.request(`/api/memories/${id}`, {
      method: 'DELETE',
    })
  }

  async starMemory(id: string, star: boolean = true): Promise<{ status: string; id: string }> {
    return this.request(`/api/memories/${id}`, {
      method: 'PUT',
      body: { starred: star },
    })
  }

  async listMemories(params: {
    limit?: number
    offset?: number
    category?: string
  } = {}): Promise<{ memories: MemoryEntry[]; total: number }> {
    const qs = new URLSearchParams()
    if (params.limit) qs.set('limit', String(params.limit))
    if (params.offset) qs.set('offset', String(params.offset))
    if (params.category) qs.set('category', params.category)
    const query = qs.toString()
    return this.request(`/api/memories${query ? `?${query}` : ''}`)
  }

  async search(params: {
    q: string
    limit?: number
    min_relevance?: number
  }): Promise<SearchResponse> {
    const qs = new URLSearchParams({ q: params.q })
    if (params.limit) qs.set('limit', String(params.limit))
    if (params.min_relevance) qs.set('min_relevance', String(params.min_relevance))
    return this.request<SearchResponse>(`/api/search?${qs.toString()}`)
  }

  async stats(): Promise<StatsResponse> {
    return this.request<StatsResponse>('/api/stats')
  }

  async tags(): Promise<{ tags: [string, number][] }> {
    return this.request('/api/tags')
  }

  async export(): Promise<{ version: string; total: number; memories: MemoryEntry[] }> {
    return this.request('/api/export')
  }

  async ensureRunning(): Promise<boolean> {
    if (await this.isRunning()) return true

    if (!this.config.autoStart) {
      throw new Error(
        `MindForge API not running at ${this.baseUrl} and autoStart disabled. ` +
        `Start: python -m mindforge.cli.main --db-path <path> serve --api --port ${this.config.port}`
      )
    }

    const { spawn } = await import('child_process')
    const pythonPath = this.config.pythonPath || 'python3'
    const mfPath = this.config.mindforgePath

    if (!mfPath) {
      throw new Error(
        'mindforgePath not set. Configure in cordis.patch.yml or start manually.'
      )
    }

    const dbPath = this.config.dbPath || 'mindforge_agent.db'
    const args = [
      '-m', 'mindforge.cli.main',
      '--db-path', dbPath,
      'serve', '--api',
      '--host', this.config.host,
      '--port', String(this.config.port),
    ]

    const child = spawn(pythonPath, args, {
      cwd: mfPath,
      stdio: 'pipe',
      detached: false,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    })

    child.stdout?.on('data', (d: Buffer) => console.log(`[mindforge] ${d.toString().trim()}`))
    child.stderr?.on('data', (d: Buffer) => console.error(`[mindforge] ${d.toString().trim()}`))
    child.on('error', (e: Error) => console.error(`[mindforge] spawn: ${e.message}`))
    child.on('exit', (code: number | null) => {
      if (code !== null && code !== 0) {
        console.error(`[mindforge] process exited with code ${code}`)
      }
    })

    // Wait up to 15 seconds with exponential backoff
    for (let i = 0; i < 8; i++) {
      await new Promise(r => setTimeout(r, Math.min(500 * Math.pow(1.5, i), 3000)))
      if (await this.isRunning()) {
        console.log(`[mindforge] API ready at ${this.baseUrl}`)
        return true
      }
    }

    throw new Error(`MindForge API failed to start within 15s at ${this.baseUrl}`)
  }
}
