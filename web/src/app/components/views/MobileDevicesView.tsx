import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Check, Copy, MapPin, Mic, RefreshCw, Smartphone, Trash2, Wifi } from 'lucide-react';
import { api } from '@unified/lib/api';

type MobileDevice = Awaited<ReturnType<typeof api.getMobileDevices>>['devices'][number];

export default function MobileDevicesView() {
  const [devices, setDevices] = useState<MobileDevice[]>([]);
  const [pairing, setPairing] = useState<{ code: string; expires_at: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setError('');
      setDevices((await api.getMobileDevices()).devices);
    } catch {
      setError('Impossible de charger les téléphones appairés.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const generate = async () => {
    try {
      setError('');
      setPairing(await api.startMobilePairing());
    } catch {
      setError('Impossible de générer le code de pairage.');
    }
  };

  const revoke = async (device: MobileDevice) => {
    if (!window.confirm(`Révoquer ${device.name} ? L’application devra être appairée à nouveau.`)) return;
    await api.revokeMobileDevice(device.device_id);
    await refresh();
  };

  const copyCode = async () => {
    if (!pairing) return;
    await navigator.clipboard.writeText(pairing.code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <section className="mx-auto w-full max-w-4xl p-4 sm:p-8 space-y-6">
      <header>
        <p className="text-xs font-mono uppercase tracking-[0.2em] text-muted-foreground">Compagnon Android</p>
        <h1 className="mt-2 text-2xl font-semibold">Téléphones JARVIS</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Le code relie l’application native à ce JARVIS sans transmettre ton secret de déverrouillage.
        </p>
      </header>

      <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="font-medium">Appairer un Galaxy</h2>
            <p className="mt-1 text-xs text-muted-foreground">Code à six chiffres, valable dix minutes et une seule fois.</p>
          </div>
          <button onClick={() => void generate()} className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-black">
            Générer un code
          </button>
        </div>
        {pairing && (
          <button
            onClick={() => void copyCode()}
            className="mt-5 flex w-full items-center justify-center gap-3 rounded-2xl border border-sky-400/30 bg-sky-400/10 px-4 py-5"
          >
            <span className="font-mono text-4xl tracking-[0.3em] text-sky-200">{pairing.code}</span>
            {copied ? <Check size={20} /> : <Copy size={20} />}
          </button>
        )}
      </div>

      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">Appareils connus</h2>
        <button onClick={() => void refresh()} aria-label="Actualiser" className="rounded-lg border border-white/10 p-2 text-muted-foreground">
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {error && <p className="rounded-xl border border-red-400/20 bg-red-400/10 p-3 text-sm text-red-200">{error}</p>}
      {!loading && devices.length === 0 && (
        <div className="rounded-2xl border border-dashed border-white/15 p-8 text-center text-sm text-muted-foreground">
          Aucun téléphone appairé.
        </div>
      )}
      <div className="space-y-3">
        {devices.map(device => (
          <article key={device.device_id} className={`rounded-2xl border p-4 ${device.revoked ? 'border-white/5 opacity-50' : 'border-white/10 bg-white/[0.02]'}`}>
            <div className="flex items-start gap-3">
              <div className="rounded-xl bg-white/5 p-3"><Smartphone size={20} /></div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-medium">{device.name}</h3>
                  {device.revoked && <span className="rounded bg-white/10 px-2 py-0.5 text-[10px] uppercase">Révoqué</span>}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{device.model} · app {device.app_version || 'inconnue'}</p>
                <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                  <Badge active={device.push_enabled} icon={<Wifi size={12} />} label="Push" />
                  <Badge active={Boolean(device.capabilities.background_location)} icon={<MapPin size={12} />} label="GPS H24" />
                  <Badge active={Boolean(device.capabilities.wake_word)} icon={<Mic size={12} />} label="Mot-clé" />
                </div>
              </div>
              {!device.revoked && (
                <button onClick={() => void revoke(device)} aria-label={`Révoquer ${device.name}`} className="rounded-lg p-2 text-red-300 hover:bg-red-400/10">
                  <Trash2 size={16} />
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function Badge({ active, icon, label }: { active: boolean; icon: ReactNode; label: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 ${active ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200' : 'border-white/10'}`}>
      {icon}{label}
    </span>
  );
}
