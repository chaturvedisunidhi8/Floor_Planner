/** Typed fetch wrapper around the FastAPI backend. */

import type {
  ApiError,
  FloorPlanRequirements,
  GeneratedLayout,
  GenerationRequest,
  GenerationResponse,
  OptionsResponse,
  TemplateMatch,
  TemplateSummary,
} from '@/types/api';

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1';

/** An error carrying the backend's structured code, not just a status number. */
export class FloorPlannerApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = 'FloorPlannerApiError';
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new FloorPlannerApiError(
      'Could not reach the server. Is the backend running?',
      'network_error',
      0,
    );
  }

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    let code = 'http_error';
    try {
      const body = (await response.json()) as ApiError & { detail?: unknown };
      if (body.error) {
        message = body.error.message;
        code = body.error.code;
      } else if (body.detail) {
        // FastAPI's own validation errors use `detail`.
        message =
          typeof body.detail === 'string' ? body.detail : 'The request was rejected as invalid.';
        code = 'validation_error';
      }
    } catch {
      /* response had no JSON body; keep the generic message */
    }
    throw new FloorPlannerApiError(message, code, response.status);
  }

  return (await response.json()) as T;
}

export const api = {
  options: () => request<OptionsResponse>('/options'),

  templates: () => request<TemplateSummary[]>('/templates'),

  match: (requirements: FloorPlanRequirements) =>
    request<TemplateMatch[]>('/match', {
      method: 'POST',
      body: JSON.stringify(requirements),
    }),

  generate: (payload: GenerationRequest) =>
    request<GenerationResponse>('/generate', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  session: (sessionId: string) => request<GenerationResponse>(`/sessions/${sessionId}`),

  selectLayout: (layoutId: string) =>
    request<GeneratedLayout>(`/layouts/${layoutId}/select`, { method: 'POST' }),
};

/** Image URLs come back from the API already prefixed with the API path. */
export function imageUrl(layout: GeneratedLayout): string {
  return layout.image_url.startsWith('http')
    ? layout.image_url
    : `${API_BASE.replace(/\/api\/v1$/, '')}${layout.image_url}`;
}
