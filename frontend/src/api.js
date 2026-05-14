/**
 * API client for the LLM Council backend.
 *
 * Contract notes (changes branch):
 *  - Stage results carry a per-entry `status` field ("ok" | "error").
 *    Failed entries have `error` populated instead of response/ranking.
 *  - Stage 3 (chairman) carries `status` at the top level. On error,
 *    `response` is null/absent and `error` contains a structured
 *    LLMError ({kind, model, detail, retryable, upstream_status, attempt}).
 *  - Two new SSE event types:
 *      - `title_failed` — title generation didn't produce a title
 *      - `error` — fatal server-side error mid-stream (carries
 *        kind, detail, request_id; never raw exceptions)
 *  - Old persisted conversations from before this branch may have
 *    assistant messages without status fields — components defensively
 *    treat absence of `status` as legacy "ok".
 */

const API_BASE = 'http://localhost:8001';

export const api = {
  async listConversations() {
    const response = await fetch(`${API_BASE}/api/conversations`);
    if (!response.ok) {
      throw new Error('Failed to list conversations');
    }
    return response.json();
  },

  async createConversation() {
    const response = await fetch(`${API_BASE}/api/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!response.ok) {
      throw new Error('Failed to create conversation');
    }
    return response.json();
  },

  async getConversation(conversationId) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}`
    );
    if (!response.ok) {
      throw new Error('Failed to get conversation');
    }
    return response.json();
  },

  async sendMessage(conversationId, content) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      }
    );
    if (!response.ok) {
      throw new Error('Failed to send message');
    }
    return response.json();
  },

  /**
   * Send a message and receive streaming updates.
   *
   * The callback `onEvent(eventType, event)` is called for every SSE
   * event. Known event types:
   *   - stage1_start, stage1_complete (event.data is array of CouncilMemberResult dicts)
   *   - stage2_start, stage2_complete (event.data is array of RankingResult dicts;
   *                                    event.metadata has label_to_model + aggregate_rankings)
   *   - stage3_start, stage3_complete (event.data is ChairmanResult dict)
   *   - title_complete (event.data has title)
   *   - title_failed   (no data; sidebar should refresh anyway)
   *   - error          (event.kind, event.detail, event.request_id)
   *   - complete       (no data; stream done)
   */
  async sendMessageStream(conversationId, content, onEvent) {
    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      }
    );

    if (!response.ok) {
      throw new Error('Failed to send message');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    // Accumulate decoded chunks across reads — SSE events can split
    // across TCP frames. Original code assumed each read contained
    // whole lines; with structured events that occasionally carry
    // long payloads, this caused intermittent JSON.parse errors.
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        // Flush any final buffered line
        if (buffer.startsWith('data: ')) {
          tryDispatch(buffer.slice(6), onEvent);
        }
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let nlIdx;
      while ((nlIdx = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, nlIdx);
        buffer = buffer.slice(nlIdx + 1);
        if (line.startsWith('data: ')) {
          tryDispatch(line.slice(6), onEvent);
        }
      }
    }
  },
};

function tryDispatch(rawData, onEvent) {
  if (!rawData) return;
  try {
    const event = JSON.parse(rawData);
    onEvent(event.type, event);
  } catch (e) {
    console.error('Failed to parse SSE event:', e, rawData);
  }
}
