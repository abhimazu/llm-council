import ReactMarkdown from 'react-markdown';
import './Stage3.css';

// Signature of the legacy-bug fallback string the backend used to
// persist as a "real" assistant answer when the chairman failed.
// We detect this in old persisted conversations and render an error
// state instead of silently showing the misleading text.
const LEGACY_CHAIRMAN_ERROR_STRING = 'Error: Unable to generate final synthesis.';

function getStatus(finalResponse) {
  if (!finalResponse) return 'absent';
  if (finalResponse.status === 'ok') return 'ok';
  if (finalResponse.status === 'error') return 'error';
  // Legacy persisted shape — no `status` field.
  if (typeof finalResponse.response === 'string') {
    if (finalResponse.response.trim() === LEGACY_CHAIRMAN_ERROR_STRING) {
      return 'legacy_error';
    }
    return 'ok';
  }
  return 'absent';
}

function getError(finalResponse, status) {
  if (status === 'legacy_error') {
    return {
      kind: 'legacy_chairman_failure',
      detail: (
        'This response was saved by an earlier version of the council ' +
        'that did not surface chairman failures correctly. The model did ' +
        'not actually say this — the chairman call failed and the system ' +
        'persisted an error string as if it were a real answer.'
      ),
      retryable: false,
    };
  }
  return finalResponse?.error || {};
}

function shortName(model) {
  if (!model) return '(unknown chairman)';
  return model.split('/')[1] || model;
}

export default function Stage3({ finalResponse }) {
  if (!finalResponse) return null;
  const status = getStatus(finalResponse);
  if (status === 'absent') return null;

  if (status === 'ok') {
    return (
      <div className="stage stage3">
        <h3 className="stage-title">Stage 3: Final Council Answer</h3>
        <div className="final-response">
          <div className="chairman-label">
            Chairman: {shortName(finalResponse.model)}
          </div>
          <div className="final-text markdown-content">
            <ReactMarkdown>{finalResponse.response || ''}</ReactMarkdown>
          </div>
        </div>
      </div>
    );
  }

  // Error path (status === 'error' or status === 'legacy_error').
  const err = getError(finalResponse, status);
  const titleSuffix = status === 'legacy_error'
    ? 'Synthesis Failed (legacy)'
    : 'Synthesis Failed';

  return (
    <div className="stage stage3 stage3-error">
      <h3 className="stage-title">Stage 3: {titleSuffix}</h3>
      <div className="final-response final-response-error">
        <div className="chairman-label">
          Chairman: {shortName(finalResponse.model)}
        </div>
        <div className="error-banner">
          <strong>The chairman could not produce a final answer.</strong>
        </div>
        <dl className="error-details">
          <dt>Reason</dt>
          <dd>
            <span className="error-kind">{err.kind || 'unknown'}</span>
            {err.detail ? ` — ${err.detail}` : ''}
          </dd>
          {err.upstream_status != null && (
            <>
              <dt>Upstream HTTP</dt>
              <dd>{err.upstream_status}</dd>
            </>
          )}
          <dt>Retryable</dt>
          <dd>{err.retryable ? 'yes' : 'no'}</dd>
        </dl>
        <p className="error-hint">
          {status === 'legacy_error'
            ? 'Previous Stage 1 and Stage 2 results above (if any) are still valid.'
            : 'Stage 1 and Stage 2 results above (if any) are still valid — they remain the council members\' direct responses to your question.'}
        </p>
      </div>
    </div>
  );
}
