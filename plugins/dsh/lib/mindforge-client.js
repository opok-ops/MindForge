/**
 * MindForge REST API Client v0.5.0
 * --------------------------------
 * Thin HTTP wrapper with retry logic and connection resilience.
 */
export class MindForgeClient {
    baseUrl;
    config;
    maxRetries;
    retryDelay;
    constructor(config) {
        this.config = config;
        this.baseUrl = `http://${config.host}:${config.port}`;
        this.maxRetries = config.maxRetries ?? 2;
        this.retryDelay = config.retryDelay ?? 500;
    }
    async request(path, options = {}) {
        const { method = 'GET', body, retries = this.maxRetries } = options;
        const url = `${this.baseUrl}${path}`;
        const init = {
            method,
            headers: { 'Content-Type': 'application/json' },
            signal: AbortSignal.timeout(10000),
        };
        if (body !== undefined) {
            init.body = JSON.stringify(body);
        }
        let lastError = null;
        for (let attempt = 0; attempt <= retries; attempt++) {
            try {
                const res = await fetch(url, init);
                if (!res.ok) {
                    const text = await res.text().catch(() => res.statusText);
                    throw new Error(`HTTP ${res.status}: ${text}`);
                }
                return res.json();
            }
            catch (err) {
                lastError = err;
                if (attempt < retries) {
                    await new Promise(r => setTimeout(r, this.retryDelay * (attempt + 1)));
                }
            }
        }
        throw lastError || new Error('Request failed');
    }
    async health() {
        return this.request('/api/health');
    }
    async isRunning() {
        try {
            await this.request('/api/health', { retries: 0 });
            return true;
        }
        catch {
            return false;
        }
    }
    async addMemory(params) {
        return this.request('/api/memories', {
            method: 'POST',
            body: params,
        });
    }
    async getMemory(id) {
        return this.request(`/api/memories/${id}`);
    }
    async updateMemory(id, params) {
        return this.request(`/api/memories/${id}`, {
            method: 'PUT',
            body: params,
        });
    }
    async deleteMemory(id) {
        return this.request(`/api/memories/${id}`, {
            method: 'DELETE',
        });
    }
    async starMemory(id, star = true) {
        return this.request(`/api/memories/${id}`, {
            method: 'PUT',
            body: { starred: star },
        });
    }
    async listMemories(params = {}) {
        const qs = new URLSearchParams();
        if (params.limit)
            qs.set('limit', String(params.limit));
        if (params.offset)
            qs.set('offset', String(params.offset));
        if (params.category)
            qs.set('category', params.category);
        const query = qs.toString();
        return this.request(`/api/memories${query ? `?${query}` : ''}`);
    }
    async search(params) {
        const qs = new URLSearchParams({ q: params.q });
        if (params.limit)
            qs.set('limit', String(params.limit));
        if (params.min_relevance)
            qs.set('min_relevance', String(params.min_relevance));
        return this.request(`/api/search?${qs.toString()}`);
    }
    async stats() {
        return this.request('/api/stats');
    }
    async tags() {
        return this.request('/api/tags');
    }
    async export() {
        return this.request('/api/export');
    }
    async ensureRunning() {
        if (await this.isRunning())
            return true;
        if (!this.config.autoStart) {
            throw new Error(`MindForge API not running at ${this.baseUrl} and autoStart disabled. ` +
                `Start: python -m mindforge.cli.main --db-path <path> serve --api --port ${this.config.port}`);
        }
        const { spawn } = await import('child_process');
        const pythonPath = this.config.pythonPath || 'python3';
        const mfPath = this.config.mindforgePath;
        if (!mfPath) {
            throw new Error('mindforgePath not set. Configure in cordis.patch.yml or start manually.');
        }
        const dbPath = this.config.dbPath || 'mindforge_agent.db';
        const args = [
            '-m', 'mindforge.cli.main',
            '--db-path', dbPath,
            'serve', '--api',
            '--host', this.config.host,
            '--port', String(this.config.port),
        ];
        const child = spawn(pythonPath, args, {
            cwd: mfPath,
            stdio: 'pipe',
            detached: false,
            env: { ...process.env, PYTHONUNBUFFERED: '1' },
        });
        child.stdout?.on('data', (d) => console.log(`[mindforge] ${d.toString().trim()}`));
        child.stderr?.on('data', (d) => console.error(`[mindforge] ${d.toString().trim()}`));
        child.on('error', (e) => console.error(`[mindforge] spawn: ${e.message}`));
        child.on('exit', (code) => {
            if (code !== null && code !== 0) {
                console.error(`[mindforge] process exited with code ${code}`);
            }
        });
        // Wait up to 15 seconds with exponential backoff
        for (let i = 0; i < 8; i++) {
            await new Promise(r => setTimeout(r, Math.min(500 * Math.pow(1.5, i), 3000)));
            if (await this.isRunning()) {
                console.log(`[mindforge] API ready at ${this.baseUrl}`);
                return true;
            }
        }
        throw new Error(`MindForge API failed to start within 15s at ${this.baseUrl}`);
    }
}
