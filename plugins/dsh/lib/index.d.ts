/**
 * MindForge DSH Plugin v0.5.0
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
export interface MindForgePluginConfig {
    host?: string;
    port?: number;
    autoStart?: boolean;
    mindforgePath?: string;
    pythonPath?: string;
    dbPath?: string;
    autoCapture?: boolean;
    autoInject?: boolean;
    maxInjectMemories?: number;
    minRelevance?: number;
    captureTags?: string[];
    captureImportance?: string;
    compactOutput?: boolean;
    injectFormat?: 'full' | 'compact' | 'ids-only';
}
export declare const name = "mindforge-memory";
export declare const inject: string[];
interface CordisContext {
    tools: {
        register: (tool: ToolDefinition) => () => void;
    };
    agentLoop?: {
        on?: (event: string, handler: (...args: unknown[]) => unknown) => () => void;
    };
    on: (event: string, handler: (...args: unknown[]) => unknown) => () => void;
    effect: (fn: () => (() => void) | Promise<(() => void) | void>) => void;
    config?: Record<string, unknown>;
}
interface ToolDefinition {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    execute: (args: Record<string, unknown>) => Promise<unknown>;
}
export declare function apply(ctx: CordisContext, config?: MindForgePluginConfig): void;
export {};
