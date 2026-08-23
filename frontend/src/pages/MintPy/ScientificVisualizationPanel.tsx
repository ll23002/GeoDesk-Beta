import React, { useState, useEffect, useRef } from "react";
import api, { API_URL } from "../../services/api";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Legend,
} from "recharts";

// ── Types ─────────────────────────────────────────────────────────────────────

interface PipelineStep {
  id: string;
  order: number;
  label: string;
  description: string;
  unit: string;
  file_key: string | null;
  color_scheme: string | null;
  available_from: string;
  has_data: boolean;
  available_years?: Record<string, number[]>;
}

interface TimeseriesPoint {
  date: string;
  deformation: number;
  lat: number;
  lon: number;
  year: number;
}

interface VolcanoMeta {
  id: string;
  name: string;
  lat: number;
  lon: number;
  years_available: number[];
  has_data: boolean;
}

// ── Color helpers ─────────────────────────────────────────────────────────────

const STEP_COLORS: Record<string, string> = {
  raw_phase:       "#f59e0b",
  corrected_phase: "#06b6d4",
  pre_pyaps:       "#a78bfa",
  post_pyaps:      "#10b981",
  timeseries:      "#00e5ff",
  multi_year:      "#f472b6",
};

const yearColors = [
  "#00e5ff", "#10b981", "#f59e0b", "#a78bfa",
  "#f472b6", "#34d399", "#fb923c", "#818cf8",
];

// ── Sub-components ────────────────────────────────────────────────────────────

const StepDot: React.FC<{
  step: PipelineStep;
  active: boolean;
  onClick: () => void;
}> = ({ step, active, onClick }) => {
  const color = STEP_COLORS[step.id] ?? "#64748b";
  return (
    <button
      onClick={onClick}
      title={step.label}
      style={{
        width: 44,
        height: 44,
        borderRadius: "50%",
        border: `2px solid ${active ? color : "rgba(255,255,255,0.1)"}`,
        background: active ? `${color}22` : "rgba(255,255,255,0.03)",
        color: active ? color : "#64748b",
        cursor: "pointer",
        fontWeight: 700,
        fontSize: "0.9rem",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        transition: "all 0.25s",
        flexShrink: 0,
        boxShadow: active ? `0 0 18px ${color}55` : "none",
        position: "relative",
      }}
    >
      {step.order}
      {step.has_data && (
        <span
          style={{
            position: "absolute",
            top: -3,
            right: -3,
            width: 10,
            height: 10,
            borderRadius: "50%",
            background: color,
            border: "2px solid #0a1223",
          }}
        />
      )}
    </button>
  );
};

const ImagePreview: React.FC<{ stepId: string; label: string }> = ({ stepId, label }) => {
  const [src, setSrc] = useState<string | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    setSrc(`${API_URL}/api/viz/pipeline/preview/${stepId}?t=${Date.now()}`);
    setErr(false);
  }, [stepId]);

  if (err) {
    return (
      <div
        style={{
          minHeight: 200,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          color: "#475569",
          gap: 8,
          background: "rgba(255,255,255,0.02)",
          borderRadius: 10,
        }}
      >
        <span style={{ fontSize: "2rem" }}>🗺️</span>
        <span style={{ fontSize: "0.8rem" }}>
          Imagen no disponible para este paso
        </span>
        <span style={{ fontSize: "0.7rem", color: "#334155" }}>
          El procesamiento aún no ha generado este resultado
        </span>
      </div>
    );
  }

  return (
    <div style={{ position: "relative" }}>
      {!src && (
        <div style={{ minHeight: 200, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div className="spin" style={{ width: 24, height: 24, border: "3px solid #1e293b", borderTopColor: "#00e5ff", borderRadius: "50%" }} />
        </div>
      )}
      {src && (
        <img
          src={src}
          alt={label}
          onError={() => setErr(true)}
          style={{
            width: "100%",
            borderRadius: 10,
            objectFit: "contain",
            maxHeight: 340,
            display: "block",
          }}
        />
      )}
    </div>
  );
};

// Custom Recharts tooltip
const CustomTooltip: React.FC<{
  active?: boolean;
  payload?: Array<{ value: number; dataKey: string; color: string }>;
  label?: string;
}> = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div
      style={{
        background: "rgba(10,18,35,0.95)",
        border: "1px solid rgba(0,229,255,0.2)",
        borderRadius: 8,
        padding: "10px 14px",
        fontSize: "0.8rem",
      }}
    >
      <div style={{ color: "#94a3b8", marginBottom: 6 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontWeight: 600 }}>
          {p.dataKey === "deformation"
            ? `Deformación: ${p.value > 0 ? "+" : ""}${p.value.toFixed(2)} mm`
            : `${p.dataKey}: ${p.value.toFixed(2)}`}
        </div>
      ))}
    </div>
  );
};

// ── Main Component ────────────────────────────────────────────────────────────

const ScientificVisualizationPanel: React.FC = () => {
  const [steps, setSteps] = useState<PipelineStep[]>([]);
  const [activeStep, setActiveStep] = useState<PipelineStep | null>(null);
  const [volcanoes, setVolcanoes] = useState<VolcanoMeta[]>([]);
  const [selectedVolcano, setSelectedVolcano] = useState<string>("santa_ana");
  const [timeseriesData, setTimeseriesData] = useState<TimeseriesPoint[]>([]);
  const [tsLoading, setTsLoading] = useState(false);
  const [tsError, setTsError] = useState<string | null>(null);
  const [stepsLoading, setStepsLoading] = useState(true);

  // ── Load pipeline steps ────────────────────────────────────────────────────
  useEffect(() => {
    setStepsLoading(true);
    api.get<{ steps: PipelineStep[] }>("/api/viz/pipeline/steps")
      .then(res => {
        setSteps(res.data.steps);
        const first = res.data.steps[0];
        if (first) setActiveStep(first);
      })
      .catch(err => console.error("[Viz] Error loading steps:", err))
      .finally(() => setStepsLoading(false));
  }, []);

  // ── Load volcanoes ────────────────────────────────────────────────────────
  useEffect(() => {
    api.get<{ volcanoes: VolcanoMeta[] }>("/api/viz/volcanoes")
      .then(res => setVolcanoes(res.data.volcanoes))
      .catch(err => console.error("[Viz] Error loading volcanoes:", err));
  }, []);

  // ── Load timeseries when volcano or step changes ───────────────────────────
  useEffect(() => {
    if (!activeStep || !["timeseries", "multi_year"].includes(activeStep.id)) return;
    setTsLoading(true);
    setTsError(null);
    api.get<{ records: TimeseriesPoint[] }>(
      `/api/viz/timeseries/${selectedVolcano}?include_current=true`
    )
      .then(res => setTimeseriesData(res.data.records))
      .catch(err => {
        const msg = err?.response?.data?.detail ?? "No hay datos históricos para este volcán.";
        setTsError(typeof msg === "string" ? msg : JSON.stringify(msg));
      })
      .finally(() => setTsLoading(false));
  }, [activeStep, selectedVolcano]);

  // ── Prepare chart data ────────────────────────────────────────────────────
  // Group by date, average deformation per date
  const chartData = React.useMemo(() => {
    if (!timeseriesData.length) return [];
    const byDate = new Map<string, number[]>();
    timeseriesData.forEach(p => {
      const arr = byDate.get(p.date) ?? [];
      arr.push(p.deformation);
      byDate.set(p.date, arr);
    });
    return Array.from(byDate.entries())
      .map(([date, vals]) => ({
        date,
        deformation: parseFloat((vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(3)),
      }))
      .sort((a, b) => a.date.localeCompare(b.date));
  }, [timeseriesData]);

  // Identify year boundaries for reference lines
  const yearBoundaries = React.useMemo(() => {
    const years = new Set<number>();
    timeseriesData.forEach(p => years.add(p.year));
    const result: Array<{ year: number; date: string }> = [];
    years.forEach(year => {
      const pts = timeseriesData.filter(p => p.year === year);
      if (pts.length) {
        result.push({ year, date: pts[0].date });
      }
    });
    return result.sort((a, b) => a.date.localeCompare(b.date));
  }, [timeseriesData]);

  const selectedVolcanoMeta = volcanoes.find(v => v.id === selectedVolcano);

  // ── Render step content ───────────────────────────────────────────────────
  const renderStepContent = () => {
    if (!activeStep) return null;
    const color = STEP_COLORS[activeStep.id] ?? "#00e5ff";

    // Steps 1-4: image-based
    if (["raw_phase", "corrected_phase", "pre_pyaps", "post_pyaps"].includes(activeStep.id)) {
      return (
        <div>
          {/* Comparison between two phases */}
          {activeStep.id === "corrected_phase" && (
            <div
              style={{
                background: `rgba(6,182,212,0.07)`,
                border: "1px solid rgba(6,182,212,0.2)",
                borderRadius: 10,
                padding: "10px 14px",
                marginBottom: 16,
                fontSize: "0.8rem",
                color: "#67e8f9",
                display: "flex",
                gap: 8,
                alignItems: "flex-start",
              }}
            >
              <span style={{ flexShrink: 0 }}>ℹ️</span>
              <span>
                Comparando este resultado con el Paso 1 (Fase Original) permite ver cuánto ruido orbital eliminó MintPy.
                Una fase más suave y coherente indica una corrección exitosa.
              </span>
            </div>
          )}
          {activeStep.id === "post_pyaps" && (
            <div
              style={{
                background: `rgba(16,185,129,0.07)`,
                border: "1px solid rgba(16,185,129,0.2)",
                borderRadius: 10,
                padding: "10px 14px",
                marginBottom: 16,
                fontSize: "0.8rem",
                color: "#6ee7b7",
                display: "flex",
                gap: 8,
                alignItems: "flex-start",
              }}
            >
              <span style={{ flexShrink: 0 }}>✅</span>
              <span>
                Este es el resultado final más confiable. La corrección ERA5 elimina el efecto de la humedad atmosférica
                que en El Salvador puede superar los 5–10 mm/año de señal espuria en épocas de lluvia (mayo–octubre).
              </span>
            </div>
          )}

          {activeStep.has_data ? (
            <ImagePreview stepId={activeStep.id} label={activeStep.label} />
          ) : (
            <div
              style={{
                minHeight: 220,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                background: "rgba(255,255,255,0.02)",
                borderRadius: 10,
                color: "#475569",
                gap: 8,
              }}
            >
              <span style={{ fontSize: "2.5rem" }}>⏳</span>
              <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>Resultado aún no disponible</span>
              <span style={{ fontSize: "0.75rem", color: "#334155", textAlign: "center", maxWidth: 300 }}>
                Este paso se generará automáticamente al completar el siguiente ciclo de procesamiento.
              </span>
            </div>
          )}
        </div>
      );
    }

    // Step 5 & 6: time series chart
    if (["timeseries", "multi_year"].includes(activeStep.id)) {
      return (
        <div>
          {/* Volcano selector */}
          <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
            <span style={{ color: "#64748b", fontSize: "0.8rem", alignSelf: "center" }}>Volcán:</span>
            {volcanoes.map(v => (
              <button
                key={v.id}
                onClick={() => setSelectedVolcano(v.id)}
                style={{
                  padding: "5px 12px",
                  borderRadius: 6,
                  border: `1px solid ${selectedVolcano === v.id ? color : "rgba(255,255,255,0.1)"}`,
                  background: selectedVolcano === v.id ? `${color}22` : "rgba(255,255,255,0.03)",
                  color: selectedVolcano === v.id ? color : "#64748b",
                  cursor: "pointer",
                  fontSize: "0.78rem",
                  fontWeight: selectedVolcano === v.id ? 700 : 400,
                  transition: "all 0.2s",
                }}
              >
                {v.name.replace("Volcán de ", "").replace(" (Ilamatepec)", "").replace(" (Quezaltepeque)", "").replace(" (Chaparrastique)", "").replace(" (Chinchontepec)", "")}
                {v.years_available.length > 0 && (
                  <span style={{ marginLeft: 4, opacity: 0.6, fontSize: "0.7rem" }}>
                    ({v.years_available.length} año{v.years_available.length !== 1 ? "s" : ""})
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Volcano info */}
          {selectedVolcanoMeta && (
            <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
              <span style={{ fontSize: "0.75rem", color: "#64748b" }}>📍</span>
              <span style={{ fontSize: "0.75rem", color: "#64748b" }}>
                {selectedVolcanoMeta.name} —{" "}
                {selectedVolcanoMeta.lat.toFixed(4)}°N, {selectedVolcanoMeta.lon.toFixed(4)}°W
              </span>
              {selectedVolcanoMeta.years_available.length > 0 && (
                <span style={{ fontSize: "0.72rem", color: "#475569" }}>
                  • Datos históricos: {selectedVolcanoMeta.years_available.join(", ")}
                </span>
              )}
            </div>
          )}

          {/* Offset concatenation info */}
          {yearBoundaries.length > 1 && (
            <div
              style={{
                background: "rgba(0,229,255,0.06)",
                border: "1px solid rgba(0,229,255,0.15)",
                borderRadius: 8,
                padding: "8px 12px",
                marginBottom: 14,
                fontSize: "0.75rem",
                color: "#67e8f9",
                display: "flex",
                gap: 8,
              }}
            >
              <span>🔗</span>
              <span>
                Serie multi-anual con encadenamiento por offset. Cada año comienza donde terminó
                el anterior para mantener la continuidad de la deformación acumulada.
                {yearBoundaries.map((b, i) => (
                  <span key={b.year} style={{ marginLeft: 4, opacity: 0.7 }}>
                    {i > 0 && "→ "}
                    <span style={{ color: yearColors[i % yearColors.length] }}>{b.year}</span>
                  </span>
                ))}
              </span>
            </div>
          )}

          {/* Chart */}
          {tsLoading ? (
            <div style={{ minHeight: 260, display: "flex", alignItems: "center", justifyContent: "center", gap: 10, color: "#64748b" }}>
              <div
                style={{
                  width: 20,
                  height: 20,
                  border: "3px solid #1e293b",
                  borderTopColor: color,
                  borderRadius: "50%",
                  animation: "spin 0.8s linear infinite",
                }}
              />
              Cargando serie temporal...
            </div>
          ) : tsError ? (
            <div
              style={{
                minHeight: 200,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                color: "#475569",
                gap: 8,
              }}
            >
              <span style={{ fontSize: "2rem" }}>📊</span>
              <span style={{ fontSize: "0.8rem" }}>{tsError}</span>
              <span style={{ fontSize: "0.72rem", color: "#334155" }}>
                Los datos históricos se generarán al completar el primer procesamiento anual.
              </span>
            </div>
          ) : chartData.length === 0 ? (
            <div
              style={{
                minHeight: 200,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                color: "#475569",
                gap: 8,
              }}
            >
              <span style={{ fontSize: "2rem" }}>📊</span>
              <span style={{ fontSize: "0.8rem" }}>Sin datos para este volcán aún.</span>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="tsGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={color} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={color} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis
                  dataKey="date"
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: "rgba(255,255,255,0.08)" }}
                  tickFormatter={d => d.slice(0, 7)}
                  interval="preserveStartEnd"
                />
                <YAxis
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: "rgba(255,255,255,0.08)" }}
                  tickFormatter={v => `${v > 0 ? "+" : ""}${v.toFixed(1)}`}
                  unit=" mm"
                  width={65}
                />
                <Tooltip content={<CustomTooltip />} />
                {/* Year boundary reference lines */}
                {yearBoundaries.slice(1).map((b, i) => (
                  <ReferenceLine
                    key={b.year}
                    x={b.date}
                    stroke={yearColors[i % yearColors.length]}
                    strokeDasharray="4 3"
                    strokeOpacity={0.5}
                    label={{
                      value: `${b.year}`,
                      fill: yearColors[i % yearColors.length],
                      fontSize: 10,
                      position: "insideTopRight",
                    }}
                  />
                ))}
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
                <Line
                  type="monotone"
                  dataKey="deformation"
                  stroke={color}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 5, fill: color }}
                />
              </LineChart>
            </ResponsiveContainer>
          )}

          {chartData.length > 0 && (
            <div style={{ marginTop: 8, display: "flex", gap: 16, flexWrap: "wrap" }}>
              <div style={{ fontSize: "0.75rem", color: "#64748b" }}>
                <span style={{ fontWeight: 600, color: "#94a3b8" }}>Puntos:</span>{" "}
                {chartData.length.toLocaleString()}
              </div>
              <div style={{ fontSize: "0.75rem", color: "#64748b" }}>
                <span style={{ fontWeight: 600, color: "#94a3b8" }}>Rango:</span>{" "}
                {chartData[0]?.date} → {chartData[chartData.length - 1]?.date}
              </div>
              <div style={{ fontSize: "0.75rem", color: "#64748b" }}>
                <span style={{ fontWeight: 600, color: "#94a3b8" }}>Deformación acumulada:</span>{" "}
                <span style={{ color: chartData[chartData.length - 1]?.deformation < 0 ? "#f87171" : "#34d399" }}>
                  {chartData[chartData.length - 1]?.deformation > 0 ? "+" : ""}
                  {chartData[chartData.length - 1]?.deformation.toFixed(2)} mm
                </span>
              </div>
            </div>
          )}
        </div>
      );
    }

    return null;
  };

  // ── Main render ───────────────────────────────────────────────────────────
  const accentColor = activeStep ? STEP_COLORS[activeStep.id] ?? "#00e5ff" : "#00e5ff";

  return (
    <div
      style={{
        background: "rgba(255,255,255,0.02)",
        border: "1px solid rgba(255,255,255,0.07)",
        borderRadius: 18,
        padding: "28px 28px 24px",
        display: "flex",
        flexDirection: "column",
        gap: 0,
      }}
    >
      {/* CSS animations */}
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
      `}</style>

      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <h2
          style={{
            margin: "0 0 6px",
            fontSize: "1.1rem",
            fontWeight: 700,
            color: "#e2e8f0",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <span
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: "linear-gradient(135deg, #00e5ff22, #00b4d822)",
              border: "1px solid rgba(0,229,255,0.3)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "1rem",
            }}
          >
            🔬
          </span>
          Visualización Científica — Pipeline InSAR
        </h2>
        <p style={{ margin: 0, fontSize: "0.82rem", color: "#64748b" }}>
          Explora el resultado de cada etapa del procesamiento, desde la fase cruda hasta la serie temporal multi-anual.
        </p>
      </div>

      {/* Step stepper */}
      {stepsLoading ? (
        <div style={{ display: "flex", gap: 12, marginBottom: 24 }}>
          {[1, 2, 3, 4, 5, 6].map(i => (
            <div
              key={i}
              style={{
                width: 44,
                height: 44,
                borderRadius: "50%",
                background: "rgba(255,255,255,0.04)",
                border: "2px solid rgba(255,255,255,0.06)",
                animation: "pulse 1.5s ease-in-out infinite",
              }}
            />
          ))}
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 24, overflowX: "auto", paddingBottom: 4 }}>
          {steps.map((step, idx) => (
            <React.Fragment key={step.id}>
              <StepDot
                step={step}
                active={activeStep?.id === step.id}
                onClick={() => setActiveStep(step)}
              />
              {idx < steps.length - 1 && (
                <div
                  style={{
                    flex: 1,
                    height: 2,
                    minWidth: 16,
                    background: "rgba(255,255,255,0.06)",
                    borderRadius: 1,
                    position: "relative",
                    overflow: "hidden",
                  }}
                >
                  {step.has_data && steps[idx + 1]?.has_data && (
                    <div
                      style={{
                        position: "absolute",
                        inset: 0,
                        background: `linear-gradient(90deg, ${STEP_COLORS[step.id] ?? "#64748b"}88, ${STEP_COLORS[steps[idx + 1].id] ?? "#64748b"}88)`,
                      }}
                    />
                  )}
                </div>
              )}
            </React.Fragment>
          ))}
        </div>
      )}

      {/* Active step detail */}
      {activeStep && (
        <div style={{ animation: "fadeIn 0.3s ease" }}>
          {/* Step header */}
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              justifyContent: "space-between",
              marginBottom: 16,
              gap: 12,
            }}
          >
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: accentColor,
                    boxShadow: `0 0 8px ${accentColor}`,
                    display: "inline-block",
                  }}
                />
                <span style={{ color: "#64748b", fontSize: "0.72rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                  Paso {activeStep.order} de {steps.length}
                </span>
              </div>
              <h3 style={{ margin: "0 0 4px", fontSize: "1rem", color: "#e2e8f0", fontWeight: 700 }}>
                {activeStep.label}
              </h3>
              <p style={{ margin: 0, fontSize: "0.8rem", color: "#64748b", lineHeight: 1.5, maxWidth: 560 }}>
                {activeStep.description}
              </p>
            </div>

            {/* Metadata chips */}
            <div style={{ display: "flex", flexDirection: "column", gap: 4, flexShrink: 0 }}>
              <span
                style={{
                  padding: "3px 8px",
                  borderRadius: 4,
                  background: `${accentColor}15`,
                  border: `1px solid ${accentColor}30`,
                  color: accentColor,
                  fontSize: "0.7rem",
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                }}
              >
                {activeStep.unit}
              </span>
              <span
                style={{
                  padding: "3px 8px",
                  borderRadius: 4,
                  background: "rgba(255,255,255,0.04)",
                  border: "1px solid rgba(255,255,255,0.08)",
                  color: "#64748b",
                  fontSize: "0.65rem",
                  whiteSpace: "nowrap",
                }}
              >
                {activeStep.available_from}
              </span>
              <span
                style={{
                  padding: "3px 8px",
                  borderRadius: 4,
                  background: activeStep.has_data ? "rgba(16,185,129,0.1)" : "rgba(100,116,139,0.1)",
                  border: `1px solid ${activeStep.has_data ? "rgba(16,185,129,0.2)" : "rgba(100,116,139,0.1)"}`,
                  color: activeStep.has_data ? "#34d399" : "#475569",
                  fontSize: "0.65rem",
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                }}
              >
                {activeStep.has_data ? "✓ Datos disponibles" : "Pendiente"}
              </span>
            </div>
          </div>

          {/* Divider */}
          <div style={{ height: 1, background: "rgba(255,255,255,0.05)", marginBottom: 20 }} />

          {/* Step content */}
          {renderStepContent()}

          {/* Navigation arrows */}
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 20 }}>
            <button
              onClick={() => {
                const idx = steps.findIndex(s => s.id === activeStep.id);
                if (idx > 0) setActiveStep(steps[idx - 1]);
              }}
              disabled={steps.findIndex(s => s.id === activeStep.id) === 0}
              style={{
                padding: "7px 16px",
                borderRadius: 8,
                border: "1px solid rgba(255,255,255,0.08)",
                background: "rgba(255,255,255,0.03)",
                color: steps.findIndex(s => s.id === activeStep.id) === 0 ? "#334155" : "#94a3b8",
                cursor: steps.findIndex(s => s.id === activeStep.id) === 0 ? "not-allowed" : "pointer",
                fontSize: "0.8rem",
                transition: "all 0.2s",
              }}
            >
              ← Paso anterior
            </button>
            <button
              onClick={() => {
                const idx = steps.findIndex(s => s.id === activeStep.id);
                if (idx < steps.length - 1) setActiveStep(steps[idx + 1]);
              }}
              disabled={steps.findIndex(s => s.id === activeStep.id) === steps.length - 1}
              style={{
                padding: "7px 16px",
                borderRadius: 8,
                border: `1px solid ${accentColor}40`,
                background: `${accentColor}10`,
                color: steps.findIndex(s => s.id === activeStep.id) === steps.length - 1 ? "#334155" : accentColor,
                cursor: steps.findIndex(s => s.id === activeStep.id) === steps.length - 1 ? "not-allowed" : "pointer",
                fontSize: "0.8rem",
                fontWeight: 600,
                transition: "all 0.2s",
              }}
            >
              Siguiente paso →
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default ScientificVisualizationPanel;
