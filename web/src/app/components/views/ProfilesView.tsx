import { useCallback, useEffect, useState } from 'react';
import {
  authClient,
  getActiveProfileId,
  type UserProfile,
} from '@jarvis/auth';
import { LogIn, Plus, ShieldCheck, UserRound } from 'lucide-react';

import { clearOfflineDB } from '@desktop/lib/offline/db';

export default function ProfilesView() {
  const [profiles, setProfiles] = useState<UserProfile[]>([]);
  const [displayName, setDisplayName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeProfileId = getActiveProfileId();
  const isAdministrator = activeProfileId === 'default';

  const load = useCallback(async () => {
    try {
      const response = await authClient.profiles();
      setProfiles(response.profiles);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const createProfile = async () => {
    const name = displayName.trim();
    if (!name || busy) return;
    setBusy(true);
    setError(null);
    try {
      await authClient.createProfile(name);
      setDisplayName('');
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const switchProfile = async (profileId: string) => {
    if (profileId === activeProfileId || busy) return;
    setBusy(true);
    setError(null);
    try {
      await authClient.logout();
      await clearOfflineDB();
      authClient.selectProfile(profileId);
      window.location.assign('/');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-4xl p-4 sm:p-6">
      <header className="mb-6">
        <p className="font-mono text-xs uppercase tracking-wider text-cyan-400">Isolation locale</p>
        <h1 className="mt-1 text-2xl font-semibold text-slate-100">Profils utilisateur</h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-400">
          Chaque profil possède sa propre base SQLite, ses sessions, ses fichiers et son secret de
          déverrouillage. Les données ne sont jamais mélangées entre profils.
        </p>
      </header>

      {error && (
        <p role="alert" className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </p>
      )}

      <section className="grid gap-3 sm:grid-cols-2" data-testid="profile-list">
        {profiles.map((profile) => {
          const active = profile.id === activeProfileId;
          return (
            <article key={profile.id} className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                  <span className="rounded-full border border-white/10 bg-black/30 p-2 text-cyan-300">
                    <UserRound size={18} />
                  </span>
                  <div>
                    <h2 className="font-medium text-slate-100">{profile.display_name}</h2>
                    <p className="font-mono text-[11px] text-slate-500">{profile.id}</p>
                  </div>
                </div>
                {active && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 px-2 py-1 text-[11px] text-emerald-300">
                    <ShieldCheck size={12} /> actif
                  </span>
                )}
              </div>
              {!active && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void switchProfile(profile.id)}
                  className="mt-4 inline-flex items-center gap-2 rounded-lg border border-white/15 px-3 py-2 text-xs text-slate-300 hover:border-cyan-400/50 hover:text-white disabled:opacity-50"
                >
                  <LogIn size={14} /> Ouvrir ce profil
                </button>
              )}
            </article>
          );
        })}
      </section>

      {isAdministrator ? (
        <section className="mt-8 rounded-xl border border-white/10 bg-white/[0.02] p-4">
          <h2 className="text-sm font-semibold text-slate-200">Créer un espace isolé</h2>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              maxLength={80}
              placeholder="Nom du profil"
              aria-label="Nom du nouveau profil"
              className="min-w-0 flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/50"
            />
            <button
              type="button"
              disabled={busy || !displayName.trim()}
              onClick={() => void createProfile()}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-cyan-400 px-4 py-2 text-sm font-medium text-slate-950 disabled:opacity-50"
            >
              <Plus size={15} /> Créer
            </button>
          </div>
        </section>
      ) : (
        <p className="mt-6 text-xs text-slate-500">
          La création et la désactivation de profils sont réservées au profil principal.
        </p>
      )}
    </div>
  );
}
