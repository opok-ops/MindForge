/**
 * MindForge DSH Plugin v0.3.0
 * ===========================
 *
 * Persistent 4-layer memory engine for DeepSeek Harness agents.
 *
 * Tools: memory_add, memory_search, memory_get, memory_list,
 *        memory_update, memory_delete, memory_stats, memory_tags, memory_star
 *
 * Hooks: turn/start (auto-recall), turn/end (auto-capture)
 *
 * Architecture:
 *   Plugin (TS) → HTTP localhost → MindForge REST API (Python) → SQLite
 *
 * License: MIT
 */

import { MindForgeClient } from './mindforge-client.js'

export interface MindForgePluginConfig {
  host?: string
  port?: number
  autoStart?: boolean
  mindforgePath?: string
  pythonPath?: string
  dbPath?: string
  autoCapture?: boolean
  autoInject?: boolean
  maxInjectMemories?: number
  minRelevance?: number
  captureTags?: string[]
  captureImportance?: string
  compactOutput?: boolean
  injectFormat?: 'full' | 'compact' | 'ids-only'
}

export const name = 'mindforge-memory'

export const inject = ['tools', 'agentLoop']

interface CordisContext {
  tools: {
    register: (tool: ToolDefinition) => () => void
  }
  agentLoop?: {
    on?: (event: string, handler: (...args: unknown[]) => unknown) => () => void
  }
  on: (event: string, handler: (...args: unknown[]) => unknown) => () => void
  effect: (fn: () => (() => void) | Promise<(() => void) | void>) => void
  config?: Record<string, unknown>
}

interface ToolDefinition {
  name: string
  description: string
  parameters: Record<string, unknown>
  execute: (args: Record<string, unknown>) => Promise<unknown>
}

interface TurnContext {
  turnId: string
  userMessage: string
  startTime: number
  recalledMemories: string[]
}

const activeTurns = new Map<string, TurnContext>()

export function apply(ctx: CordisContext, config: MindForgePluginConfig = {}) {
  const opts: Required<MindForgePluginConfig> = {
    host: config.host || '127.0.0.1',
    port: config.port || 8765,
    autoStart: config.autoStart ?? true,
    mindforgePath: config.mindforgePath || '',
    pythonPath: config.pythonPath || 'python3',
    dbPath: config.dbPath || 'mindforge_agent.db',
    autoCapture: config.autoCapture ?? true,
    autoInject: config.autoInject ?? true,
    maxInjectMemories: config.maxInjectMemories || 5,
    minRelevance: config.minRelevance || 0.3,
    captureTags: config.captureTags || ['dsh', 'agent-session'],
    captureImportance: config.captureImportance || 'MEDIUM',
    compactOutput: config.compactOutput ?? true,
    injectFormat: config.injectFormat || 'compact',
  }

  const client = new MindForgeClient({
    host: opts.host,
    port: opts.port,
    autoStart: opts.autoStart,
    mindforgePath: opts.mindforgePath,
    pythonPath: opts.pythonPath,
    dbPath: opts.dbPath,
  })

  let initialized = false

  // ===== Tool Definitions (compact descriptions for token savings) =====

  const toolAdd: ToolDefinition = {
    name: 'memory_add',
    description:
      'Store info in persistent long-term memory. Survives across sessions. ' +
      'Use for user preferences, decisions, facts, or instructions to remember.',
    parameters: {
      type: 'object',
      properties: {
        content: { type: 'string', description: 'Info to remember. Be specific and self-contained.' },
        category: { type: 'string', description: 'Category: preference|decision|fact|code|general', default: 'general' },
        tags: { type: 'array', items: { type: 'string' }, description: 'Tags for retrieval.' },
        importance: { type: 'string', enum: ['HIGH', 'MEDIUM', 'LOW'], default: 'MEDIUM' },
      },
      required: ['content'],
    },
    execute: async (args) => {
      await ensureInitialized()
      const entry = await client.addMemory({
        content: String(args.content),
        category: args.category ? String(args.category) : 'general',
        tags: Array.isArray(args.tags) ? args.tags.map(String) : [],
        importance: args.importance ? String(args.importance) : 'MEDIUM',
      })
      return { ok: true, id: entry.id }
    },
  }

  const toolSearch: ToolDefinition = {
    name: 'memory_search',
    description:
      'Search long-term memory by natural language. Returns matching memories with relevance scores.',
    parameters: {
      type: 'object',
      properties: {
        q: { type: 'string', description: 'Search query.' },
        limit: { type: 'number', description: 'Max results.', default: 5 },
        min_relevance: { type: 'number', description: 'Min score 0-1.', default: 0.3 },
      },
      required: ['q'],
    },
    execute: async (args) => {
      await ensureInitialized()
      const result = await client.search({
        q: String(args.q),
        limit: args.limit !== undefined ? Number(args.limit) : 5,
        min_relevance: args.min_relevance !== undefined ? Number(args.min_relevance) : 0.3,
      })
      if (opts.compactOutput) {
        return {
          total: result.total,
          results: result.results.map(r => ({
            id: r.id,
            content: r.content,
            category: r.category,
            score: Number(r.relevance_score.toFixed(3)),
            tags: r.tags,
          })),
        }
      }
      return {
        query: result.query,
        total: result.total,
        results: result.results.map(r => ({
          id: r.id,
          content: r.content,
          category: r.category,
          relevance: Number(r.relevance_score.toFixed(3)),
          tags: r.tags,
        })),
      }
    },
  }

  const toolGet: ToolDefinition = {
    name: 'memory_get',
    description: 'Get full memory by ID. Use after memory_search for details.',
    parameters: {
      type: 'object',
      properties: {
        id: { type: 'string', description: 'Memory ID.' },
      },
      required: ['id'],
    },
    execute: async (args) => {
      await ensureInitialized()
      const entry = await client.getMemory(String(args.id))
      return {
        id: entry.id,
        content: entry.content,
        category: entry.category,
        tags: entry.tags,
        importance: entry.importance,
        layer: entry.layer,
        created: new Date(entry.created_at * 1000).toISOString(),
        accesses: entry.access_count,
      }
    },
  }

  const toolList: ToolDefinition = {
    name: 'memory_list',
    description:
      'List recent memories with pagination. Optionally filter by category.',
    parameters: {
      type: 'object',
      properties: {
        limit: { type: 'number', description: 'Max results.', default: 20 },
        offset: { type: 'number', description: 'Skip first N.', default: 0 },
        category: { type: 'string', description: 'Filter by category.' },
      },
    },
    execute: async (args) => {
      await ensureInitialized()
      const result = await client.listMemories({
        limit: args.limit ? Number(args.limit) : 20,
        offset: args.offset ? Number(args.offset) : 0,
        category: args.category ? String(args.category) : undefined,
      })
      if (opts.compactOutput) {
        return {
          total: result.total,
          memories: result.memories.map((m: MemoryEntry) => ({
            id: m.id,
            content: m.content.slice(0, 200),
            category: m.category,
            tags: m.tags,
            starred: m.starred,
          })),
        }
      }
      return result
    },
  }

  const toolUpdate: ToolDefinition = {
    name: 'memory_update',
    description:
      'Update a memory: change content, importance, or tags.',
    parameters: {
      type: 'object',
      properties: {
        id: { type: 'string', description: 'Memory ID.' },
        content: { type: 'string', description: 'New content.' },
        importance: { type: 'string', enum: ['HIGH', 'MEDIUM', 'LOW'] },
        tags: { type: 'array', items: { type: 'string' } },
      },
      required: ['id'],
    },
    execute: async (args) => {
      await ensureInitialized()
      const params: Record<string, unknown> = {}
      if (args.content) params.content = String(args.content)
      if (args.importance) params.importance = String(args.importance)
      if (args.tags) params.tags = (args.tags as unknown[]).map(String)
      await client.updateMemory(String(args.id), params)
      return { ok: true, id: args.id }
    },
  }

  const toolDelete: ToolDefinition = {
    name: 'memory_delete',
    description: 'Delete a memory by ID. Only when user explicitly asks to forget.',
    parameters: {
      type: 'object',
      properties: {
        id: { type: 'string', description: 'Memory ID.' },
      },
      required: ['id'],
    },
    execute: async (args) => {
      await ensureInitialized()
      await client.deleteMemory(String(args.id))
      return { ok: true, id: args.id }
    },
  }

  const toolStats: ToolDefinition = {
    name: 'memory_stats',
    description: 'Memory store stats: total, categories, importance distribution, top tags.',
    parameters: { type: 'object', properties: {} },
    execute: async () => {
      await ensureInitialized()
      const stats = await client.stats()
      return {
        total: stats.total,
        size_mb: Number((stats.db_size_bytes / 1024 / 1024).toFixed(2)),
        importance: stats.by_importance,
        layers: stats.by_layer,
        categories: stats.top_categories,
        top_tags: stats.top_tags,
        starred: stats.starred_count,
      }
    },
  }

  const toolTags: ToolDefinition = {
    name: 'memory_tags',
    description: 'List all tags with usage counts.',
    parameters: { type: 'object', properties: {} },
    execute: async () => {
      await ensureInitialized()
      const result = await client.tags()
      return { tags: result.tags.slice(0, 50) }
    },
  }

  const toolStar: ToolDefinition = {
    name: 'memory_star',
    description: 'Star or unstar a memory for quick access.',
    parameters: {
      type: 'object',
      properties: {
        id: { type: 'string', description: 'Memory ID.' },
        star: { type: 'boolean', description: 'True to star, false to unstar.', default: true },
      },
      required: ['id'],
    },
    execute: async (args) => {
      await ensureInitialized()
      const star = args.star !== false
      await client.starMemory(String(args.id), star)
      return { ok: true, id: args.id, starred: star }
    },
  }

  // Register all tools
  ctx.effect(() => {
    const disposers = [
      ctx.tools.register(toolAdd),
      ctx.tools.register(toolSearch),
      ctx.tools.register(toolGet),
      ctx.tools.register(toolList),
      ctx.tools.register(toolUpdate),
      ctx.tools.register(toolDelete),
      ctx.tools.register(toolStats),
      ctx.tools.register(toolTags),
      ctx.tools.register(toolStar),
    ]
    return () => disposers.forEach(d => d())
  })

  // ===== Hook: turn/start — auto-recall =====
  if (opts.autoInject) {
    ctx.effect(() => {
      const off = ctx.on('turn/start', async (...args: unknown[]) => {
        const turnId = String(args[0] || Date.now())
        const userMessage = extractUserMessage(args)

        if (!userMessage || userMessage.length < 3) return

        try {
          await ensureInitialized()

          const result = await client.search({
            q: userMessage.slice(0, 500),
            limit: opts.maxInjectMemories,
            min_relevance: opts.minRelevance,
          })

          if (result.results.length === 0) return

          activeTurns.set(turnId, {
            turnId,
            userMessage,
            startTime: Date.now(),
            recalledMemories: result.results.map(r => r.id),
          })

          // Format context injection based on config
          const contextLines = formatMemoryContext(result, opts.injectFormat)
          if (contextLines) {
            console.log(`[mindforge] Injected ${result.results.length} memories (${opts.injectFormat})`)
          }
        } catch (err) {
          console.error(`[mindforge] recall: ${(err as Error).message}`)
        }
      })
      return off
    })
  }

  // ===== Hook: turn/end — auto-capture =====
  if (opts.autoCapture) {
    ctx.effect(() => {
      const off = ctx.on('turn/end', async (...args: unknown[]) => {
        const turnId = String(args[0] || '')
        const turnCtx = activeTurns.get(turnId)
        if (!turnCtx) return
        activeTurns.delete(turnId)

        const duration = Date.now() - turnCtx.startTime
        if (duration < 5000) return

        try {
          await ensureInitialized()
          const summary = buildTurnSummary(args, turnCtx, duration)
          if (summary.length < 10) return

          await client.addMemory({
            content: summary,
            category: 'agent-session',
            tags: opts.captureTags,
            importance: opts.captureImportance,
          })
          console.log(`[mindforge] Captured turn ${turnId} (${(duration / 1000).toFixed(0)}s)`)
        } catch (err) {
          console.error(`[mindforge] capture: ${(err as Error).message}`)
        }
      })
      return off
    })
  }

  // ===== Init with retry =====
  async function ensureInitialized(): Promise<void> {
    if (initialized) return

    try {
      await client.ensureRunning()
      const health = await client.health()
      initialized = true
      console.log(
        `[mindforge] Connected — ${health.total_memories} memories, ` +
        `${(health.db_size_bytes / 1024 / 1024).toFixed(1)}MB`
      )
    } catch (err) {
      initialized = false
      throw new Error(
        `[mindforge] Connect failed ${opts.host}:${opts.port}: ${(err as Error).message}. ` +
        `Manual start: python -m mindforge.cli.main --db-path ${opts.dbPath} serve --api --port ${opts.port}`
      )
    }
  }

  console.log('[mindforge] v0.3.0 loaded — 9 tools registered')
}

// ===== Helpers =====

interface MemoryEntry {
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

function formatMemoryContext(
  result: { results: Array<{ id: string; content: string; category: string; relevance_score: number; tags: string[] }> },
  format: string
): string {
  if (result.results.length === 0) return ''

  switch (format) {
    case 'ids-only':
      return `[memory] ${result.results.length} relevant: ${result.results.map(r => r.id).join(', ')}`

    case 'full':
      return result.results.map((r, i) =>
        `[Memory ${i + 1}] (score:${r.relevance_score.toFixed(2)}, cat:${r.category})\n${r.content}`
      ).join('\n\n')

    case 'compact':
    default:
      return result.results.map((r, i) =>
        `${i + 1}. [${r.category}] ${r.content.slice(0, 120)}`
      ).join('\n')
  }
}

function extractUserMessage(args: unknown[]): string {
  for (const arg of args) {
    if (typeof arg === 'string') return arg
    if (arg && typeof arg === 'object') {
      const obj = arg as Record<string, unknown>
      if (typeof obj.message === 'string') return obj.message
      if (typeof obj.content === 'string') return obj.content
      if (typeof obj.input === 'string') return obj.input
      if (typeof obj.prompt === 'string') return obj.prompt
    }
  }
  return ''
}

function buildTurnSummary(
  args: unknown[],
  turnCtx: TurnContext,
  duration: number
): string {
  const parts: string[] = []
  parts.push(`Request: ${turnCtx.userMessage.slice(0, 200)}`)

  for (const arg of args) {
    if (arg && typeof arg === 'object') {
      const obj = arg as Record<string, unknown>
      if (typeof obj.response === 'string') {
        parts.push(`Response: ${obj.response.slice(0, 300)}`)
      }
      if (typeof obj.summary === 'string') {
        parts.push(`Summary: ${obj.summary.slice(0, 300)}`)
      }
      if (Array.isArray(obj.tools) && obj.tools.length > 0) {
        parts.push(`Tools: ${obj.tools.map(String).join(', ')}`)
      }
    }
  }

  parts.push(`Duration: ${(duration / 1000).toFixed(1)}s`)
  parts.push(`Recalled: ${turnCtx.recalledMemories.length} memories`)
  return parts.join('\n')
}
