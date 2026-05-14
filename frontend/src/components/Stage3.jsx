import ReactMarkdown from 'react-markdown';
import './Stage3.css';

function getStatus(finalResponse) {
  if (!finalResponse) return 'absent';
  if (finalResponse.status === 'ok') return 'ok';
  if (finalResponse.status === 'error') return 'error';
  // Legacy persisted shape — no `status` field. Treat as ok if there's
  // a response string. Old conversations render as before; new
  // conversations use the structured-status path. Removing the legacy
  // detector means old conversations with the misleading
  // 'Error: Unable to generate final synthesis.' string will render
  // that string as if it were a real chairman answer; this matches
  // the original (pre-refactor) behavior on those documents.
  if (typeof finalResponse.response === 'string') return 'ok';
  return 'absent';
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

  // Error path (status === 'error').
  const err = finalResponse.error || {};

  return (
    <div className="stage stage3 stage3-error">
      <h3 className="stage-title">Stage 3: Synthesis Failed</h3>
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
          Stage 1 and Stage 2 results above (if any) are still valid —
          they remain the council members' direct responses to your question.
        </p>
      </div>
    </div>
  );
}
