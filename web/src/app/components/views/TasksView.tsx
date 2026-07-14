import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Calendar,
  CheckCircle2,
  Circle,
  Clock,
  ListTodo,
  Loader2,
  Plus,
  Trash2,
  X,
} from 'lucide-react';
import { api } from '@unified/lib/api';
import { enqueueWrite, isNetworkError } from '@desktop/lib/offline/queue';

// ── Types ────────────────────────────────────────────────────

interface Task {
  id: number;
  title: string;
  description: string | null;
  priority: 'high' | 'medium' | 'low';
  status: 'todo' | 'doing' | 'done';
  due_date: string | null;
  category: string | null;
  created_at: string;
  completed_at: string | null;
  /** Créée hors ligne, en attente de synchronisation (jamais renvoyé par l'API). */
  pendingSync?: boolean;
}

type FilterStatus = 'all' | 'todo' | 'doing' | 'done';

// ── Helpers ──────────────────────────────────────────────────

function relativeDate(iso: string | null | undefined): string {
  if (!iso) return '';
  try {
    const d = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
    if (isNaN(d.getTime())) return iso.slice(0, 10);
    const now = Date.now();
    const diff = now - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "à l'instant";
    if (mins < 60) return `il y a ${mins} min`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `il y a ${hours} h`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `il y a ${days} j`;
    return iso.slice(0, 10);
  } catch {
    return (iso || '').slice(0, 10);
  }
}

const PRIORITY_CONFIG: Record<string, { label: string; color: string; bg: string; border: string; dot: string }> = {
  high:   { label: 'Haute',   color: 'text-red-400',    bg: 'bg-red-400/10',    border: 'border-l-red-400',   dot: 'bg-red-400' },
  medium: { label: 'Moyenne', color: 'text-amber-400',  bg: 'bg-amber-400/10',  border: 'border-l-amber-400', dot: 'bg-amber-400' },
  low:    { label: 'Basse',   color: 'text-sky-400',    bg: 'bg-sky-400/10',    border: 'border-l-sky-400',   dot: 'bg-sky-400' },
};

const STATUS_CONFIG: Record<string, { label: string; icon: typeof Circle }> = {
  todo:  { label: 'À faire', icon: Circle },
  doing: { label: 'En cours', icon: Clock },
  done:  { label: 'Terminé', icon: CheckCircle2 },
};

const STATUS_CYCLE: Task['status'][] = ['todo', 'doing', 'done'];

function nextStatus(current: Task['status']): Task['status'] {
  const idx = STATUS_CYCLE.indexOf(current);
  return STATUS_CYCLE[(idx + 1) % STATUS_CYCLE.length];
}

// ── Composant ────────────────────────────────────────────────

export function TasksView() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterStatus>('all');

  // Création
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newPriority, setNewPriority] = useState<Task['priority']>('medium');
  const [newDueDate, setNewDueDate] = useState('');
  const [newCategory, setNewCategory] = useState('');
  const [creating, setCreating] = useState(false);

  // Suppression toutes
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // ── Chargement ──────────────────────────────────────────────

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const statusParam = filter === 'all' ? undefined : filter;
      const data: any = await api.getTasks(statusParam);
      setTasks(Array.isArray(data?.tasks) ? data.tasks : []);
    } catch (e: any) {
      setError(e?.message || 'Erreur lors du chargement des tâches');
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  // Recharge la liste une fois la file hors-ligne resynchronisée (les tâches
  // "en attente" créées localement laissent place aux vraies entrées serveur.
  useEffect(() => {
    const onSyncDone = () => loadTasks();
    window.addEventListener('jarvis:offline-sync-done', onSyncDone);
    return () => window.removeEventListener('jarvis:offline-sync-done', onSyncDone);
  }, [loadTasks]);

  // ── Actions ─────────────────────────────────────────────────

  const handleStatusCycle = async (task: Task) => {
    const newStatus = nextStatus(task.status);
    const prevTasks = [...tasks];
    setTasks(prev => prev.map(t => t.id === task.id ? { ...t, status: newStatus } : t));
    try {
      await api.updateTask(task.id, newStatus);
    } catch {
      setTasks(prevTasks);
    }
  };

  const handleDelete = async (taskId: number) => {
    const prevTasks = [...tasks];
    setTasks(prev => prev.filter(t => t.id !== taskId));
    try {
      await api.deleteTask(taskId);
    } catch {
      setTasks(prevTasks);
    }
  };

  const handleCreate = async () => {
    const title = newTitle.trim();
    if (!title) return;
    setCreating(true);
    const body = {
      title,
      priority: newPriority,
      due_date: newDueDate || undefined,
      category: newCategory.trim() || undefined,
    };
    try {
      const data: any = await api.createTask(body);
      if (data?.task) {
        setTasks(prev => [data.task, ...prev]);
      }
      setNewTitle('');
      setNewPriority('medium');
      setNewDueDate('');
      setNewCategory('');
      setShowCreate(false);
    } catch (e: any) {
      if (isNetworkError(e)) {
        // Hors ligne : la tâche part dans la file d'attente et sera créée
        // côté serveur au retour réseau — l'utilisateur la voit tout de suite.
        await enqueueWrite({
          method: 'POST',
          path: '/api/tasks',
          body,
          label: `Nouvelle tâche : ${title}`,
        });
        setTasks(prev => [
          {
            id: -Date.now(),
            title,
            description: null,
            priority: newPriority,
            status: 'todo',
            due_date: newDueDate || null,
            category: newCategory.trim() || null,
            created_at: new Date().toISOString(),
            completed_at: null,
            pendingSync: true,
          },
          ...prev,
        ]);
        setNewTitle('');
        setNewPriority('medium');
        setNewDueDate('');
        setNewCategory('');
        setShowCreate(false);
      } else {
        setError(e?.message || 'Erreur lors de la création');
      }
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteAll = async () => {
    setDeleting(true);
    try {
      await api.deleteAllTasks();
      setTasks([]);
      setConfirmDeleteAll(false);
    } catch (e: any) {
      setError(e?.message || 'Erreur lors de la suppression');
    } finally {
      setDeleting(false);
    }
  };

  // ── Stats ───────────────────────────────────────────────────

  const counts = {
    all: tasks.length,
    todo: tasks.filter(t => t.status === 'todo').length,
    doing: tasks.filter(t => t.status === 'doing').length,
    done: tasks.filter(t => t.status === 'done').length,
  };

  // ── Rendu ───────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      {/* ── Header ─────────────────────────────────────────── */}
      <div className="shrink-0 border-b border-white/10 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <ListTodo size={20} className="text-white/60" />
            <h1 className="text-lg font-semibold tracking-tight">Tâches</h1>
          </div>

          <div className="flex items-center gap-2">
            {/* Bouton supprimer tout */}
            <button
              onClick={() => setConfirmDeleteAll(true)}
              disabled={tasks.length === 0}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono
                         border border-red-400/20 text-red-400/70 hover:bg-red-400/10 hover:text-red-400
                         transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              title="Supprimer toutes les tâches"
            >
              <Trash2 size={13} />
              Tout supprimer
            </button>

            {/* Bouton créer */}
            <button
              onClick={() => setShowCreate(v => !v)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono
                         border border-white/15 text-white/70 hover:bg-white/5 hover:text-white
                         transition-colors"
            >
              {showCreate ? <X size={13} /> : <Plus size={13} />}
              {showCreate ? 'Annuler' : 'Nouvelle tâche'}
            </button>
          </div>
        </div>

        {/* ── Filtres ──────────────────────────────────────── */}
        <div className="flex items-center gap-1.5 mt-3">
          {([
            ['all', 'Toutes'],
            ['todo', 'À faire'],
            ['doing', 'En cours'],
            ['done', 'Terminées'],
          ] as [FilterStatus, string][]).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`px-2.5 py-1 rounded-md text-xs font-mono transition-colors border ${
                filter === key
                  ? 'bg-white/10 border-white/20 text-white'
                  : 'border-transparent text-white/40 hover:text-white/70 hover:bg-white/5'
              }`}
            >
              {label}
              <span className="ml-1.5 text-white/30">{counts[key]}</span>
            </button>
          ))}
        </div>

        {/* ── Formulaire création ──────────────────────────── */}
        {showCreate && (
          <div className="mt-3 p-3 rounded-lg border border-white/10 bg-white/[0.03]">
            <div className="flex gap-2">
              <input
                type="text"
                value={newTitle}
                onChange={e => setNewTitle(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleCreate()}
                placeholder="Titre de la tâche..."
                autoFocus
                className="flex-1 bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm
                           text-white placeholder:text-white/30 focus:outline-none focus:border-white/30"
              />
              <button
                onClick={handleCreate}
                disabled={!newTitle.trim() || creating}
                className="px-4 py-2 rounded-lg bg-white text-black text-xs font-mono
                           hover:bg-white/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {creating ? <Loader2 size={14} className="animate-spin" /> : 'Créer'}
              </button>
            </div>

            <div className="flex gap-2 mt-2">
              {/* Priorité */}
              <select
                value={newPriority}
                onChange={e => setNewPriority(e.target.value as Task['priority'])}
                className="bg-black/30 border border-white/10 rounded-lg px-3 py-1.5 text-xs
                           text-white/70 focus:outline-none focus:border-white/30"
              >
                <option value="high">Haute priorité</option>
                <option value="medium">Priorité moyenne</option>
                <option value="low">Basse priorité</option>
              </select>

              {/* Date d'échéance */}
              <input
                type="date"
                value={newDueDate}
                onChange={e => setNewDueDate(e.target.value)}
                className="bg-black/30 border border-white/10 rounded-lg px-3 py-1.5 text-xs
                           text-white/70 focus:outline-none focus:border-white/30"
              />

              {/* Catégorie */}
              <input
                type="text"
                value={newCategory}
                onChange={e => setNewCategory(e.target.value)}
                placeholder="Catégorie"
                className="bg-black/30 border border-white/10 rounded-lg px-3 py-1.5 text-xs
                           text-white/70 placeholder:text-white/20 focus:outline-none focus:border-white/30"
              />
            </div>
          </div>
        )}
      </div>

      {/* ── Contenu ────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={24} className="animate-spin text-white/30" />
          </div>
        )}

        {/* Erreur */}
        {!loading && error && (
          <div className="flex items-center gap-2 p-4 rounded-lg border border-red-400/20 bg-red-400/5 text-red-400 text-sm">
            <AlertTriangle size={16} />
            {error}
            <button
              onClick={loadTasks}
              className="ml-auto text-xs underline hover:text-red-300"
            >
              Réessayer
            </button>
          </div>
        )}

        {/* Vide */}
        {!loading && !error && tasks.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-white/25">
            <ListTodo size={40} className="mb-3" />
            <p className="text-sm">
              {filter === 'all' ? 'Aucune tâche' : `Aucune tâche "${STATUS_CONFIG[filter]?.label || filter}"`}
            </p>
            <button
              onClick={() => setShowCreate(true)}
              className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs
                         border border-white/10 text-white/40 hover:text-white/70 hover:border-white/20 transition-colors"
            >
              <Plus size={13} />
              Créer une tâche
            </button>
          </div>
        )}

        {/* Liste */}
        {!loading && !error && tasks.length > 0 && (
          <div className="space-y-1.5">
            {tasks.map(task => {
              const p = PRIORITY_CONFIG[task.priority] || PRIORITY_CONFIG.medium;
              const s = STATUS_CONFIG[task.status];
              const isDone = task.status === 'done';
              const StatusIcon = s.icon;

              return (
                <div
                  key={task.id}
                  className={`group flex items-center gap-3 px-3 py-2.5 rounded-lg border border-l-2
                              ${p.border} border-white/5 ${p.bg}
                              hover:border-white/10 transition-colors
                              ${isDone ? 'opacity-60' : ''}`}
                >
                  {/* Status toggle */}
                  <button
                    onClick={() => handleStatusCycle(task)}
                    className="shrink-0 p-0.5 rounded-full transition-colors hover:bg-white/10"
                    title={`Statut: ${s.label} — cliquer pour passer à "${STATUS_CONFIG[nextStatus(task.status)]?.label}"`}
                  >
                    <StatusIcon
                      size={18}
                      className={isDone ? 'text-emerald-400' : 'text-white/40 group-hover:text-white/70'}
                    />
                  </button>

                  {/* Contenu */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`text-sm truncate ${isDone ? 'line-through text-white/40' : 'text-white/90'}`}>
                        {task.title}
                      </span>
                      {task.pendingSync && (
                        <span
                          className="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-500/10 text-amber-400"
                          title="Créée hors ligne — sera synchronisée au retour du réseau"
                        >
                          en attente
                        </span>
                      )}
                      {task.category && (
                        <span className="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-mono bg-white/5 text-white/30">
                          {task.category}
                        </span>
                      )}
                    </div>

                    {(task.description || task.due_date) && (
                      <div className="flex items-center gap-3 mt-0.5">
                        {task.description && (
                          <p className="text-xs text-white/30 truncate max-w-md">
                            {task.description}
                          </p>
                        )}
                        {task.due_date && (
                          <span className="inline-flex items-center gap-1 text-[11px] text-white/30 shrink-0">
                            <Calendar size={11} />
                            {relativeDate(task.due_date)}
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Priorité badge */}
                  <span className={`shrink-0 px-2 py-0.5 rounded text-[10px] font-mono ${p.color} ${p.bg} border border-current/10`}>
                    {p.label}
                  </span>

                  {/* Delete */}
                  <button
                    onClick={() => handleDelete(task.id)}
                    className="shrink-0 p-1 rounded opacity-0 group-hover:opacity-100 transition-all
                               hover:bg-red-400/10 text-white/20 hover:text-red-400"
                    title="Supprimer cette tâche"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Modal confirmation "Tout supprimer" ────────────── */}
      {confirmDeleteAll && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-sm mx-4 p-6 rounded-2xl border border-red-400/20 bg-[#0d1117]/95 shadow-2xl">
            <div className="flex items-start gap-3">
              <div className="p-2 rounded-full bg-red-400/10">
                <AlertTriangle size={20} className="text-red-400" />
              </div>
              <div className="flex-1">
                <h3 className="text-sm font-semibold text-white">
                  Supprimer toutes les tâches ?
                </h3>
                <p className="mt-1 text-xs text-white/50">
                  Cette action est irréversible. Les {counts.all} tâche{counts.all > 1 ? 's' : ''} seront définitivement supprimées.
                </p>
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button
                onClick={() => setConfirmDeleteAll(false)}
                disabled={deleting}
                className="px-4 py-2 rounded-lg text-xs font-mono text-white/50
                           hover:text-white/70 hover:bg-white/5 transition-colors"
              >
                Annuler
              </button>
              <button
                onClick={handleDeleteAll}
                disabled={deleting}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-mono
                           bg-red-500/20 text-red-400 border border-red-400/20
                           hover:bg-red-500/30 transition-colors disabled:opacity-40"
              >
                {deleting ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Trash2 size={13} />
                )}
                Tout supprimer
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default TasksView;
