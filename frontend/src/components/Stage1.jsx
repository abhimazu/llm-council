import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage1.css';

/**
 * Stage 1: per-council-member individual responses.
 *
 * Members may have status="ok" (response present) or status="error"
 * (error object present, response absent). Members from old persisted
 * conversations may have neither field — in that case we treat the
 * presence of `response` as the legacy ok signal.
 */

function isOk(resp) {
  if (!resp) return false;
  if (resp.status === 'ok') return true;
  if (resp.status === 'error') return false;
  // Legacy persisted shape — assume ok if there's a response string.
  return typeof resp.response === 'string';
}

function shortName(model) {
  if (!model) return '(unknown model)';
  return model.split('/')[1] || model;
}

function ErrorBody({ resp }) {
  const err = resp.error || {};
  return (
    <div className="model-error">
      <div className="error-banner">
        <strong>This council member failed.</strong>{' '}
        <span className="error-kind">{err.kind || 'unknown'}</span>
      </div>
      <dl className="error-details">
        <dt>Detail</dt>
        <dd>{err.detail || '(no detail)'}</dd>
        {err.upstream_status != null && (
          <>
            <dt>Upstream HTTP</dt>
            <dd>{err.upstream_status}</dd>
          </>
        )}
        <dt>Retryable</dt>
        <dd>{err.retryable ? 'yes' : 'no'}</dd>
        {err.attempt != null && (
          <>
            <dt>Attempt</dt>
            <dd>{err.attempt}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

export default function Stage1({ responses }) {
  const [activeTab, setActiveTab] = useState(0);

  if (!responses || responses.length === 0) {
    return null;
  }

  const okCount = responses.filter(isOk).length;
  const errCount = responses.length - okCount;

  return (
    <div className="stage stage1">
      <h3 className="stage-title">
        Stage 1: Individual Responses
        {errCount > 0 && (
          <span className="stage-status-warn">
            {' '}({okCount} of {responses.length} succeeded — {errCount}{' '}
            failed)
          </span>
        )}
      </h3>

      <div className="tabs">
        {responses.map((resp, index) => {
          const ok = isOk(resp);
          return (
            <button
              key={index}
              className={`tab ${activeTab === index ? 'active' : ''} ${ok ? '' : 'tab-error'}`}
              onClick={() => setActiveTab(index)}
              title={ok ? '' : `Failed: ${resp.error?.kind || 'unknown'}`}
            >
              {!ok && <span className="error-dot" aria-hidden>●</span>}
              {shortName(resp.model)}
            </button>
          );
        })}
      </div>

      <div className="tab-content">
        <div className="model-name">{responses[activeTab].model || '(unknown)'}</div>
        {isOk(responses[activeTab]) ? (
          <div className="response-text markdown-content">
            <ReactMarkdown>
              {responses[activeTab].response || ''}
            </ReactMarkdown>
          </div>
        ) : (
          <ErrorBody resp={responses[activeTab]} />
        )}
      </div>
    </div>
  );
}
