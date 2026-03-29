/**
 * ILUMINATY Node.js Client SDK
 * ==============================
 * npm install iluminaty
 *
 * Usage:
 *   import { Iluminaty } from 'iluminaty';
 *
 *   const eye = new Iluminaty();
 *   const frame = await eye.see();
 *   const text = await eye.read();
 *   const diff = await eye.whatChanged();
 *   const ctx = await eye.whatDoing();
 */

// ─── Types ───

export interface Frame {
  timestamp: number;
  width: number;
  height: number;
  size_bytes: number;
  format: string;
  change_score: number;
  image_base64?: string;
}

export interface OCRResult {
  text: string;
  blocks: Array<{
    text: string;
    x: number;
    y: number;
    w: number;
    h: number;
    confidence: number;
  }>;
  confidence: number;
  engine: string;
  block_count: number;
}

export interface DiffResult {
  changed: boolean;
  change_percentage: number;
  changed_cells: number;
  total_cells: number;
  description: string;
  regions: Array<{
    grid: string;
    pixel_x: number;
    pixel_y: number;
    pixel_w: number;
    pixel_h: number;
    intensity: number;
  }>;
  heatmap?: number[][];
}

export interface ContextState {
  workflow: string;
  confidence: number;
  app: string;
  title: string;
  is_focused: boolean;
  time_in_workflow_seconds: number;
  switches_5min: number;
  summary: string;
}

export interface AudioState {
  level: number;
  is_speech: boolean;
}

export interface AIResponse {
  text: string;
  provider: string;
  model: string;
  latency_ms: number;
  tokens_used: number;
}

export interface Snapshot {
  timestamp: number;
  width: number;
  height: number;
  ocr_text: string;
  ocr_blocks_count: number;
  active_window: { title: string; pid: number };
  change_score: number;
  ai_prompt: string;
  image_base64?: string;
}

export interface MonitorInfo {
  count: number;
  active: number;
  monitors: Array<{
    id: number;
    resolution: string;
    position: string;
    primary: boolean;
    active: boolean;
    fps_multiplier: number;
  }>;
}

export interface HealthStatus {
  status: string;
  capture_running: boolean;
  buffer_slots: number;
}

export interface BufferStats {
  slots_used: number;
  slots_max: number;
  memory_mb: number;
  total_frames_captured: number;
  frames_dropped_no_change: number;
  current_fps: number;
}

// ─── Client ───

export class IluminatyError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(`[${status}] ${message}`);
    this.status = status;
    this.name = "IluminatyError";
  }
}

export class Iluminaty {
  private baseUrl: string;
  private apiKey?: string;

  /**
   * Create ILUMINATY client.
   * @param baseUrl - API base URL (default: http://127.0.0.1:8420)
   * @param apiKey - Optional API key for authentication
   */
  constructor(baseUrl: string = "http://127.0.0.1:8420", apiKey?: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
  }

  private async request<T>(method: string, path: string, params?: Record<string, any>): Promise<T> {
    let url = this.baseUrl + path;
    if (params && Object.keys(params).length > 0) {
      const query = new URLSearchParams(
        Object.fromEntries(
          Object.entries(params).filter(([_, v]) => v !== undefined && v !== null)
            .map(([k, v]) => [k, String(v)])
        )
      ).toString();
      if (query) url += "?" + query;
    }

    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.apiKey) headers["X-API-Key"] = this.apiKey;

    const res = await fetch(url, {
      method,
      headers,
      body: method === "POST" ? "" : undefined,
    });

    if (!res.ok) {
      const text = await res.text();
      throw new IluminatyError(res.status, text);
    }

    return res.json() as Promise<T>;
  }

  private get<T>(path: string, params?: Record<string, any>): Promise<T> {
    return this.request<T>("GET", path, params);
  }

  private post<T>(path: string, params?: Record<string, any>): Promise<T> {
    return this.request<T>("POST", path, params);
  }

  // ─── Core: See ───

  /** See what's on the screen right now. */
  async see(options?: { includeImage?: boolean; ocr?: boolean }): Promise<Snapshot> {
    return this.get<Snapshot>("/vision/snapshot", {
      include_image: options?.includeImage ?? true,
      ocr: options?.ocr ?? false,
    });
  }

  /** Get the latest frame. */
  async seeFrame(): Promise<Frame> {
    return this.get<Frame>("/frame/latest", { base64: "true" });
  }

  // ─── Core: Read ───

  /** Read text from the screen using OCR. */
  async read(region?: { x: number; y: number; w: number; h: number }): Promise<OCRResult> {
    const params = region
      ? { region_x: region.x, region_y: region.y, region_w: region.w, region_h: region.h }
      : {};
    return this.get<OCRResult>("/vision/ocr", params);
  }

  // ─── Core: Diff ───

  /** See what changed on screen. */
  async whatChanged(): Promise<DiffResult> {
    return this.get<DiffResult>("/vision/diff");
  }

  // ─── Core: Context ───

  /** Get what the user is doing. */
  async whatDoing(): Promise<ContextState> {
    return this.get<ContextState>("/context/state");
  }

  // ─── Core: Audio ───

  /** Get audio level and speech detection. */
  async hear(): Promise<AudioState> {
    return this.get<AudioState>("/audio/level");
  }

  /** Transcribe recent audio. */
  async transcribe(seconds: number = 10): Promise<string> {
    const data = await this.get<{ text: string }>("/audio/transcribe", { seconds });
    return data.text;
  }

  // ─── Core: Annotate ───

  /** Draw an annotation on the screen. */
  async mark(
    x: number, y: number,
    options?: { text?: string; type?: string; width?: number; height?: number; color?: string }
  ): Promise<string> {
    const data = await this.post<{ id: string }>("/annotations/add", {
      type: options?.type ?? "rect",
      x, y,
      width: options?.width ?? 100,
      height: options?.height ?? 50,
      color: options?.color ?? "#FF0000",
      text: options?.text ?? "",
    });
    return data.id;
  }

  /** Clear all annotations. */
  async clearMarks(): Promise<void> {
    await this.post("/annotations/clear");
  }

  // ─── Core: AI ───

  /** Send current screen to an AI provider. */
  async ask(provider: string, prompt: string, providerApiKey: string, model?: string): Promise<AIResponse> {
    return this.post<AIResponse>("/ai/ask", {
      provider,
      prompt,
      provider_api_key: providerApiKey,
      model,
    });
  }

  // ─── Streaming ───

  /**
   * Stream frames via callback.
   * @param callback - Called for each new frame
   * @param fps - Target frames per second
   * @returns Stop function
   */
  watch(callback: (frame: Frame) => void, fps: number = 1): () => void {
    const interval = 1000 / fps;
    let running = true;

    const loop = async () => {
      while (running) {
        try {
          const frame = await this.seeFrame();
          callback(frame);
        } catch (e) {
          // Connection lost, retry
        }
        await new Promise((r) => setTimeout(r, interval));
      }
    };
    loop();

    return () => { running = false; };
  }

  // ─── System ───

  /** Health check. */
  async health(): Promise<HealthStatus> {
    return this.get<HealthStatus>("/health");
  }

  /** Buffer stats. */
  async status(): Promise<BufferStats> {
    return this.get<BufferStats>("/buffer/stats");
  }

  /** Monitor info. */
  async monitors(): Promise<MonitorInfo> {
    return this.get<MonitorInfo>("/monitors");
  }

  /** Change config. */
  async config(options: {
    fps?: number;
    quality?: number;
    image_format?: string;
    max_width?: number;
  }): Promise<void> {
    await this.post("/config", options);
  }

  /** Destroy all visual data. */
  async flush(): Promise<void> {
    await this.post("/buffer/flush");
  }
}

// ─── Default export ───
export default Iluminaty;
