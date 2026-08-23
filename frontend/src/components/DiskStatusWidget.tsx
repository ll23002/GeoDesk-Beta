import React, { useState, useEffect } from 'react';
import api from '../services/api';

interface DiskStatusResponse {
  total_gb: number;
  used_gb: number;
  free_gb: number;
  used_percent: number;
  is_warning: boolean;   // true when free_gb < 80
  is_critical: boolean;  // true when free_gb < 50
}

const REFRESH_INTERVAL_MS = 60_000;

const pulseKeyframes = `
@keyframes geodesk-disk-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}
`;

function getBarColor(freeGb: number): string {
  if (freeGb < 50) return '#ef4444';
  if (freeGb < 80) return '#f59e0b';
  return '#10b981';
}

function formatGb(gb: number): string {
  return gb.toFixed(1);
}

const DiskStatusWidget: React.FC = () => {
  const [data, setData] = useState<DiskStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchDiskStatus = async () => {
    try {
      const res = await api.get<DiskStatusResponse>('/api/jobs/disk/status');
      setData(res.data);
      setError(null);
    } catch (err: unknown) {
      setError('No se pudo obtener el estado del disco.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDiskStatus();
    const interval = setInterval(fetchDiskStatus, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  const barColor = data ? getBarColor(data.free_gb) : '#475569';
  const usedPercent = data ? Math.min(100, data.used_percent) : 0;

  return (
    <>
      <style>{pulseKeyframes}</style>
      <div
        style={{
          width: '100%',
          background: 'rgba(10, 18, 35, 0.75)',
          border: '1px solid rgba(0, 229, 255, 0.10)',
          borderRadius: '10px',
          padding: '14px 18px',
          boxSizing: 'border-box',
          fontFamily: 'inherit',
          color: '#e2e8f0',
        }}
      >
        {/* Title row */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '15px' }}>💾</span>
            <span style={{ fontSize: '12px', fontWeight: 600, letterSpacing: '0.06em', color: '#94a3b8', textTransform: 'uppercase' }}>
              Estado del Disco
            </span>
          </div>

          {/* Alert icons */}
          {data?.is_critical && (
            <span
              style={{
                fontSize: '16px',
                animation: 'geodesk-disk-pulse 1s ease-in-out infinite',
                color: '#ef4444',
              }}
              title="Espacio crítico en disco"
            >
              🔴
            </span>
          )}
          {data?.is_warning && !data?.is_critical && (
            <span style={{ fontSize: '16px', color: '#f59e0b' }} title="Espacio bajo en disco">
              ⚠️
            </span>
          )}

          {loading && (
            <span style={{ fontSize: '11px', color: '#475569' }}>Cargando…</span>
          )}
        </div>

        {error && (
          <div
            style={{
              fontSize: '12px',
              color: '#ef4444',
              background: 'rgba(239, 68, 68, 0.08)',
              border: '1px solid rgba(239, 68, 68, 0.25)',
              borderRadius: '6px',
              padding: '8px 12px',
            }}
          >
            {error}
          </div>
        )}

        {data && !error && (
          <>
            {/* Usage bar track */}
            <div
              style={{
                width: '100%',
                height: '10px',
                background: 'rgba(255, 255, 255, 0.06)',
                borderRadius: '99px',
                overflow: 'hidden',
                marginBottom: '10px',
              }}
            >
              <div
                style={{
                  width: `${usedPercent}%`,
                  height: '100%',
                  background: barColor,
                  borderRadius: '99px',
                  transition: 'width 0.7s ease, background 0.5s ease',
                }}
              />
            </div>

            {/* Stats row */}
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                fontSize: '12px',
                color: '#94a3b8',
                gap: '8px',
                flexWrap: 'wrap',
              }}
            >
              <span>
                <span style={{ color: '#64748b' }}>Total: </span>
                <span style={{ color: '#cbd5e1', fontWeight: 600 }}>{formatGb(data.total_gb)} GB</span>
              </span>
              <span>
                <span style={{ color: '#64748b' }}>Usado: </span>
                <span style={{ color: '#cbd5e1', fontWeight: 600 }}>{formatGb(data.used_gb)} GB</span>
              </span>
              <span>
                <span style={{ color: '#64748b' }}>Libre: </span>
                <span style={{ color: barColor, fontWeight: 700 }}>{formatGb(data.free_gb)} GB</span>
              </span>
            </div>

            {/* Percent used pill */}
            <div style={{ marginTop: '10px', display: 'flex', justifyContent: 'flex-end' }}>
              <span
                style={{
                  fontSize: '11px',
                  fontWeight: 600,
                  padding: '2px 8px',
                  borderRadius: '99px',
                  background: `${barColor}22`,
                  border: `1px solid ${barColor}55`,
                  color: barColor,
                  letterSpacing: '0.03em',
                }}
              >
                {usedPercent.toFixed(1)}% usado
              </span>
            </div>
          </>
        )}
      </div>
    </>
  );
};

export default DiskStatusWidget;
