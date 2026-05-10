import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage2.css';

function isOk(rank) {
  if (!rank) return false;
  if (rank.status === 'ok') return true;
  if (rank.status === 'error') return false;
  return typeof rank.ranking === 'string';
}

function shortName(model) {
  if (!model) return '(unknown)';
  return model.split('/')[1] || model;
}

function deAnonymizeText(text, labelToModel) {
  if (!text || !labelToModel) return text || '';
  let result = text;
  Object.entries(labelToModel).forEach(([label, model]) => {
    const modelShortName = shortName(model);
    result = result.replace(new RegExp(label, 'g'), `**${modelShortName}**`);
  });
  return result;
}

function ParseStatusBadge({ status }) {
  if (!status || status === 'ok') return null;
  const cls = status === 'partial' ? 'badge-warn' : 'badge-error';
  const label = status === 'partial' ? 'Partial parse' : 'Parse error';
  return <span className={`stage-badge ${cls}`}>{label}</span>;
}

function ErrorBody({ rank }) {
  const err = rank.error || {};
  return (
    <div className="ranker-error">
      <div className="error-banner">
        <strong>This ranker failed.</strong>{' '}
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
      </dl>
    </div>
  );
}

export default function Stage2({ rankings, labelToModel, aggregateRankings }) {
  const [activeTab, setActiveTab] = useState(0);

  if (!rankings || rankings.length === 0) {
    return null;
  }

  const okCount = rankings.filter(isOk).length;
  const errCount = rankings.length - okCount;
  const partialAggregate = (aggregateRankings || []).some((a) => a.partial);

  const active = rankings[activeTab];

  return (
    <div className="stage stage2">
      <h3 className="stage-title">
        Stage 2: Peer Rankings
        {errCount > 0 && (
          <span className="stage-status-warn">
            {' '}({okCount} of {rankings.length} rankers succeeded)
          </span>
        )}
      </h3>

      <h4>Raw Evaluations</h4>
      <p className="stage-description">
        Each model evaluated all responses (anonymized as Response A, B, C, etc.) and provided rankings.
        Model names are shown in <strong>bold</strong> below for readability; the original evaluation used anonymous labels.
      </p>

      <div className="tabs">
        {rankings.map((rank, index) => {
          const ok = isOk(rank);
          const partial = ok && rank.parse_status === 'partial';
          return (
            <button
              key={index}
              className={`tab ${activeTab === index ? 'active' : ''} ${ok ? '' : 'tab-error'} ${partial ? 'tab-partial' : ''}`}
              onClick={() => setActiveTab(index)}
              title={
                ok
                  ? (partial ? `Partial parse: ${rank.parse_reason || ''}` : '')
                  : `Failed: ${rank.error?.kind || 'unknown'}`
              }
            >
              {!ok && <span className="error-dot" aria-hidden>●</span>}
              {partial && <span className="partial-dot" aria-hidden>◐</span>}
              {shortName(rank.model)}
            </button>
          );
        })}
      </div>

      <div className="tab-content">
        <div className="ranking-model">
          {active.model || '(unknown)'}
          <ParseStatusBadge status={active.parse_status} />
        </div>

        {isOk(active) ? (
          <>
            <div className="ranking-content markdown-content">
              <ReactMarkdown>
                {deAnonymizeText(active.ranking, labelToModel)}
              </ReactMarkdown>
            </div>

            {active.parse_status && active.parse_status !== 'ok' && active.parse_reason && (
              <div className="parse-reason">
                <em>Parse note: {active.parse_reason}</em>
              </div>
            )}

            {active.parsed_ranking && active.parsed_ranking.length > 0 && (
              <div className="parsed-ranking">
                <strong>Extracted Ranking:</strong>
                <ol>
                  {active.parsed_ranking.map((label, i) => (
                    <li key={i}>
                      {labelToModel && labelToModel[label]
                        ? shortName(labelToModel[label])
                        : label}
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </>
        ) : (
          <ErrorBody rank={active} />
        )}
      </div>

      {aggregateRankings && aggregateRankings.length > 0 && (
        <div className="aggregate-rankings">
          <h4>
            Aggregate Rankings (Street Cred)
            {partialAggregate && (
              <span className="stage-badge badge-warn">
                {' '}Partial data
              </span>
            )}
          </h4>
          <p className="stage-description">
            Combined results across all peer evaluations (lower score is better)
            {partialAggregate && (
              <>
                {' '}
                — <strong>note:</strong> at least one ranker produced an
                incomplete ranking, so this aggregate is based on partial data.
              </>
            )}
            :
          </p>
          <div className="aggregate-list">
            {aggregateRankings.map((agg, index) => (
              <div
                key={index}
                className={`aggregate-item ${agg.partial ? 'aggregate-partial' : ''}`}
              >
                <span className="rank-position">#{index + 1}</span>
                <span className="rank-model">{shortName(agg.model)}</span>
                <span className="rank-score">
                  Avg: {Number(agg.average_rank ?? 0).toFixed(2)}
                </span>
                <span className="rank-count">
                  ({agg.rankings_count} votes)
                  {agg.partial && <span className="rank-partial-flag"> · partial</span>}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
