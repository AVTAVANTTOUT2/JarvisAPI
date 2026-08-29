import type { ReactNode } from 'react'
import type { VoiceSection, VoiceSource, VisualAnswer } from './types'

const certaintyLabels: Record<string, string> = {
  confirmed: 'Confirmé',
  probable: 'Probable',
  estimate: 'Estimation',
  unverified: 'Non vérifié',
  conflicting: 'Sources contradictoires',
}

const statusLabels: Record<string, string> = {
  discovered: 'Détectée',
  fetching: 'Consultation en cours',
  verified: 'Vérifiée',
  used: 'Utilisée',
  rejected: 'Rejetée',
  unavailable: 'Indisponible',
  conflicting: 'Contradictoire',
}

function scalar(value: unknown): string | null {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return null
}

function RecordGrid({ item }: { item: Record<string, unknown> }) {
  const entries = Object.entries(item)
    .filter(([key, value]) => !['id', 'source_id', 'title', 'name'].includes(key) && scalar(value) !== null)
    .slice(0, 5)
  return (
    <dl className="vd-record-grid">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll('_', ' ')}</dt>
          <dd>{scalar(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

function ResultItems({ section, focusIndex }: { section: VoiceSection; focusIndex: number }) {
  const items = Array.isArray(section.data.items) ? section.data.items : []
  return (
    <ol className="vd-results">
      {items.slice(0, 6).map((item, index) => {
        if (!item || typeof item !== 'object') return null
        const record = item as Record<string, unknown>
        return (
          <li key={String(record.id ?? record.source_id ?? index)} className={index === focusIndex ? 'is-focused' : ''}>
            <span className="vd-rank">{index + 1}</span>
            <div>
              <h3>{scalar(record.title) ?? scalar(record.name) ?? `Résultat ${index + 1}`}</h3>
              <RecordGrid item={record} />
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function Comparison({ section }: { section: VoiceSection }) {
  const rows = Array.isArray(section.data.rows) ? section.data.rows : []
  return (
    <div className="vd-comparison" role="table" aria-label={section.title}>
      {rows.slice(0, 8).map((row, index) => (
        row && typeof row === 'object'
          ? <RecordGrid key={index} item={row as Record<string, unknown>} />
          : null
      ))}
    </div>
  )
}

function GenericSection({ section }: { section: VoiceSection }) {
  const text = scalar(section.data.text) ?? scalar(section.data.summary)
  return (
    <section className="vd-section" id={`voice-target-${section.id}`}>
      <p className="vd-eyebrow">{section.title}</p>
      {text ? <p className="vd-section-copy">{text}</p> : <RecordGrid item={section.data} />}
    </section>
  )
}

const sectionLabels: Record<string, string> = {
  calendar: 'Agenda',
  route: 'Itinéraire',
  code: 'Code',
  confirmation: 'Confirmation',
}

function StructuredSection({ section }: { section: VoiceSection }) {
  const items = Array.isArray(section.data.items) ? section.data.items : null
  const code = section.type === 'code'
    ? scalar(section.data.code) ?? scalar(section.data.content) ?? scalar(section.data.text)
    : null
  return (
    <section className={`vd-section vd-section-${section.type}`} id={`voice-target-${section.id}`}>
      <p className="vd-eyebrow">{sectionLabels[section.type] ?? section.title}</p>
      <h2>{section.title}</h2>
      {code ? <pre><code>{code}</code></pre> : items
        ? <ResultItems section={section} focusIndex={-1} />
        : <RecordGrid item={section.data} />}
    </section>
  )
}

export function SourceReader({ source, claims }: { source: VoiceSource; claims: VisualAnswer['claims'] }) {
  const linkedClaims = claims.filter((claim) => claim.source_ids.includes(source.id))
  return (
    <article className="vd-source-reader" aria-label={`Source : ${source.title}`}>
      <header>
        <span className="vd-source-number">SOURCE</span>
        <span className={`vd-source-status status-${source.status}`}>{statusLabels[source.status]}</span>
      </header>
      <h2>{source.title}</h2>
      <p className="vd-source-meta">{[source.provider, source.domain, source.locator].filter(Boolean).join(' · ')}</p>
      {source.excerpt ? <blockquote>{source.excerpt}</blockquote> : <p>Aucun extrait textuel disponible.</p>}
      {linkedClaims.length > 0 && (
        <div className="vd-linked-claims">
          <p className="vd-eyebrow">Informations étayées</p>
          {linkedClaims.map((claim) => <p key={claim.id}>{claim.text}</p>)}
        </div>
      )}
      <p className="vd-reader-hint">Dites « source suivante » ou « reviens aux résultats »</p>
    </article>
  )
}

export function AnswerRenderer({
  answer,
  focus,
  activeTargets,
}: {
  answer: VisualAnswer
  focus?: Record<string, unknown> | null
  activeTargets: Set<string>
}) {
  const sourceId = typeof focus?.source_id === 'string' ? focus.source_id : null
  const sourceIndex = typeof focus?.index === 'number' ? focus.index : -1
  const openSource = sourceId ? answer.sources.find((source) => source.id === sourceId) : null
  if (focus?.view === 'source' && openSource) {
    return <SourceReader source={openSource} claims={answer.claims} />
  }

  const renderSection = (section: VoiceSection): ReactNode => {
    if (section.type === 'ranked_results' || section.type === 'email') {
      return <ResultItems section={section} focusIndex={sourceIndex} />
    }
    if (section.type === 'comparison') return <Comparison section={section} />
    if (['calendar', 'route', 'code', 'confirmation'].includes(section.type)) {
      return <StructuredSection section={section} />
    }
    return <GenericSection section={section} />
  }

  return (
    <div className="vd-answer">
      {answer.sections.filter((section) => section.type !== 'source_list').sort((a, b) => a.order - b.order).map((section) => (
        <div
          key={section.id}
          className={`vd-renderer ${activeTargets.has(section.id) ? 'is-speaking' : ''}`}
        >
          {renderSection(section)}
        </div>
      ))}
      <section className="vd-claims" aria-label="Provenance des informations">
        {answer.claims.map((claim) => (
          <article key={claim.id} className={claim.conflict ? 'is-conflicting' : ''}>
            <p>{claim.text}</p>
            <span>{certaintyLabels[claim.certainty]}{claim.source_ids.length ? ` · ${claim.source_ids.join(', ')}` : ''}</span>
          </article>
        ))}
        {answer.sources.length === 0 && <p className="vd-no-source">Aucune source externe utilisée pour cette réponse.</p>}
      </section>
      {answer.sources.length > 0 && (
        <aside className="vd-source-strip" aria-label="Sources consultées">
          <p className="vd-source-strip-label">Sources consultées · {answer.sources.length}</p>
          {answer.sources.map((source, index) => (
            <div key={source.id} className={source.id === sourceId ? 'is-focused' : ''}>
              <b>{index + 1}</b>
              <span>{source.title}</span>
              <small>{statusLabels[source.status]}</small>
            </div>
          ))}
        </aside>
      )}
    </div>
  )
}
