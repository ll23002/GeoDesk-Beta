import React, { useState, useEffect, useRef, useCallback } from 'react';
import api from '../services/api';

interface JobStatus {
  job_id: string;
  status: 'queued' | 'started' | 'progress' | 'completed' | 'failed';
  step: string;
  progress: number;
  updated_at: string;
  error: string | null;
  result: Record<string, unknown> | null;
}

const JOB_ID_KEY = 'geodesk_active_job_id';
const POLL_INTERVAL_MS = 8000;
const AUTO_DISMISS_MS = 10000;

function formatElapsed(updatedAt: string): string {
  const diff = Math.floor((Date.now() - new Date(updatedAt).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  const mins = Math.floor(diff / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m ago`;
}

const keyframes = `
@keyframes geodesk-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
@keyframes geodesk-pulse-border {
  0%, 100% { box-shadow: 0 0 0 0 rgba(0, 229, 255, 0.25); }
  50%       { box-shadow: 0 0 0 6px rgba(0, 229, 255, 0); }
}
@keyframes geodesk-bar-shimmer {
  0%   { background-position: -200% center; }
  100% { background-position: 200% center; }
}
@keyframes geodesk-fadein {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
`;

const JobStatusPanel: React.FC = () => {
  const [jobId, setJobId] = useState<string | null>(() => localStorage.getItem(JOB_ID_KEY));
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [elapsed, setElapsed] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoDismissRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearPoll = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  };

  const fetchStatus = useCallback(async (id: string) => {
    try {
      const res = await api.get<JobStatus>(`/api/jobs/${id}/status`);
      setStatus(res.data);
      if (res.data.updated_at) {
        setElapsed(formatElapsed(res.data.updated_at));
      }
      if (res.data.status === 'completed' || res.data.status === 'failed') {
        clearPoll();
        if (res.data.status === 'completed') {
          autoDismissRef.current = setTimeout(() => {
            setDismissed(true);
            localStorage.removeItem(JOB_ID_KEY);
          }, AUTO_DISMISS_MS);
        }
      }
    } catch {
      // silently ignore polling errors
    }
  }, []);

  // Start polling when jobId is set
  useEffect(() => {
    if (!jobId || dismissed) return;

    fetchStatus(jobId);
    pollRef.current = setInterval(() => fetchStatus(jobId), POLL_INTERVAL_MS);

    return () => {
      clearPoll();
      if (autoDismissRef.current) clearTimeout(autoDismissRef.current);
    };
  }, [jobId, dismissed, fetchStatus]);

  // Update elapsed every second while running
  useEffect(() => {
    if (!status || !status.updated_at) return;
    const t = setInterval(() => setElapsed(formatElapsed(status.updated_at)), 1000);
    return () => clearInterval(t);
  }, [status]);

  // Listen for new job_id events from localStorage mutations (cross-tab + same-tab via custom event)
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === JOB_ID_KEY && e.newValue) {
        setJobId(e.newValue);
        setDismissed(false);
        setStatus(null);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const handleDismiss = () => {
    setDismissed(true);
    setStatus(null);
    clearPoll();
    if (autoDismissRef.current) clearTimeout(autoDismissRef.current);
    localStorage.removeItem(JOB_ID_KEY);
  };

  if (!jobId || dismissed || !status) return null;

  const isRunning = status.status === 'queued' || status.status === 'started' || status.status === 'progress';
  const isCompleted = status.status === 'completed';
  const isFailed = status.status === 'failed';

  const progressBarColor = isCompleted
    ? '#10b981'
    : isFailed
    ? '#ef4444'
    : 'linear-gradient(90deg, #00e5ff, #00b4d8, #00e5ff)';

  const progressBarStyle: React.CSSProperties = isCompleted || isFailed
    ? { background: progressBarColor }
    : {
        background: 'linear-gradient(90deg, #00e5ff 0%, #00b4d8 50%, #00e5ff 100%)',
        backgroundSize: '200% auto',
        animation: 'geodesk-bar-shimmer 2s linear infinite',
      };

  return (
    <>
      <style>{keyframes}</style>
      <div
        style={{
          position: 'fixed',
          bottom: '24px',
          right: '24px',
          width: '380px',
          zIndex: 9999,
          background: 'rgba(10, 18, 35, 0.92)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          border: '1px solid rgba(0, 229, 255, 0.15)',
          borderRadius: '14px',
          padding: '20px',
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.45)',
          animation: 'geodesk-fadein 0.3s ease-out, geodesk-pulse-border 3s ease-in-out infinite',
          fontFamily: 'inherit',
          color: '#e2e8f0',
        }}
      >
        {/* Header row */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {isRunning && (
              <div
                style={{
                  width: '16px',
                  height: '16px',
                  border: '2px solid rgba(0, 229, 255, 0.25)',
                  borderTop: '2px solid #00e5ff',
                  borderRadius: '50%',
                  animation: 'geodesk-spin 0.9s linear infinite',
                  flexShrink: 0,
                }}
              />
            )}
            {isCompleted && <span style={{ fontSize: '16px' }}>✅</span>}
            {isFailed && <span style={{ fontSize: '16px' }}>❌</span>}
            <span style={{ fontSize: '13px', fontWeight: 600, letterSpacing: '0.03em', color: '#94a3b8' }}>
              JOB STATUS
            </span>
          </div>
          <button
            onClick={handleDismiss}
            style={{
              background: 'none',
              border: 'none',
              color: '#64748b',
              cursor: 'pointer',
              fontSize: '18px',
              lineHeight: 1,
              padding: '0 4px',
              transition: 'color 0.2s',
            }}
            onMouseEnter={e => (e.currentTarget.style.color = '#e2e8f0')}
            onMouseLeave={e => (e.currentTarget.style.color = '#64748b')}
            title="Dismiss"
          >
            ×
          </button>
        </div>

        {/* Success banner */}
        {isCompleted && (
          <div
            style={{
              background: 'rgba(16, 185, 129, 0.12)',
              border: '1px solid rgba(16, 185, 129, 0.35)',
              borderRadius: '8px',
              padding: '10px 14px',
              marginBottom: '14px',
              color: '#10b981',
              fontWeight: 600,
              fontSize: '14px',
              textAlign: 'center',
            }}
          >
            ✅ Procesamiento completado
          </div>
        )}

        {/* Error banner */}
        {isFailed && status.error && (
          <div
            style={{
              background: 'rgba(239, 68, 68, 0.10)',
              border: '1px solid rgba(239, 68, 68, 0.35)',
              borderRadius: '8px',
              padding: '10px 14px',
              marginBottom: '14px',
              color: '#ef4444',
              fontSize: '13px',
              wordBreak: 'break-word',
            }}
          >
            <strong>Error:</strong> {status.error}
          </div>
        )}

        {/* Step description */}
        <div
          style={{
            fontSize: '13px',
            color: '#cbd5e1',
            marginBottom: '10px',
            minHeight: '18px',
            lineHeight: 1.5,
          }}
        >
          {status.step || 'Iniciando...'}
        </div>

        {/* Progress bar */}
        <div
          style={{
            background: 'rgba(255,255,255,0.06)',
            borderRadius: '99px',
            height: '8px',
            overflow: 'hidden',
            marginBottom: '8px',
          }}
        >
          <div
            style={{
              width: `${Math.min(100, Math.max(0, status.progress))}%`,
              height: '100%',
              borderRadius: '99px',
              transition: 'width 0.6s ease',
              ...progressBarStyle,
            }}
          />
        </div>

        {/* Progress % and elapsed */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span
            style={{
              fontSize: '12px',
              fontWeight: 700,
              color: isCompleted ? '#10b981' : isFailed ? '#ef4444' : '#00e5ff',
            }}
          >
            {status.progress}%
          </span>
          <span style={{ fontSize: '11px', color: '#475569' }}>
            actualizado {elapsed}
          </span>
        </div>

        {/* Auto-dismiss notice */}
        {isCompleted && (
          <div style={{ marginTop: '10px', fontSize: '11px', color: '#475569', textAlign: 'center' }}>
            Se cerrará automáticamente en {AUTO_DISMISS_MS / 1000}s
          </div>
        )}

        {/* Job ID footer */}
        <div style={{ marginTop: '12px', fontSize: '10px', color: '#334155', letterSpacing: '0.04em' }}>
          JOB ID: {status.job_id}
        </div>
      </div>
    </>
  );
};

export default JobStatusPanel;
