import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Upload,
  FileText,
  Mic,
  Database,
  Download,
  LayoutGrid,
  List,
  ChevronDown,
  ChevronUp,
  X,
  Loader2,
  CheckCircle,
  AlertCircle,
} from 'lucide-react';
import { api } from '@unified/lib/api';
import { timeAgo, formatDurationSec } from '@desktop/app/lib/timeFormat';

// ── Types ────────────────────────────────────────────────────

interface OutputFile {
  filename: string;
  subject?: string;
  path: string;
  size_kb: number;
  created_at: string;
}

interface SchoolDoc {
  id: number;
  title: string;
  doc_type?: string;
  file_path?: string;
  content_length?: number;
  created_at: string;
}

interface Recording {
  id: number;
  label?: string;
  title?: string;
  duration_seconds?: number;
  summary?: string;
  created_at: string;
}

interface RecordingDetail extends Recording {
  transcription?: string;
  synthesis?: string;
  actions_taken?: string | Record<string, unknown>;
}

interface UploadResult {
  name: string;
  error?: boolean;
  content_length?: number;
  doc_type?: string;
}

// ── Helpers ──────────────────────────────────────────────────

function formatSize(kb: number): string {
  if (!kb || kb < 0) return '—';
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}


function docTypeIcon(type: string | undefined): string {
  const map: Record<string, string> = {
    cours: '📚', exercice: '✏️', devoir: '📝', fiche: '📋',
    pdf: '📄', md: '📝', txt: '📄', autre: '📄',
  };
  return map[type?.toLowerCase() ?? ''] ?? '📄';
}

function fileIcon(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, string> = {
    pdf: '📄', md: '📝', txt: '📄', docx: '📃',
    xlsx: '📊', csv: '📊', json: '⚙️', py: '🐍',
    js: '📜', ts: '📜', html: '🌐', css: '🎨',
  };
  return map[ext] ?? '📄';
}

function parseActions(actions: string | Record<string, unknown> | undefined): string {
  if (!actions) return '';
  if (typeof actions === 'string') {
    try {
      const p = JSON.parse(actions) as Record<string, unknown>;
      return summariseActions(p);
    } catch {
      return actions.slice(0, 80);
    }
  }
  return summariseActions(actions);
}

function summariseActions(obj: Record<string, unknown>): string {
  const parts: string[] = [];
  const tasks = (obj.tasks_created ?? obj.tasks) as unknown[] | undefined;
  const events = (obj.events_created ?? obj.events) as unknown[] | undefined;
  const facts = (obj.facts_added ?? obj.facts) as unknown[] | undefined;
  if (Array.isArray(tasks) && tasks.length > 0) parts.push(`${tasks.length} tâche${tasks.length > 1 ? 's' : ''}`);
  if (Array.isArray(events) && events.length > 0) parts.push(`${events.length} événement${events.length > 1 ? 's' : ''}`);
  if (Array.isArray(facts) && facts.length > 0) parts.push(`${facts.length} fait${facts.length > 1 ? 's' : ''}`);
  return parts.join(' • ');
}

// ── Sous-composants ───────────────────────────────────────────

function SectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <h2 className="font-mono text-xs uppercase tracking-widest text-muted-foreground">{title}</h2>
      <span className="px-2 py-0.5 rounded-full bg-white/8 border border-white/10 font-mono text-xs">
        {count}
      </span>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <p className="text-sm text-muted-foreground py-4 font-mono">{message}</p>
  );
}

// ── Composant principal ──────────────────────────────────────

export function DocumentsView() {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [outputs, setOutputs] = useState<OutputFile[]>([]);
  const [schoolDocs, setSchoolDocs] = useState<SchoolDoc[]>([]);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [uploading, setUploading] = useState(false);
  const [uploadResults, setUploadResults] = useState<UploadResult[]>([]);
  const [dragOver, setDragOver] = useState(false);

  const [expandedRecId, setExpandedRecId] = useState<number | null>(null);
  const [recordingDetail, setRecordingDetail] = useState<RecordingDetail | null>(null);
  const [recDetailLoading, setRecDetailLoading] = useState(false);
  const [transcriptOpen, setTranscriptOpen] = useState(false);

  // ── Chargement ────────────────────────────────────────────

  const loadDocuments = useCallback(async () => {
    try {
      const [outputsRes, memoryRes, recordingsRes] = await Promise.all([
        api.getOutputs() as Promise<{ files: OutputFile[] }>,
        api.getMemory() as Promise<{ school_documents?: SchoolDoc[] }>,
        api.getRecordings() as Promise<Recording[]>,
      ]);
      setOutputs(outputsRes.files ?? []);
      setSchoolDocs(memoryRes.school_documents ?? []);
      setRecordings(Array.isArray(recordingsRes) ? recordingsRes : []);
    } catch (e) {
      console.error('[DocumentsView] loadDocuments:', e);
    }
  }, []);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  // ── Upload ────────────────────────────────────────────────

  const handleUpload = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    setUploading(true);
    const results: UploadResult[] = [];
    for (const file of list) {
      try {
        const result = (await api.uploadFile(file)) as { content_length?: number; doc_type?: string };
        results.push({ name: file.name, ...result });
      } catch {
        results.push({ name: file.name, error: true });
      }
    }
    setUploadResults(results);
    setUploading(false);
    void loadDocuments();
    setTimeout(() => setUploadResults([]), 5000);
  }, [loadDocuments]);

  // ── Drag & drop ───────────────────────────────────────────

  const onDragOver = (e: React.DragEvent) => { e.preventDefault(); setDragOver(true); };
  const onDragLeave = () => setDragOver(false);
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    void handleUpload(e.dataTransfer.files);
  };

  // ── Détail enregistrement ─────────────────────────────────

  const openRecording = useCallback(async (id: number) => {
    if (expandedRecId === id) {
      setExpandedRecId(null);
      setRecordingDetail(null);
      setTranscriptOpen(false);
      return;
    }
    setExpandedRecId(id);
    setRecordingDetail(null);
    setTranscriptOpen(false);
    setRecDetailLoading(true);
    try {
      const detail = (await api.getRecording(id)) as RecordingDetail;
      setRecordingDetail(detail);
    } catch (e) {
      console.error(e);
    } finally {
      setRecDetailLoading(false);
    }
  }, [expandedRecId]);

  // ── Statistiques ──────────────────────────────────────────

  const totalSizeKb = [
    ...outputs.map((f) => f.size_kb ?? 0),
    ...schoolDocs.map((d) => (d.content_length ?? 0) / 1024),
  ].reduce((a, b) => a + b, 0);

  const totalFiles = outputs.length + schoolDocs.length + recordings.length;
  const totalMB = totalSizeKb / 1024;

  // ── Rendu ─────────────────────────────────────────────────

  return (
    <div className="flex-1 p-6 overflow-y-auto space-y-8">
      {/* ── Header ── */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-sm font-bold tracking-widest uppercase">Documents</h1>
          <p className="font-mono text-xs text-muted-foreground mt-0.5">
            Gestion des fichiers et enregistrements
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs text-muted-foreground">
            {totalFiles} fichier{totalFiles > 1 ? 's' : ''} • {totalMB.toFixed(1)} MB
          </span>
          <div className="flex gap-1">
            <button
              onClick={() => setViewMode('grid')}
              className={`w-8 h-8 rounded-lg flex items-center justify-center border transition-colors ${
                viewMode === 'grid' ? 'bg-white text-black border-white' : 'bg-white/5 border-white/10 hover:bg-white/10 text-muted-foreground'
              }`}
              aria-label="Vue grille"
            >
              <LayoutGrid className="w-4 h-4" />
            </button>
            <button
              onClick={() => setViewMode('list')}
              className={`w-8 h-8 rounded-lg flex items-center justify-center border transition-colors ${
                viewMode === 'list' ? 'bg-white text-black border-white' : 'bg-white/5 border-white/10 hover:bg-white/10 text-muted-foreground'
              }`}
              aria-label="Vue liste"
            >
              <List className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* ── Zone d'upload ── */}
      <div>
        <div
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onClick={() => !uploading && fileInputRef.current?.click()}
          className={`rounded-2xl border-2 border-dashed transition-all cursor-pointer flex flex-col items-center justify-center gap-3 py-10 px-6 ${
            dragOver
              ? 'border-white/60 bg-white/5 shadow-[0_0_30px_rgba(255,255,255,0.05)]'
              : 'border-white/15 hover:border-white/30 hover:bg-white/3'
          } ${uploading ? 'pointer-events-none opacity-70' : ''}`}
        >
          <input
            ref={fileInputRef}
            type="file"
            hidden
            multiple
            accept=".pdf,.txt,.md,.png,.jpg,.jpeg,.csv,.json,.py,.js,.ts"
            onChange={(e) => e.target.files && void handleUpload(e.target.files)}
          />
          {uploading ? (
            <>
              <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
              <p className="font-mono text-sm text-muted-foreground">Upload en cours...</p>
            </>
          ) : (
            <>
              <Upload className={`w-8 h-8 transition-colors ${dragOver ? 'text-white' : 'text-muted-foreground'}`} />
              <p className={`text-xs font-bold tracking-widest uppercase transition-colors ${dragOver ? 'text-white' : 'text-muted-foreground'}`}>
                Déposer un fichier ou cliquer
              </p>
              <p className="font-mono text-xs text-muted-foreground/60">
                PDF, TXT, MD, PNG, JPG acceptés
              </p>
            </>
          )}
        </div>

        {/* Résultats upload */}
        {uploadResults.length > 0 && (
          <div className="mt-3 space-y-1.5">
            {uploadResults.map((r, i) => (
              <div
                key={i}
                className={`flex items-center gap-2 px-3 py-2 rounded-xl border text-sm ${
                  r.error
                    ? 'bg-red-500/8 border-red-500/20 text-red-400'
                    : 'bg-white/5 border-white/10'
                }`}
              >
                {r.error
                  ? <AlertCircle className="w-4 h-4 shrink-0 text-red-400" />
                  : <CheckCircle className="w-4 h-4 shrink-0 text-white/70" />
                }
                <span className="font-mono text-xs flex-1 truncate">{r.name}</span>
                {!r.error && r.content_length !== undefined && (
                  <span className="font-mono text-xs text-muted-foreground shrink-0">
                    {r.content_length.toLocaleString('fr-FR')} chars
                  </span>
                )}
                {r.error && <span className="font-mono text-xs shrink-0">Erreur</span>}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Stats rapides ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { icon: FileText, label: 'Fichiers produits', value: outputs.length },
          { icon: Upload, label: 'Docs uploadés', value: schoolDocs.length },
          { icon: Mic, label: 'Enregistrements', value: recordings.length },
          { icon: Database, label: 'Stockage', value: formatSize(totalSizeKb) },
        ].map(({ icon: Icon, label, value }) => (
          <div key={label} className="glass-panel rounded-xl p-4 border border-white/10 flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center shrink-0">
              <Icon className="w-4 h-4 text-muted-foreground" />
            </div>
            <div>
              <p className="font-mono text-xs text-muted-foreground leading-tight">{label}</p>
              <p className="font-bold text-base leading-tight mt-0.5">{value}</p>
            </div>
          </div>
        ))}
      </div>

      {/* ── Fichiers produits ── */}
      <section>
        <SectionHeader title="Fichiers produits" count={outputs.length} />
        {outputs.length === 0 ? (
          <EmptyState message="Aucun fichier produit. Demande à JARVIS de faire un exercice pour générer un fichier." />
        ) : viewMode === 'grid' ? (
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
            {outputs.map((file, i) => (
              <div
                key={i}
                className="glass-panel rounded-xl p-4 border border-transparent hover:border-white/20 transition-all group"
              >
                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xl shrink-0">{fileIcon(file.filename)}</span>
                    <p className="text-sm truncate" title={file.filename}>{file.filename}</p>
                  </div>
                  <a
                    href={api.getOutputUrl(file.path)}
                    download={file.filename}
                    onClick={(e) => e.stopPropagation()}
                    className="w-7 h-7 rounded-lg bg-white/5 hover:bg-white/15 border border-white/10 flex items-center justify-center shrink-0 transition-colors opacity-0 group-hover:opacity-100"
                    aria-label="Télécharger"
                  >
                    <Download className="w-3.5 h-3.5" />
                  </a>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  {file.subject && (
                    <span className="px-2 py-0.5 rounded-full bg-white/8 border border-white/10 font-mono text-xs">
                      {file.subject}
                    </span>
                  )}
                  <span className="font-mono text-xs text-muted-foreground">{formatSize(file.size_kb)}</span>
                  <span className="font-mono text-xs text-muted-foreground ml-auto">{timeAgo(file.created_at)}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="glass-panel rounded-xl border border-white/10 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10">
                  <th className="text-left px-4 py-2.5 font-mono text-xs text-muted-foreground uppercase tracking-wider">Nom</th>
                  <th className="text-left px-4 py-2.5 font-mono text-xs text-muted-foreground uppercase tracking-wider hidden sm:table-cell">Matière</th>
                  <th className="text-right px-4 py-2.5 font-mono text-xs text-muted-foreground uppercase tracking-wider">Taille</th>
                  <th className="text-right px-4 py-2.5 font-mono text-xs text-muted-foreground uppercase tracking-wider hidden sm:table-cell">Date</th>
                  <th className="px-4 py-2.5 w-10" />
                </tr>
              </thead>
              <tbody>
                {outputs.map((file, i) => (
                  <tr key={i} className="border-b border-white/5 last:border-0 hover:bg-white/3 transition-colors">
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className="text-base shrink-0">{fileIcon(file.filename)}</span>
                        <span className="truncate max-w-xs" title={file.filename}>{file.filename}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 hidden sm:table-cell">
                      {file.subject && (
                        <span className="px-2 py-0.5 rounded-full bg-white/8 border border-white/10 font-mono text-xs">{file.subject}</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs text-muted-foreground">{formatSize(file.size_kb)}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs text-muted-foreground hidden sm:table-cell">{timeAgo(file.created_at)}</td>
                    <td className="px-4 py-2.5">
                      <a
                        href={api.getOutputUrl(file.path)}
                        download={file.filename}
                        className="w-7 h-7 rounded-lg bg-white/5 hover:bg-white/15 border border-white/10 flex items-center justify-center transition-colors"
                        aria-label="Télécharger"
                      >
                        <Download className="w-3.5 h-3.5" />
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Documents uploadés ── */}
      <section>
        <SectionHeader title="Documents chargés" count={schoolDocs.length} />
        {schoolDocs.length === 0 ? (
          <EmptyState message="Aucun document uploadé. Dépose un PDF ou un fichier texte dans la zone ci-dessus." />
        ) : viewMode === 'grid' ? (
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
            {schoolDocs.map((doc) => (
              <div key={doc.id} className="glass-panel rounded-xl p-4 border border-transparent hover:border-white/20 transition-all">
                <div className="flex items-start gap-2 mb-3">
                  <span className="text-xl shrink-0">{docTypeIcon(doc.doc_type)}</span>
                  <p className="text-sm truncate flex-1" title={doc.title}>{doc.title}</p>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  {doc.doc_type && (
                    <span className="px-2 py-0.5 rounded-full bg-white/8 border border-white/10 font-mono text-xs capitalize">
                      {doc.doc_type}
                    </span>
                  )}
                  {doc.content_length !== undefined && doc.content_length > 0 && (
                    <span className="font-mono text-xs text-muted-foreground">
                      {doc.content_length.toLocaleString('fr-FR')} chars
                    </span>
                  )}
                  <span className="font-mono text-xs text-muted-foreground ml-auto">{timeAgo(doc.created_at)}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="glass-panel rounded-xl border border-white/10 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10">
                  <th className="text-left px-4 py-2.5 font-mono text-xs text-muted-foreground uppercase tracking-wider">Titre</th>
                  <th className="text-left px-4 py-2.5 font-mono text-xs text-muted-foreground uppercase tracking-wider hidden sm:table-cell">Type</th>
                  <th className="text-right px-4 py-2.5 font-mono text-xs text-muted-foreground uppercase tracking-wider hidden sm:table-cell">Taille</th>
                  <th className="text-right px-4 py-2.5 font-mono text-xs text-muted-foreground uppercase tracking-wider">Date</th>
                </tr>
              </thead>
              <tbody>
                {schoolDocs.map((doc) => (
                  <tr key={doc.id} className="border-b border-white/5 last:border-0 hover:bg-white/3 transition-colors">
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className="text-base shrink-0">{docTypeIcon(doc.doc_type)}</span>
                        <span className="truncate max-w-xs">{doc.title}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 hidden sm:table-cell">
                      {doc.doc_type && (
                        <span className="px-2 py-0.5 rounded-full bg-white/8 border border-white/10 font-mono text-xs capitalize">{doc.doc_type}</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs text-muted-foreground hidden sm:table-cell">
                      {doc.content_length ? `${doc.content_length.toLocaleString('fr-FR')} ch` : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs text-muted-foreground">{timeAgo(doc.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Enregistrements ── */}
      <section className="pb-6">
        <SectionHeader title="Enregistrements" count={recordings.length} />
        {recordings.length === 0 ? (
          <EmptyState message="Aucun enregistrement. Utilise le mode écoute continue sur la page Voix pour enregistrer un cours ou une réunion." />
        ) : (
          <div className="space-y-2">
            {recordings.map((rec) => {
              const isOpen = expandedRecId === rec.id;
              const displayTitle = rec.title ?? rec.label ?? `Enregistrement #${rec.id}`;
              return (
                <div
                  key={rec.id}
                  className={`glass-panel rounded-xl border transition-all ${isOpen ? 'border-white/25' : 'border-white/8 hover:border-white/15'}`}
                >
                  {/* En-tête cliquable */}
                  <button
                    onClick={() => void openRecording(rec.id)}
                    className="w-full flex items-center gap-3 p-4 text-left"
                  >
                    <div className="w-10 h-10 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center shrink-0">
                      <Mic className="w-4 h-4 text-muted-foreground" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{displayTitle}</p>
                      <div className="flex items-center gap-3 mt-0.5">
                        <span className="font-mono text-xs text-muted-foreground">
                          {formatDurationSec(rec.duration_seconds)}
                        </span>
                        <span className="font-mono text-xs text-muted-foreground">
                          {timeAgo(rec.created_at)}
                        </span>
                        {rec.summary && (
                          <span className="text-xs text-muted-foreground truncate hidden sm:block">
                            {rec.summary.slice(0, 80)}{rec.summary.length > 80 ? '…' : ''}
                          </span>
                        )}
                      </div>
                    </div>
                    {isOpen
                      ? <ChevronUp className="w-4 h-4 text-muted-foreground shrink-0" />
                      : <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0" />
                    }
                  </button>

                  {/* Panel détail expand */}
                  {isOpen && (
                    <div className="px-4 pb-4 border-t border-white/8 pt-4 space-y-4">
                      {recDetailLoading ? (
                        <div className="flex items-center gap-2 text-muted-foreground">
                          <Loader2 className="w-4 h-4 animate-spin" />
                          <span className="text-sm font-mono">Chargement…</span>
                        </div>
                      ) : recordingDetail?.id === rec.id ? (
                        <>
                          {/* Résumé */}
                          {recordingDetail.summary && (
                            <div>
                              <p className="font-mono text-xs text-muted-foreground uppercase tracking-wider mb-1.5">Résumé</p>
                              <p className="text-sm leading-relaxed">{recordingDetail.summary}</p>
                            </div>
                          )}

                          {/* Synthèse / actions */}
                          {recordingDetail.synthesis && (
                            <div>
                              <p className="font-mono text-xs text-muted-foreground uppercase tracking-wider mb-1.5">Synthèse</p>
                              <p className="text-sm leading-relaxed">{recordingDetail.synthesis}</p>
                            </div>
                          )}

                          {/* Actions effectuées */}
                          {recordingDetail.actions_taken && (() => {
                            const summary = parseActions(recordingDetail.actions_taken);
                            if (!summary) return null;
                            return (
                              <div className="flex flex-wrap gap-2">
                                {summary.split(' • ').map((part, i) => (
                                  <span key={i} className="px-2 py-1 rounded-lg bg-white/8 border border-white/10 font-mono text-xs">
                                    {part}
                                  </span>
                                ))}
                              </div>
                            );
                          })()}

                          {/* Transcription (repliable) */}
                          {recordingDetail.transcription && (
                            <div>
                              <button
                                onClick={() => setTranscriptOpen((v) => !v)}
                                className="flex items-center gap-2 font-mono text-xs text-muted-foreground hover:text-white transition-colors mb-2"
                              >
                                {transcriptOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                                {transcriptOpen ? 'Masquer la transcription' : 'Voir la transcription'}
                              </button>
                              {transcriptOpen && (
                                <div className="rounded-xl bg-white/3 border border-white/8 p-4 max-h-96 overflow-y-auto">
                                  <p className="text-xs leading-relaxed whitespace-pre-wrap font-mono text-muted-foreground">
                                    {recordingDetail.transcription}
                                  </p>
                                </div>
                              )}
                            </div>
                          )}

                          {/* Bouton fermer */}
                          <button
                            onClick={() => { setExpandedRecId(null); setRecordingDetail(null); setTranscriptOpen(false); }}
                            className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground hover:text-white transition-colors"
                          >
                            <X className="w-3.5 h-3.5" />
                            Fermer
                          </button>
                        </>
                      ) : null}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
