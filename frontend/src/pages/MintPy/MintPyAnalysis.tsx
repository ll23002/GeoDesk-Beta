import React, { useState, useCallback, useRef, useEffect } from "react";
import ScientificVisualizationPanel from "./ScientificVisualizationPanel";
import axios from "axios";
import api, { apiFormData, API_URL } from "../../services/api";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  Legend,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MapContainer, TileLayer, FeatureGroup, useMap, Rectangle, CircleMarker, Marker, Tooltip as LeafletTooltip } from "react-leaflet";
import { EditControl } from "react-leaflet-draw";
import "leaflet-draw/dist/leaflet.draw.css";

if (typeof window !== "undefined") {
  (window as unknown as Window & { type: string }).type = "";
}

const STORAGE_KEY = "mintpy_last_crop";

type BoundsType = {
  lat_min: number;
  lon_min: number;
  lat_max: number;
  lon_max: number;
};

function isCropWithinBounds(crop: BoundsType, image: BoundsType): boolean {
  return (
    crop.lat_min >= image.lat_min &&
    crop.lat_max <= image.lat_max &&
    crop.lon_min >= image.lon_min &&
    crop.lon_max <= image.lon_max &&
    crop.lat_min < crop.lat_max &&
    crop.lon_min < crop.lon_max
  );
}

function parseCoord(raw: string): number | null {
  if (raw === "" || raw === "-" || raw === "." || raw === "-.") return null;
  const n = parseFloat(raw);
  return isNaN(n) ? null : n;
}

type DrawEvent = {
  layer: L.Rectangle | L.Polygon | L.Circle | L.CircleMarker | L.Marker | L.Polyline;
};

function MapContent({
  bounds,
  drawnBox,
  setDrawnBox,
  deformationData = [],
  seedPoints = [],
  velMin = 0,
  velMax = 0,
}: {
  bounds: BoundsType | null;
  drawnBox: BoundsType | null;
  setDrawnBox: (box: BoundsType | null) => void;
  deformationData?: Array<{ lat: number; lon: number; velocidad_mm_yr: number }>;
  seedPoints?: Array<{ lat: number; lon: number }>;
  velMin?: number;
  velMax?: number;
}) {
  const map = useMap();

  useEffect(() => {
    if (bounds && bounds.lat_min !== undefined) {
      const leafBounds = L.latLngBounds(
        [bounds.lat_min, bounds.lon_min],
        [bounds.lat_max, bounds.lon_max]
      );
      map.fitBounds(leafBounds, { padding: [50, 50] });
    }
  }, [bounds, map]);

  useEffect(() => {
    const styleId = "seed-pop-keyframes";
    if (!document.getElementById(styleId)) {
      const style = document.createElement("style");
      style.id = styleId;
      style.textContent = `@keyframes seed-pop {
        0%   { transform: scale(0) rotate(-20deg); opacity: 0; }
        60%  { transform: scale(1.3) rotate(8deg);  opacity: 1; }
        100% { transform: scale(1)   rotate(0deg);  opacity: 1; }
      }`;
      document.head.appendChild(style);
    }
  }, []);

  const onCreated = (e: DrawEvent) => {
    if (!(e.layer instanceof L.Rectangle)) {
      console.warn("Solo se aceptan rectángulos");
      return;
    }
    const layer = e.layer as L.Rectangle;
    const leafBounds = layer.getBounds();
    setDrawnBox({
      lat_min: leafBounds.getSouth(),
      lon_min: leafBounds.getWest(),
      lat_max: leafBounds.getNorth(),
      lon_max: leafBounds.getEast(),
    });
    map.removeLayer(layer);
  };

  return (
    <FeatureGroup>
      <EditControl
        position="topright"
        onCreated={onCreated}
        onDeleted={() => setDrawnBox(null)}
        draw={{
          rectangle: true,
          polygon: false,
          circle: false,
          circlemarker: false,
          marker: false,
          polyline: false,
        }}
        edit={{ edit: false, remove: true }}
      />
      {bounds && (
        <Rectangle
          bounds={[
            [bounds.lat_min, bounds.lon_min],
            [bounds.lat_max, bounds.lon_max],
          ]}
          pathOptions={{ color: "#3b82f6", weight: 2, fillOpacity: 0.1, dashArray: "5, 5" }}
        />
      )}
      {drawnBox && (
        <Rectangle
          bounds={[
            [drawnBox.lat_min, drawnBox.lon_min],
            [drawnBox.lat_max, drawnBox.lon_max],
          ]}
          pathOptions={{ color: "#ef4444", weight: 2, fillColor: "#ef4444", fillOpacity: 0.2 }}
        />
      )}
      {seedPoints.map((pt, i) => (
        <Marker
          key={i}
          position={[pt.lat, pt.lon]}
          icon={L.divIcon({
            className: "",
            html: `<div style="
              font-size: 28px;
              line-height: 1;
              filter: drop-shadow(0 2px 6px rgba(0,0,0,0.7));
              cursor: pointer;
              animation: seed-pop 0.4s cubic-bezier(.175,.885,.32,1.275) both;
            ">🌱</div>`,
            iconAnchor: [14, 28],
            popupAnchor: [0, -30],
          })}
        >
          <LeafletTooltip direction="top" offset={[0, -32]} opacity={1}>
            <span style={{ fontWeight: 600 }}>🌱 Punto semilla recomendado</span><br />
            <span style={{ fontSize: "0.8em", color: "#555" }}>Lat: {pt.lat.toFixed(5)} · Lon: {pt.lon.toFixed(5)}</span>
          </LeafletTooltip>
        </Marker>
      ))}
      {deformationData.map((pt, i) => {
        // Find color based on min/max of current data or just default to blue/red
        return (
          <CircleMarker
            key={i}
            center={[pt.lat, pt.lon]}
            radius={1.5}
            pathOptions={{
              fillColor: velocityColor(pt.velocidad_mm_yr, velMin, velMax),
              color: velocityColor(pt.velocidad_mm_yr, velMin, velMax),
              weight: 1,
              opacity: 0.8,
              fillOpacity: 0.6,
            }}
          >
            <LeafletTooltip>
              <span>Def: {pt.velocidad_mm_yr.toFixed(2)} mm/a</span>
            </LeafletTooltip>
          </CircleMarker>
        );
      })}
    </FeatureGroup>
  );
}

const COORD_FIELDS: { label: string; key: keyof BoundsType }[] = [
  { label: "Lat Mín (Sur)",  key: "lat_min" },
  { label: "Lat Máx (Norte)", key: "lat_max" },
  { label: "Lon Mín (Oeste)", key: "lon_min" },
  { label: "Lon Máx (Este)",  key: "lon_max" },
];

function CoordPanel({
  drawnBox,
  setDrawnBox,
  imageBounds,
}: {
  drawnBox: BoundsType;
  setDrawnBox: (b: BoundsType) => void;
  imageBounds: BoundsType | null;
}) {
  const [raw, setRaw] = useState<Record<keyof BoundsType, string>>({
    lat_min: String(drawnBox.lat_min),
    lat_max: String(drawnBox.lat_max),
    lon_min: String(drawnBox.lon_min),
    lon_max: String(drawnBox.lon_max),
  });

  const prevBox = useRef(drawnBox);
  useEffect(() => {
    if (prevBox.current !== drawnBox) {
      setRaw({
        lat_min: String(drawnBox.lat_min),
        lat_max: String(drawnBox.lat_max),
        lon_min: String(drawnBox.lon_min),
        lon_max: String(drawnBox.lon_max),
      });
      prevBox.current = drawnBox;
    }
  }, [drawnBox]);

  const handleChange = (key: keyof BoundsType, value: string) => {
    setRaw((r) => ({ ...r, [key]: value }));
    const n = parseCoord(value);
    if (n !== null) {
      setDrawnBox({ ...drawnBox, [key]: n });
    }
  };

  const isValid = imageBounds ? isCropWithinBounds(drawnBox, imageBounds) : true;

  return (
    <div
      style={{
        marginTop: "16px",
        padding: "16px",
        background: "rgba(0,0,0,0.2)",
        borderRadius: "12px",
        border: `1px solid ${isValid ? "rgba(255,255,255,0.08)" : "rgba(239,68,68,0.5)"}`,
      }}
    >
      <label
        style={{
          color: "#e2e8f0",
          fontWeight: 600,
          fontSize: "0.9rem",
          display: "block",
          marginBottom: "12px",
        }}
      >
        ✂️ Coordenadas de Recorte
      </label>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "8px" }}>
        {COORD_FIELDS.map(({ label, key }) => (
          <div key={key} style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
            <span
              style={{
                fontSize: "0.75rem",
                color: "#94a3b8",
                marginBottom: "4px",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {label}
            </span>
            <input
              type="text"
              inputMode="decimal"
              value={raw[key]}
              placeholder="ej. -89.2"
              onChange={(e) => handleChange(key, e.target.value)}
              style={{
                width: "100%",
                boxSizing: "border-box",
                padding: "8px",
                borderRadius: "6px",
                border: "1px solid rgba(255,255,255,0.1)",
                background: "rgba(255,255,255,0.03)",
                color: "white",
                fontSize: "0.85rem",
              }}
            />
          </div>
        ))}
      </div>
      {imageBounds && !isValid && (
        <p style={{ fontSize: "0.75rem", color: "#fca5a5", margin: 0, marginTop: "8px" }}>
          ⚠️ El recorte debe estar dentro de los límites de la imagen ({imageBounds.lat_min.toFixed(4)}°N–{imageBounds.lat_max.toFixed(4)}°N, {imageBounds.lon_min.toFixed(4)}°E–{imageBounds.lon_max.toFixed(4)}°E).
        </p>
      )}
    </div>
  );
}




interface IgramMeta {
  filename: string;
  date1: string;
  date2: string;
  days: number;
}

interface VelocityStats {
  min: number;
  max: number;
  mean: number;
  std: number;
  n_points: number;
  n_interferograms: number;
  date_start: string;
  date_end: string;
  era5_successful?: boolean;
  tropo_method?: string;
  min_ew?: number;
  max_ew?: number;
  mean_ew?: number;
  std_ew?: number;
  phase_closure_skipped?: boolean;
  min_coherence?: number;
  excluded_ifgrams?: Array<{
    date1: string;
    date2: string;
    days: number;
    filename: string;
    reason: string;
  }>;
}

interface VelocityPoint {
  lat: number;
  lon: number;
  velocidad_mm_yr: number;
  vel_ew_mm_yr?: number;
  vel_up_mm_yr?: number;
}

interface IgramStat {
  date1: string;
  date2: string;
  label: string;
  mean: number;
  std: number;
  min: number;
  max: number;
}

interface HyP3Stats {
  min: number;
  max: number;
  mean: number;
  std: number;
  n_points: number;
  n_interferograms: number;
  min_up?: number;
  max_up?: number;
  mean_up?: number;
  std_up?: number;
}

interface HyP3Results {
  stats: HyP3Stats;
  sample: VelocityPoint[];
  igram_stats?: IgramStat[];
}

interface ProcessingResults {
  stats: VelocityStats;
  interferograms: IgramMeta[];
  igram_stats?: IgramStat[];
  sample: VelocityPoint[];
  mode?: "2D" | "LOS";
  hyp3?: HyP3Results | null;
  phase_previews?: {
    pair_label: string;
    wrapped_url?: string | null;
    unwrapped_url?: string | null;
    corrected_url?: string | null;
  } | null;
}


/**
 * Maps a ground deformation velocity value to an RGB color string.
 * 
 * Uses a five-point color scale that transitions from subsidence (red) through
 * stable conditions (green) to uplift (blue). The color mapping is normalized
 * to the range of input data, allowing proper visualization of asymmetric
 * deformation patterns.
 * 
 * @param {number} val - The velocity value in mm/year to be mapped to a color
 * @param {number} min - The minimum velocity value in the dataset (typically subsidence)
 * @param {number} max - The maximum velocity value in the dataset (typically uplift)
 * @returns {string} An RGB color string in the format "rgb(r,g,b)" where r,g,b are 0-255
 *
 * @see The color scale follows InSAR conventions:
 * - 0.0 (Max Subsidence) → Red rgb(255,0,0)
 * - 0.25 → Yellow rgb(255,255,0)
 * - 0.50 (Stable 0 mm/yr) → Green rgb(0,255,0)
 * - 0.75 → Cyan rgb(0,255,255)
 * - 1.00 (Max Uplift) → Blue rgb(0,0,255)
 */
function velocityColor(val: number, min: number, max: number): string {
  const limit = Math.max(Math.abs(min), Math.abs(max));
  if (limit === 0) return "rgb(0, 255, 0)";

  const t = Math.max(0, Math.min(1, (val + limit) / (2 * limit)));

  
  if (t < 0.25) {
    const u = t / 0.25; 
    return `rgb(255, ${Math.round(255 * u)}, 0)`;
  } else if (t < 0.5) {
    const u = (t - 0.25) / 0.25;
    return `rgb(${Math.round(255 * (1 - u))}, 255, 0)`;
  } else if (t < 0.75) {
    const u = (t - 0.5) / 0.25;
    return `rgb(0, 255, ${Math.round(255 * u)})`;
  } else {
    const u = (t - 0.75) / 0.25;
    return `rgb(0, ${Math.round(255 * (1 - u))}, 255)`;
  }
}


function buildHistogram(points: VelocityPoint[], bins = 20): { range: string; count: number }[] {
  /**
 * Builds a distribution histogram of ground deformation velocity values.
 *
 * Groups velocity values into bins and counts how many points fall within each range.
 * Each bin is labeled with its minimum value and contains the count of points within
 * that range.
 *
 * @param {VelocityPoint[]} points - Array of points containing ground deformation velocity data
 * @param {number} [bins=20] - Number of bins to divide the velocity range into. Defaults to 20.
 * @returns {{ range: string; count: number }[]} Array of objects with two properties:
 *          - range: string with the minimum value of the range (e.g., "-5.0")
 *          - count: number with the quantity of points in that bin
 *
 **/
  if (!points.length) return [];
  const vals = points.map((p) => p.velocidad_mm_yr);
  const mn = Math.min(...vals);
  const mx = Math.max(...vals);
  if (mn === mx) return [{ range: `${mn.toFixed(2)}`, count: vals.length }];
  const step = (mx - mn) / bins;
  const buckets = Array.from({ length: bins }, (_, i) => ({
    range: `${(mn + i * step).toFixed(1)}`,
    count: 0,
  }));
  vals.forEach((v) => {
    const idx = Math.min(Math.floor((v - mn) / step), bins - 1);
    buckets[idx].count++;
  });
  return buckets;
}

// ── Main Component ─────────────────────────────────────────────────────────────

interface SeedPoint {
  lat: number;
  lon: number;
  is_mintpy_default: boolean;
}

interface PlanData {
  success: boolean;
  asc_count: number;
  desc_count: number;
  mode: "2D" | "LOS";
  available_modes?: string[];
}

export default function MintPyAnalysis() {
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [sessionId, setSessionId] = useState(() => crypto.randomUUID());
  const [uploadedCount, setUploadedCount] = useState(0);
  const [isUploading, setIsUploading] = useState(false);
  const [message, setMessage] = useState("");
  const [results, setResults] = useState<ProcessingResults | null>(null);
  const [seedPoints, setSeedPoints] = useState<SeedPoint[]>([]);
  const [selectedSeed, setSelectedSeed] = useState<SeedPoint | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const MIN_IGRAMS = 3;

  const [systemStatus, setSystemStatus] = useState<{
    ram: { total_gb: number; available_gb: number; used_gb: number; percent: number };
    disk: { total_gb: number; free_gb: number; used_gb: number; percent: number };
  } | null>(null);
  const [uploadWarning, setUploadWarning] = useState<string | null>(null);

  const [plan, setPlan] = useState<PlanData | null>(null);
  const [selectedMode, setSelectedMode] = useState<string | null>(null);
  const [minCoherence, setMinCoherence] = useState<number>(0.6);
  const [activeMethod, setActiveMethod] = useState<"mintpy" | "hyp3">("mintpy");
  const [viewMode, setViewMode] = useState<"UP" | "EW">("UP");

  const [bounds, setBounds] = useState<BoundsType | null>(null);
  const [drawnBox, setDrawnBox] = useState<BoundsType | null>(null);

  useEffect(() => {
    if (drawnBox) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(drawnBox));
      // When the crop changes, the previously found seed points are no longer valid
      // (a seed from a previous crop area may be outside the new one → 500 error).
      // Force the user to re-run the preview so seeds are recalculated within the new bounds.
      setSeedPoints([]);
      setSelectedSeed(null);
    }
  }, [drawnBox]);

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await api.get('/api/mintpy/system_status');
        if (res.data.success) {
          setSystemStatus({
            ram: res.data.ram,
            disk: res.data.disk,
          });
        }
      } catch (err) {
        console.error("Error al consultar el estado del sistema:", err);
      }
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, []);

  const fetchPlan = useCallback(async () => {
    try {
      const formData = new FormData();
      formData.append("session_id", sessionId);
      const res = await apiFormData.post('/api/mintpy/preview_plan', formData);
      if (res.data.success) {
        setPlan(res.data);
      }
    } catch (err) {
      console.error(err);
    }
  }, [sessionId]);

  const fetchBounds = useCallback(async () => {
    try {
      const formData = new FormData();
      formData.append("session_id", sessionId);
      const res = await apiFormData.post('/api/mintpy/preview_bounds', formData);
      if (res.data.success) {
        setBounds(res.data.bounds);
        
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved) {
          try {
            const savedCrop: BoundsType = JSON.parse(saved);
            if (isCropWithinBounds(savedCrop, res.data.bounds)) {
              setDrawnBox(savedCrop);
            }
          } catch (err) {
            console.error("Invalid saved crop in storage", err);
          }
        }
      }
    } catch (err) {
      console.error(err);
    }
  }, [sessionId]);

  useEffect(() => {
    if (files.length > 0 && uploadedCount === files.length && !busy && !isUploading) {
      if (!bounds) fetchBounds();
      if (!plan) fetchPlan();
    } else if (files.length === 0) {
      setPlan(null);
      setBounds(null);
      setSelectedMode(null);
    }
  }, [files, bounds, plan, busy, uploadedCount, isUploading, sessionId, fetchBounds, fetchPlan]);

  useEffect(() => {
    if (plan) {
      if (plan.available_modes && plan.available_modes.length > 0) {
        setSelectedMode(plan.available_modes.includes("2D") ? "2D" : plan.available_modes[0]);
      } else {
        setSelectedMode(plan.mode);
      }
    }
  }, [plan]);

  useEffect(() => {
    const queue = files.slice(uploadedCount);
    if (queue.length > 0 && !isUploading) {
      const uploadQueue = async () => {
        setIsUploading(true);
        try {
          for (const f of queue) {
            const formData = new FormData();
            formData.append("session_id", sessionId);
            formData.append("file", f, f.name);

            let lastLoadedBytes = 0;
            let lastActivityTime = Date.now();

            const activityInterval = setInterval(() => {
              // 3 minutes (180000 ms) of no upload activity / progress change
              if (Date.now() - lastActivityTime > 180000) {
                setUploadWarning(
                  `⚠️ Alerta: No se ha detectado progreso en la subida del archivo "${f.name}" durante los últimos 3 minutos. Es posible que el servidor en Windows esté saturado escribiendo en disco o que el antivirus esté analizando el archivo.`
                );
              }
            }, 5000);

            try {
              await apiFormData.post('/api/mintpy/upload_file', formData, {
                onUploadProgress: (progressEvent) => {
                  if (progressEvent.loaded !== lastLoadedBytes) {
                    lastLoadedBytes = progressEvent.loaded;
                    lastActivityTime = Date.now();
                    setUploadWarning(null); // Clear the warning if progress moves
                  }
                },
              });
            } finally {
              clearInterval(activityInterval);
              setUploadWarning(null);
            }

            setUploadedCount((prev) => prev + 1);
          }
        } catch (e) {
          console.error("Upload error", e);
        } finally {
          setIsUploading(false);
        }
      };
      uploadQueue();
    }
  }, [files, uploadedCount, isUploading, sessionId]);



  const onDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); setDragging(true); }, []);
  const onDragLeave = useCallback(() => setDragging(false), []);
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const dropped = Array.from(e.dataTransfer.files).filter((f) => f.name.toLowerCase().endsWith(".zip"));
    setFiles((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [...prev, ...dropped.filter((f) => !names.has(f.name))];
    });
  }, []);

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files) return;
    const selected = Array.from(e.target.files).filter((f) => f.name.toLowerCase().endsWith(".zip"));
    setFiles((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [...prev, ...selected.filter((f) => !names.has(f.name))];
    });
  };

  const removeFile = (name: string) => setFiles((prev) => prev.filter((f) => f.name !== name));

  // ── Process ──────────────────────────────────────────────────────────────────

  const handlePreview = async () => {
    if (files.length < MIN_IGRAMS) {
      setMessage(`❌ Se requieren al menos ${MIN_IGRAMS} interferogramas.`);
      return;
    }

    setBusy(true);
    setProgress(0);
    setSeedPoints([]);
    setSelectedSeed(null);
    setMessage("🔍 Buscando los puntos semilla de mayor coherencia...");

    try {
      const formData = new FormData();
      formData.append("session_id", sessionId);
      if (drawnBox) {
        formData.append("crop_lat_min", drawnBox.lat_min.toString());
        formData.append("crop_lat_max", drawnBox.lat_max.toString());
        formData.append("crop_lon_min", drawnBox.lon_min.toString());
        formData.append("crop_lon_max", drawnBox.lon_max.toString());
      }

      const response = await apiFormData.post('/api/mintpy/preview_reference', formData, {
        onUploadProgress: (e) => {
          if (e.total) setProgress(Math.round((e.loaded / e.total) * 100));
        },
      });

      const pts = response.data.seed_points as SeedPoint[];
      setSeedPoints(pts);
      const def = pts.find(p => p.is_mintpy_default) || pts[0];
      setSelectedSeed(def);
      setMessage(`✅ Se encontraron ${pts.length} puntos con coherencia máxima. Selecciona uno para anclar el cálculo.`);
    } catch (error: unknown) {
      if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail || error.message;
        setMessage(`❌ Error: ${detail}`);
      } else {
        setMessage("❌ Error desconocido al previsualizar.");
      }
    } finally {
      setBusy(false);
      setProgress(0);
    }
  };

  const handleProcess = async () => {
    if (files.length < MIN_IGRAMS) {
      setMessage(`Se requieren al menos ${MIN_IGRAMS} interferogramas. Actualmente tienes ${files.length}.`);
      return;
    }

    setBusy(true);
    setProgress(0);
    setResults(null);
    setMessage("Ejecutando inversión SBAS con el punto semilla seleccionado...");

    try {
      const formData = new FormData();
      formData.append("session_id", sessionId);
      if (selectedMode) {
        formData.append("selected_mode", selectedMode);
      }
      if (selectedSeed) {
        formData.append("ref_lat", selectedSeed.lat.toString());
        formData.append("ref_lon", selectedSeed.lon.toString());
      }
      if (drawnBox) {
        formData.append("crop_lat_min", drawnBox.lat_min.toString());
        formData.append("crop_lat_max", drawnBox.lat_max.toString());
        formData.append("crop_lon_min", drawnBox.lon_min.toString());
        formData.append("crop_lon_max", drawnBox.lon_max.toString());
      }
      formData.append("min_coherence", minCoherence.toString());

      const response = await apiFormData.post('/api/mintpy/process', formData, {
        onUploadProgress: (e) => {
          if (e.total) setProgress(Math.round((e.loaded / e.total) * 50));
        },
      });

      setProgress(100);
      setResults(response.data as ProcessingResults);
      setMessage(`Análisis completado: ${response.data.stats.n_points.toLocaleString()} puntos procesados.`);
    } catch (error: unknown) {
      if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail || error.message;
        setMessage(`Error: ${detail}`);
      } else {
        setMessage("Error desconocido al procesar.");
      }
    } finally {
      setBusy(false);
      setProgress(0);
    }
  };

  const handleReset = () => {
    const oldSession = sessionId;
    setFiles([]);
    setResults(null);
    setSeedPoints([]);
    setSelectedSeed(null);
    setBounds(null);
    setPlan(null);
    setSelectedMode(null);
    setSelectedSeed(null);
    setBounds(null);
    setMessage("");
    setProgress(0);
    setUploadedCount(0);
    setUploadWarning(null);
    setSessionId(crypto.randomUUID());
    const fd = new FormData();
    fd.append("session_id", oldSession);
    apiFormData.post('/api/mintpy/clear_session', fd).catch(console.error);
  };

  const cropIsValid = drawnBox && bounds ? isCropWithinBounds(drawnBox, bounds) : !!drawnBox;


  const hasHyp3 = !!(results?.hyp3);

  const activeSample = activeMethod === "hyp3" && hasHyp3
    ? (results!.hyp3!.sample)
    : results?.sample ?? [];

  const activeData = results ? (
    activeMethod === "hyp3" && hasHyp3
      ? activeSample
      : results.mode === "2D" ? results.sample.map(p => ({
          lat: p.lat, lon: p.lon, velocidad_mm_yr: viewMode === "EW" ? (p.vel_ew_mm_yr || 0) : (p.vel_up_mm_yr || 0)
        }))
      : results.sample
  ) : [];

  const histogramData = results ? buildHistogram(activeData) : [];
  const hyp3HistogramData = hasHyp3 ? buildHistogram(results!.hyp3!.sample) : [];

  const velMin = activeMethod === "hyp3" && hasHyp3
    ? (results!.hyp3!.stats.min)
    : (results && results.mode === "2D" && viewMode === "EW" ? (results.stats.min_ew ?? 0) : (results?.stats.min ?? 0));
  const velMax = activeMethod === "hyp3" && hasHyp3
    ? (results!.hyp3!.stats.max)
    : (results && results.mode === "2D" && viewMode === "EW" ? (results.stats.max_ew ?? 0) : (results?.stats.max ?? 0));


  return (
    <div
      style={{
        minHeight: "100vh",
        background: "linear-gradient(135deg, #0a0f1e 0%, #0d1b2a 50%, #0a1628 100%)",
        color: "white",
        fontFamily: "'Inter', sans-serif",
        padding: "24px",
      }}
    >
      {/* Header */}
      <div style={{ marginBottom: "32px" }}>
        <h1
          style={{
            fontSize: "1.8rem",
            fontWeight: 700,
            background: "linear-gradient(135deg, #38bdf8, #818cf8)",
            WebkitBackgroundClip: "text",
            WebkitTextFillColor: "transparent",
            margin: 0,
            marginBottom: "6px",
          }}
        >
          Análisis InSAR — MintPy
        </h1>
        <p style={{ color: "#94a3b8", fontSize: "0.9rem", margin: 0 }}>
          Inversión SBAS de velocidad de deformación del suelo (mm/año) a partir de múltiples interferogramas HyP3/ASF.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", gap: "24px", alignItems: "start" }}>
        {/* Left Panel: Upload */}
        <div
          style={{
            background: "rgba(255,255,255,0.03)",
            border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: "16px",
            padding: "24px",
          }}
        >


          <h2 style={{ fontSize: "1rem", fontWeight: 600, color: "#e2e8f0", marginBottom: "16px" }}>
            📂 Subir Interferogramas
          </h2>

          {/* Drop Zone */}
          <div
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={() => fileInputRef.current?.click()}
            style={{
              border: `2px dashed ${dragging ? "#38bdf8" : "rgba(255,255,255,0.15)"}`,
              borderRadius: "12px",
              padding: "32px 20px",
              textAlign: "center",
              cursor: "pointer",
              background: dragging ? "rgba(56,189,248,0.06)" : "rgba(255,255,255,0.02)",
              transition: "all 0.2s",
              marginBottom: "16px",
            }}
          >
            <div style={{ fontSize: "2rem", marginBottom: "8px" }}>📦</div>
            <p style={{ color: "#94a3b8", fontSize: "0.85rem", margin: 0 }}>
              Arrastra archivos <strong style={{ color: "#e2e8f0" }}>.zip</strong> aquí o haz clic para seleccionar
            </p>
            <p style={{ color: "#64748b", fontSize: "0.75rem", marginTop: "6px" }}>
              Mínimo {MIN_IGRAMS} interferogramas requeridos
            </p>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".zip"
              style={{ display: "none" }}
              onChange={onFileChange}
            />
          </div>

          {/* Validation badge */}
          {files.length > 0 && (
            <div
              style={{
                marginBottom: "12px",
                padding: "8px 12px",
                borderRadius: "8px",
                background:
                  files.length >= MIN_IGRAMS
                    ? "rgba(16,185,129,0.12)"
                    : "rgba(239,68,68,0.12)",
                border: `1px solid ${files.length >= MIN_IGRAMS ? "rgba(16,185,129,0.3)" : "rgba(239,68,68,0.3)"}`,
                fontSize: "0.8rem",
                color: files.length >= MIN_IGRAMS ? "#6ee7b7" : "#fca5a5",
              }}
            >
              {files.length >= MIN_IGRAMS && uploadedCount === files.length
                ? `✅ ${uploadedCount} interferogramas listos`
                : isUploading ? `⏳ Subiendo archivo ${uploadedCount + 1} de ${files.length}...` : `⚠️ ${files.length}/${MIN_IGRAMS} — faltan ${MIN_IGRAMS - files.length} más`}
            </div>
          )}

          {/* Advertencia de Inactividad en Carga */}
          {uploadWarning && (
            <div
              style={{
                marginBottom: "12px",
                padding: "10px 14px",
                borderRadius: "10px",
                background: "rgba(245, 158, 11, 0.12)",
                border: "1px solid rgba(245, 158, 11, 0.35)",
                fontSize: "0.8rem",
                color: "#fbbf24",
                lineHeight: "1.4",
              }}
            >
              {uploadWarning}
            </div>
          )}

          {plan && (
            <div
              style={{
                marginBottom: "12px",
                padding: "8px 12px",
                borderRadius: "8px",
                background: selectedMode === "2D" ? "rgba(56,189,248,0.12)" : "rgba(255,255,255,0.05)",
                border: `1px solid ${selectedMode === "2D" ? "rgba(56,189,248,0.3)" : "rgba(255,255,255,0.1)"}`,
                fontSize: "0.8rem",
                color: "#cbd5e1",
              }}
            >
              <div style={{fontWeight: 600, color: selectedMode === "2D" ? "#38bdf8" : "#cbd5e1"}}>
                {selectedMode === "2D" ? "🚀 Descomposición 2D" : selectedMode === "LOS ASC" ? "📏 LOS Ascendentes" : selectedMode === "LOS DESC" ? "📏 LOS Descendentes" : "📏 LOS Estándar"}
              </div>
              <div style={{ color: "#94a3b8", marginTop: "4px", fontSize: "0.75rem" }}>
                {selectedMode === "LOS ASC" ? `Ascendentes: ${plan.asc_count}` : selectedMode === "LOS DESC" ? `Descendentes: ${plan.desc_count}` : `Ascendentes: ${plan.asc_count} | Descendentes: ${plan.desc_count}`}
              </div>
            </div>
          )}


          {/* File list */}
          {files.length > 0 && (
            <div
              style={{
                maxHeight: "220px",
                overflowY: "auto",
                marginBottom: "16px",
                display: "flex",
                flexDirection: "column",
                gap: "6px",
              }}
            >
              {files.map((f) => (
                <div
                  key={f.name}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "8px 10px",
                    background: "rgba(255,255,255,0.04)",
                    borderRadius: "8px",
                    fontSize: "0.75rem",
                  }}
                >
                  <span
                    style={{
                      color: "#cbd5e1",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      maxWidth: "240px",
                    }}
                    title={f.name}
                  >
                    📄 {f.name}
                  </span>
                  <button
                    onClick={(e) => { e.stopPropagation(); removeFile(f.name); }}
                    style={{
                      background: "none",
                      border: "none",
                      color: "#94a3b8",
                      cursor: "pointer",
                      fontSize: "0.9rem",
                      padding: "0 4px",
                    }}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Progress bar */}
          {busy && progress > 0 && (
            <div
              style={{
                height: "6px",
                background: "rgba(255,255,255,0.1)",
                borderRadius: "4px",
                overflow: "hidden",
                marginBottom: "12px",
              }}
            >
              <div
                style={{
                  height: "100%",
                  width: `${progress}%`,
                  background: "linear-gradient(90deg, #38bdf8, #818cf8)",
                  transition: "width 0.3s ease",
                  borderRadius: "4px",
                }}
              />
            </div>
          )}

          {/* Message */}
          {message && (
            <div
              style={{
                padding: "10px 12px",
                borderRadius: "8px",
                background: message.startsWith("❌")
                  ? "rgba(239,68,68,0.1)"
                  : message.startsWith("✅")
                  ? "rgba(16,185,129,0.1)"
                  : "rgba(56,189,248,0.1)",
                border: `1px solid ${
                  message.startsWith("❌")
                    ? "rgba(239,68,68,0.3)"
                    : message.startsWith("✅")
                    ? "rgba(16,185,129,0.3)"
                    : "rgba(56,189,248,0.3)"
                }`,
                fontSize: "0.8rem",
                color: "#e2e8f0",
                marginBottom: "12px",
                lineHeight: "1.5",
              }}
            >
              {message}
            </div>
          )}

          {/* Coordinate panel — visible when bounds are available */}
          {bounds && !results && (
            <CoordPanel
              drawnBox={drawnBox ?? { lat_min: 0, lon_min: 0, lat_max: 0, lon_max: 0 }}
              setDrawnBox={setDrawnBox}
              imageBounds={bounds}
            />
          )}

          {/* Mode Selector */}
          {plan && plan.available_modes && plan.available_modes.length > 1 && !results && (
            <div style={{ marginTop: "16px", padding: "12px", background: "rgba(255,255,255,0.03)", borderRadius: "12px", border: "1px solid rgba(255,255,255,0.08)" }}>
              <label style={{ display: "block", marginBottom: "8px", fontWeight: 600, color: "#e2e8f0", fontSize: "0.9rem" }}>⚙️ Método de procesamiento:</label>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {plan.available_modes.map(m => (
                  <label key={m} style={{ display: "flex", alignItems: "center", cursor: "pointer", fontSize: "0.85rem", color: "#cbd5e1" }}>
                    <input 
                      type="radio" 
                      name="processing_mode" 
                      value={m} 
                      checked={selectedMode === m} 
                      onChange={() => setSelectedMode(m)} 
                      style={{ marginRight: "8px", accentColor: "#38bdf8" }} 
                    />
                    {m === "2D" ? "🚀 Descomposición 2D" : m === "LOS ASC" ? "📏 LOS Ascendentes" : "📏 LOS Descendentes"}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Min Coherence input */}
          <div
            style={{
              marginTop: "16px",
              padding: "14px 16px",
              background: "rgba(99,102,241,0.07)",
              border: "1px solid rgba(99,102,241,0.25)",
              borderRadius: "12px",
            }}
          >
            <label
              htmlFor="min-coherence-input"
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                fontSize: "0.82rem",
                fontWeight: 600,
                color: "#a5b4fc",
                marginBottom: "10px",
              }}
            >
              <span>🎚️ Coherencia mínima de red</span>
              <span
                style={{
                  fontSize: "1rem",
                  fontWeight: 700,
                  color: minCoherence >= 0.7 ? "#34d399" : minCoherence >= 0.5 ? "#fbbf24" : "#f87171",
                  minWidth: "36px",
                  textAlign: "right",
                }}
              >
                {minCoherence.toFixed(2)}
              </span>
            </label>
            <input
              id="min-coherence-input"
              type="range"
              min={0.1}
              max={1.0}
              step={0.05}
              value={minCoherence}
              onChange={(e) => setMinCoherence(parseFloat(e.target.value))}
              style={{ width: "100%", accentColor: "#818cf8", cursor: "pointer" }}
            />
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "0.68rem",
                color: "#475569",
                marginTop: "4px",
              }}
            >
              <span>0.10 (permisivo)</span>
              <span style={{ color: "#64748b" }}>Recomendado: 0.60–0.70</span>
              <span>1.00 (estricto)</span>
            </div>
            <p style={{ fontSize: "0.7rem", color: "#475569", margin: "8px 0 0", lineHeight: "1.4" }}>
              Interferogramas con coherencia espacial media inferior a este umbral serán excluidos de la red SBAS.
            </p>
          </div>

          {/* Action Buttons */}
          <div style={{ display: "flex", gap: "8px", marginTop: "16px" }}>
            {seedPoints.length === 0 ? (
              <button
                onClick={handlePreview}
                disabled={busy || files.length < MIN_IGRAMS || (!!drawnBox && !cropIsValid)}
                style={{
                  flex: 1,
                  padding: "12px",
                  borderRadius: "10px",
                  border: "none",
                  background:
                    busy || files.length < MIN_IGRAMS || (!!drawnBox && !cropIsValid)
                      ? "rgba(99,102,241,0.3)"
                      : "linear-gradient(135deg, #0ea5e9, #3b82f6)",
                  color: "white",
                  fontWeight: 600,
                  fontSize: "0.9rem",
                  cursor: busy || files.length < MIN_IGRAMS || (!!drawnBox && !cropIsValid) ? "not-allowed" : "pointer",
                  transition: "all 0.2s",
                }}
              >
                {busy ? "⏳ Buscando Semillas…" : "🔍 Buscar Puntos Semilla"}
              </button>
            ) : (
              <button
                onClick={handleProcess}
                disabled={busy || files.length < MIN_IGRAMS || (!!drawnBox && !cropIsValid)}
                style={{
                  flex: 1,
                  padding: "12px",
                  borderRadius: "10px",
                  border: "none",
                  background:
                    busy || files.length < MIN_IGRAMS || (!!drawnBox && !cropIsValid)
                      ? "rgba(16,185,129,0.3)"
                      : "linear-gradient(135deg, #10b981, #059669)",
                  color: "white",
                  fontWeight: 600,
                  fontSize: "0.9rem",
                  cursor: busy || files.length < MIN_IGRAMS || (!!drawnBox && !cropIsValid) ? "not-allowed" : "pointer",
                  transition: "all 0.2s",
                }}
              >
                {busy ? "⏳ Procesando…" : "🚀 Ejecutar Análisis SBAS"}
              </button>
            )}
            {(files.length > 0 || results) && (
              <button
                onClick={handleReset}
                disabled={busy}
                style={{
                  padding: "12px 16px",
                  borderRadius: "10px",
                  border: "1px solid rgba(255,255,255,0.15)",
                  background: "transparent",
                  color: "#94a3b8",
                  cursor: "pointer",
                  fontSize: "0.9rem",
                }}
              >
                ↺
              </button>
            )}
          </div>

          {/* Seed Points Selection */}
          {seedPoints.length > 0 && !results && (
            <div style={{ marginTop: "20px" }}>
              <h3 style={{ fontSize: "0.85rem", color: "#e2e8f0", marginBottom: "10px" }}>
                📍 Selecciona el Punto Cero (Semilla):
              </h3>
              <p style={{ fontSize: "0.75rem", color: "#94a3b8", marginBottom: "12px" }}>
                Estos puntos empataron con la máxima coherencia espacial.
              </p>
              
              <div style={{ display: "flex", flexDirection: "column", gap: "8px", maxHeight: "180px", overflowY: "auto", paddingRight: "4px" }}>
                {seedPoints.map((p, i) => {
                  const isSelected = selectedSeed === p;
                  return (
                    <div
                      key={i}
                      onClick={() => setSelectedSeed(p)}
                      style={{
                        padding: "10px 12px",
                        background: isSelected ? "rgba(56,189,248,0.15)" : "rgba(255,255,255,0.03)",
                        border: `1px solid ${isSelected ? "#38bdf8" : "rgba(255,255,255,0.1)"}`,
                        borderRadius: "8px",
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        transition: "all 0.2s"
                      }}
                    >
                      <div style={{ fontSize: "0.8rem", color: isSelected ? "#e0f2fe" : "#cbd5e1" }}>
                        Lat: {p.lat.toFixed(4)} <br/>
                        Lon: {p.lon.toFixed(4)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Interferogram list (after processing) */}
          {results && (
            <div style={{ marginTop: "20px" }}>
              <h3 style={{ fontSize: "0.85rem", color: "#94a3b8", marginBottom: "10px" }}>
                Interferogramas procesados:
              </h3>
              <div style={{ 
                display: "flex", 
                flexDirection: "column", 
                gap: "6px",
                maxHeight: "300px",
                overflowY: "auto",
                paddingRight: "6px"
              }}>
                {results.interferograms.map((ig, i) => (
                  <div
                    key={i}
                    style={{
                      padding: "8px 10px",
                      background: "rgba(99,102,241,0.08)",
                      border: "1px solid rgba(99,102,241,0.2)",
                      borderRadius: "8px",
                      fontSize: "0.75rem",
                    }}
                  >
                    <div style={{ color: "#a5b4fc", fontWeight: 600, marginBottom: "2px" }}>
                      {ig.date1} → {ig.date2}
                    </div>
                    <div style={{ color: "#64748b" }}>{ig.days} días | {ig.filename.slice(0, 40)}…</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right Panel: Results & Map */}
        <div>
          {!results && bounds ? (
            <div
              className="data-widget"
              style={{
                padding: "0",
                display: "flex",
                flexDirection: "column",
                height: "600px",
                borderRadius: "16px",
                overflow: "hidden",
                border: "1px solid rgba(255,255,255,0.1)",
              }}
            >
              <MapContainer
                center={[(bounds.lat_min + bounds.lat_max)/2, (bounds.lon_min + bounds.lon_max)/2]}
                zoom={8}
                style={{ height: "100%", width: "100%", background: "#1a1a1a" }}
              >
                <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
              <MapContent
                  bounds={bounds}
                  drawnBox={drawnBox}
                  setDrawnBox={setDrawnBox}
                  seedPoints={seedPoints}
                />
              </MapContainer>
            </div>
          ) : !results && !bounds ? (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                height: "600px",
                background: "rgba(255,255,255,0.02)",
                border: "1px solid rgba(255,255,255,0.06)",
                borderRadius: "16px",
                color: "#475569",
              }}
            >
              <div style={{ fontSize: "3rem", marginBottom: "12px" }}>🗺️</div>
              <p style={{ fontSize: "0.9rem" }}>
                Sube interferogramas para visualizar sus límites geográficos
              </p>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>

              {results && results.mode === "2D" && activeMethod === "mintpy" && (
                <div style={{ display: "flex", gap: "10px", marginBottom: "4px" }}>
                  <button
                    onClick={() => setViewMode("UP")}
                    style={{
                      flex: 1, padding: "8px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.1)",
                      background: viewMode === "UP" ? "rgba(56,189,248,0.2)" : "transparent",
                      color: viewMode === "UP" ? "#e0f2fe" : "#94a3b8", cursor: "pointer", fontSize: "0.85rem", fontWeight: 600
                    }}
                  >⬆️ Movimiento Vertical</button>
                  <button
                    onClick={() => setViewMode("EW")}
                    style={{
                      flex: 1, padding: "8px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.1)",
                      background: viewMode === "EW" ? "rgba(56,189,248,0.2)" : "transparent",
                      color: viewMode === "EW" ? "#e0f2fe" : "#94a3b8", cursor: "pointer", fontSize: "0.85rem", fontWeight: 600
                    }}
                  >↔️ Movimiento Este-Oeste</button>
                </div>
              )}

              {/* HyP3 comparison banner + method tabs */}
              {hasHyp3 && (
                <div style={{
                  padding: "12px 16px",
                  borderRadius: "12px",
                  background: "linear-gradient(135deg, rgba(245,158,11,0.12), rgba(249,115,22,0.08))",
                  border: "1px solid rgba(245,158,11,0.35)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "12px",
                  flexWrap: "wrap",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <span style={{ fontSize: "1.3rem" }}>🛰️</span>
                    <div>
                      <div style={{ fontWeight: 700, color: "#fbbf24", fontSize: "0.9rem" }}>Modo Comparación Activo</div>
                      <div style={{ fontSize: "0.75rem", color: "#d97706" }}>
                        Visualizando Fase 1 (Pre-inversión HyP3) vs Fase 2 (Post-inversión MintPy)
                      </div>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "8px" }}>
                    <button
                      id="tab-mintpy"
                      onClick={() => setActiveMethod("mintpy")}
                      style={{
                        padding: "8px 16px", borderRadius: "8px", border: "none", cursor: "pointer",
                        fontWeight: 700, fontSize: "0.82rem", transition: "all 0.2s",
                        background: activeMethod === "mintpy" ? "linear-gradient(135deg, #38bdf8, #6366f1)" : "rgba(255,255,255,0.07)",
                        color: activeMethod === "mintpy" ? "white" : "#94a3b8",
                      }}
                    >Fase 2: Post-inversión (MintPy)</button>
                    <button
                      id="tab-hyp3"
                      onClick={() => setActiveMethod("hyp3")}
                      style={{
                        padding: "8px 16px", borderRadius: "8px", border: "none", cursor: "pointer",
                        fontWeight: 700, fontSize: "0.82rem", transition: "all 0.2s",
                        background: activeMethod === "hyp3" ? "linear-gradient(135deg, #f59e0b, #ef4444)" : "rgba(255,255,255,0.07)",
                        color: activeMethod === "hyp3" ? "white" : "#94a3b8",
                      }}
                    >Fase 1: Pre-inversión (HyP3)</button>
                  </div>
                </div>
              )}

              {results && results.stats.phase_closure_skipped && (
                <div style={{
                  padding: "16px",
                  borderRadius: "12px",
                  background: "rgba(234,179,8,0.1)",
                  border: "1px solid rgba(234,179,8,0.3)",
                  color: "#fef08a",
                  fontSize: "0.85rem",
                  marginBottom: "4px",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "12px"
                }}>
                  <span style={{ fontSize: "1.4rem" }}>⚠️</span>
                  <div>
                    <strong style={{ display: "block", marginBottom: "4px", color: "#fde047" }}>Corrección de Clausura de Fase Omitida</strong>
                    El área solicitada carece de conexiones de geometría coherente suficiente para calcular la topología de la clausura de fase (Phase Closure). MintPy omitió esta corrección para no corromper la señal. La inversión continuó satisfactoriamente con la fase sin procesar.
                  </div>
                </div>
              )}

              {/* Stats cards — muestra el método activo */}
              {(() => {
                const s = activeMethod === "hyp3" && hasHyp3 ? results!.hyp3!.stats : results?.stats;
                const isEW = viewMode === "EW" && results?.mode === "2D" && activeMethod === "mintpy";
                const statRows = s ? [
                  { label: "Vel. Mínima", value: `${(isEW && (s as VelocityStats).min_ew !== undefined ? (s as VelocityStats).min_ew! : (s as VelocityStats).min).toFixed(2)} mm/a`, color: "#60a5fa" },
                  { label: "Vel. Máxima", value: `${(isEW && (s as VelocityStats).max_ew !== undefined ? (s as VelocityStats).max_ew! : (s as VelocityStats).max).toFixed(2)} mm/a`, color: "#f87171" },
                  { label: "Vel. Media", value: `${(isEW && (s as VelocityStats).mean_ew !== undefined ? (s as VelocityStats).mean_ew! : (s as VelocityStats).mean).toFixed(2)} mm/a`, color: "#34d399" },
                  { label: "Desv. Est.", value: `${(isEW && (s as VelocityStats).std_ew !== undefined ? (s as VelocityStats).std_ew! : (s as VelocityStats).std).toFixed(2)} mm/a`, color: "#a78bfa" },
                ] : [];
                const methodLabel = activeMethod === "hyp3" ? "Fase 1: Pre-inversión (HyP3)" : "Fase 2: Post-inversión (MintPy)";
                const methodColor = activeMethod === "hyp3" ? "rgba(245,158,11,0.12)" : "rgba(56,189,248,0.08)";
                const methodBorder = activeMethod === "hyp3" ? "rgba(245,158,11,0.25)" : "rgba(56,189,248,0.2)";
                return (
                  <div>
                    {hasHyp3 && (
                      <div style={{ fontSize: "0.75rem", color: activeMethod === "hyp3" ? "#f59e0b" : "#38bdf8", fontWeight: 700, marginBottom: "8px" }}>
                        {methodLabel} — Estadísticas de velocidad
                      </div>
                    )}
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "12px" }}>
                      {statRows.map((st) => (
                        <div
                          key={st.label}
                          style={{
                            background: methodColor,
                            border: `1px solid ${methodBorder}`,
                            borderRadius: "12px",
                            padding: "16px",
                            textAlign: "center",
                            transition: "all 0.3s",
                          }}
                        >
                          <div style={{ fontSize: "1.3rem", fontWeight: 700, color: st.color }}>{st.value}</div>
                          <div style={{ fontSize: "0.75rem", color: "#64748b", marginTop: "4px" }}>{st.label}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "20px" }}>
                {/* Velocity legend map */}
                <div
                  style={{
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    borderRadius: "14px",
                    padding: "20px",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      marginBottom: "14px",
                    }}
                  >
                    <h3 style={{ fontSize: "0.9rem", color: "#e2e8f0", margin: 0 }}>
                      🗺️ Mapa de Velocidad — {activeMethod === "hyp3" ? "Fase 1: Pre-inversión (HyP3)" : "Fase 2: Post-inversión (MintPy)"}
                    </h3>
                    <div style={{ display: "flex", gap: "8px" }}>
                      {activeMethod === "hyp3" && hasHyp3 ? (
                        <>
                          <a
                            href={`${API_URL}/api/mintpy/export_xlsx_hyp3`}
                            download
                            style={{ padding: "6px 14px", background: "linear-gradient(135deg, #f59e0b, #d97706)", color: "white", borderRadius: "8px", fontSize: "0.78rem", fontWeight: 600, textDecoration: "none" }}
                          >📊 XLSX Fase 1 (HyP3)</a>
                          <a
                            href={`${API_URL}/api/mintpy/export_csv_hyp3`}
                            download
                            style={{ padding: "6px 14px", background: "linear-gradient(135deg, #ef4444, #dc2626)", color: "white", borderRadius: "8px", fontSize: "0.78rem", fontWeight: 600, textDecoration: "none" }}
                          >⬇️ CSV Fase 1 (HyP3)</a>
                        </>
                      ) : (
                        <>
                          <a
                            href={`${API_URL}/api/mintpy/export_xlsx`}
                            download
                            style={{ padding: "6px 14px", background: "linear-gradient(135deg, #10b981, #059669)", color: "white", borderRadius: "8px", fontSize: "0.78rem", fontWeight: 600, textDecoration: "none" }}
                          >📊 XLSX Fase 2 (MintPy)</a>
                          <a
                            href={`${API_URL}/api/mintpy/export_csv`}
                            download
                            style={{ padding: "6px 14px", background: "linear-gradient(135deg, #6366f1, #4f46e5)", color: "white", borderRadius: "8px", fontSize: "0.78rem", fontWeight: 600, textDecoration: "none" }}
                          >⬇️ Exportar CSV</a>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Color scale strip */}
                  <div style={{ marginBottom: "10px", display: "flex", alignItems: "center", gap: "8px" }}>
                    <span style={{ fontSize: "0.7rem", color: "#64748b" }}>{velMin.toFixed(1)}</span>
                    <div
                      style={{
                        flex: 1,
                        height: "10px",
                        borderRadius: "6px",
                        background: `linear-gradient(to right, ${velocityColor(velMin, velMin, velMax)}, ${velocityColor((velMin + velMax) / 2, velMin, velMax)}, ${velocityColor(velMax, velMin, velMax)})`,
                      }}
                    />
                    <span style={{ fontSize: "0.7rem", color: "#64748b" }}>{velMax.toFixed(1)} mm/a</span>
                  </div>

                  {/* Map Layer Visualization */}
                  <div
                    style={{
                      position: "relative",
                      width: "100%",
                      height: "400px",
                      background: "rgba(0,0,0,0.3)",
                      borderRadius: "10px",
                      overflow: "hidden",
                      border: "1px solid rgba(255,255,255,0.05)",
                    }}
                  >
                    <MapContainer
                      center={
                        bounds 
                          ? [(bounds.lat_min + bounds.lat_max)/2, (bounds.lon_min + bounds.lon_max)/2] 
                          : [0, 0]
                      }
                      zoom={8}
                      style={{ height: "100%", width: "100%", background: "#1a1a1a" }}
                      zoomControl={false}
                    >
                      <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
                      <MapContent
                        bounds={bounds}
                        drawnBox={drawnBox}
                        setDrawnBox={setDrawnBox}
                        deformationData={activeData}
                        velMin={velMin}
                        velMax={velMax}
                      />
                    </MapContainer>
                  </div>

                  <div
                    style={{
                      marginTop: "12px",
                      fontSize: "0.7rem",
                      color: "#475569",
                      textAlign: "center",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: "10px"
                    }}
                  >
                    <span>Periodo: {results?.stats.date_start} → {results?.stats.date_end} | {results?.stats.n_interferograms} interferogramas</span>
                    {results?.stats.era5_successful !== undefined && (
                      <span style={{
                        padding: "2px 6px",
                        borderRadius: "4px",
                        background: "rgba(16,185,129,0.15)",
                        color: "#10b981",
                        fontWeight: 600
                      }}>
                        {results?.stats.tropo_method === "ERA5"
                          ? "☁️ ERA5 OK"
                          : `🌄 Troposf: ${results?.stats.tropo_method ?? "height_corr"}`}
                      </span>
                    )}
                  </div>
                </div>

                {/* Histogram */}
                <div
                  style={{
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    borderRadius: "14px",
                    padding: "20px",
                  }}
                >
                  <h3 style={{ fontSize: "0.9rem", color: "#e2e8f0", marginBottom: "14px" }}>
                    📊 Distribución de Velocidades {hasHyp3 ? "— MintPy vs HyP3" : ""}
                  </h3>
                  {hasHyp3 ? (
                    // Dual histogram: merge MintPy and HyP3 bins into one chart
                    <ResponsiveContainer width="100%" height={280}>
                      <BarChart
                        data={histogramData.map((b, i) => ({
                          range: b.range,
                          mintpy: b.count,
                          hyp3: hyp3HistogramData[i]?.count ?? 0,
                        }))}
                        margin={{ top: 4, right: 8, left: 0, bottom: 24 }}
                      >
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                        <XAxis dataKey="range" tick={{ fontSize: 9, fill: "#64748b" }} angle={-45} textAnchor="end"
                          label={{ value: "Velocidad (mm/a)", position: "insideBottom", offset: -16, fill: "#64748b", fontSize: 10 }} />
                        <YAxis tick={{ fontSize: 10, fill: "#64748b" }} />
                        <Tooltip
                          contentStyle={{ background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", color: "white", fontSize: "0.8rem" }}
                          formatter={(v: unknown, name: unknown) => [`${v} px`, name === "mintpy" ? "Fase 2: Post-inversión (MintPy)" : "Fase 1: Pre-inversión (HyP3)"]}
                        />
                        <Legend verticalAlign="top" height={36} wrapperStyle={{ fontSize: "0.8rem", color: "#94a3b8" }}
                          formatter={(v: string) => v === "mintpy" ? "Fase 2: Post-inversión (MintPy)" : "Fase 1: Pre-inversión (HyP3)"} />
                        <Bar dataKey="mintpy" fill="#6366f1" radius={[4, 4, 0, 0]} opacity={0.85} />
                        <Bar dataKey="hyp3" fill="#f59e0b" radius={[4, 4, 0, 0]} opacity={0.85} />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <ResponsiveContainer width="100%" height={280}>
                      <BarChart data={histogramData} margin={{ top: 4, right: 8, left: 0, bottom: 24 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                        <XAxis dataKey="range" tick={{ fontSize: 9, fill: "#64748b" }} angle={-45} textAnchor="end"
                          label={{ value: "Velocidad (mm/a)", position: "insideBottom", offset: -16, fill: "#64748b", fontSize: 10 }} />
                        <YAxis tick={{ fontSize: 10, fill: "#64748b" }} />
                        <Tooltip
                          contentStyle={{ background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", color: "white", fontSize: "0.8rem" }}
                          formatter={(v: number | string | React.ReactNode) => [`${v} píxeles`, "Frecuencia"]}
                        />
                        <Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>



              {/* Line Chart for Igram Stats */}
              {(() => {
                const igStats = activeMethod === "hyp3" && hasHyp3
                  ? results!.hyp3!.igram_stats
                  : results?.igram_stats;
                if (!igStats || igStats.length === 0) return null;
                const lineColor = activeMethod === "hyp3" ? "#f59e0b" : "#38bdf8";
                const maxColor = activeMethod === "hyp3" ? "#ef4444" : "#f87171";
                const minColor = activeMethod === "hyp3" ? "#84cc16" : "#34d399";
                return (
                  <div style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: "14px", padding: "20px" }}>
                    <h3 style={{ fontSize: "0.9rem", color: "#e2e8f0", marginBottom: "14px" }}>
                      📈 Velocidad Media por Par de Fechas — {activeMethod === "hyp3" ? "Fase 1: Pre-inversión (HyP3)" : "Fase 2: Post-inversión (MintPy)"}
                    </h3>
                    <ResponsiveContainer width="100%" height={280}>
                      <LineChart data={igStats} margin={{ top: 4, right: 8, left: 0, bottom: 24 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                        <XAxis dataKey="label" tick={{ fontSize: 9, fill: "#64748b" }} angle={-45} textAnchor="end" />
                        <YAxis tick={{ fontSize: 10, fill: "#64748b" }} />
                        <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(255,255,255,0.1)", borderRadius: "8px", color: "white", fontSize: "0.8rem" }} />
                        <Legend verticalAlign="top" height={36} wrapperStyle={{ fontSize: "0.8rem", color: "#94a3b8" }} />
                        <Line type="monotone" name="Media (mm)" dataKey="mean" stroke={lineColor} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                        <Line type="monotone" name="Max (mm)" dataKey="max" stroke={maxColor} strokeWidth={1} strokeDasharray="3 3" dot={false} />
                        <Line type="monotone" name="Min (mm)" dataKey="min" stroke={minColor} strokeWidth={1} strokeDasharray="3 3" dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                );
              })()}

              {/* Visualización del Estado de Fase (Wrapped vs Unwrapped vs Corrected) */}
              {results && results.phase_previews && (
                <div
                  style={{
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    borderRadius: "14px",
                    padding: "20px",
                    marginTop: "20px",
                  }}
                >
                  <h3 style={{ fontSize: "0.9rem", color: "#e2e8f0", marginBottom: "14px" }}>
                    🔍 Visualización del Estado de la Fase Radar — Par: {results.phase_previews.pair_label}
                  </h3>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "16px" }}>
                    {/* Fase Envuelta */}
                    <div style={{ textAlign: "center" }}>
                      <div style={{ fontSize: "0.8rem", color: "#cbd5e1", marginBottom: "8px", fontWeight: 600 }}>
                        Fase Envuelta (Wrapped)
                      </div>
                      <div style={{ background: "rgba(0,0,0,0.2)", borderRadius: "10px", padding: "8px", border: "1px solid rgba(255,255,255,0.05)" }}>
                        {results.phase_previews.wrapped_url ? (
                          <>
                            <img 
                              src={`${API_URL}${results.phase_previews.wrapped_url}`} 
                              alt="Fase Envuelta" 
                              style={{ width: "100%", height: "200px", objectFit: "contain", borderRadius: "6px" }} 
                            />
                            <a
                              href={`${API_URL}${results.phase_previews.wrapped_url}`}
                              download={`fase_envuelta_${results.phase_previews.pair_label.replace(" → ", "_")}.png`}
                              target="_blank"
                              rel="noopener noreferrer"
                              style={{
                                display: "inline-block",
                                marginTop: "8px",
                                padding: "6px 12px",
                                background: "rgba(255, 255, 255, 0.08)",
                                border: "1px solid rgba(255, 255, 255, 0.15)",
                                borderRadius: "6px",
                                color: "#cbd5e1",
                                fontSize: "0.72rem",
                                fontWeight: 600,
                                textDecoration: "none",
                                transition: "all 0.2s",
                                cursor: "pointer",
                                width: "calc(100% - 24px)",
                                boxSizing: "border-box"
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = "rgba(255, 255, 255, 0.15)";
                                e.currentTarget.style.color = "white";
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = "rgba(255, 255, 255, 0.08)";
                                e.currentTarget.style.color = "#cbd5e1";
                              }}
                            >
                              ⬇️ Descargar Fase Envuelta
                            </a>
                          </>
                        ) : (
                          <div style={{ height: "200px", display: "flex", alignItems: "center", justifyContent: "center", color: "#64748b", fontSize: "0.75rem" }}>
                            No disponible
                          </div>
                        )}
                      </div>
                      <p style={{ color: "#64748b", fontSize: "0.7rem", marginTop: "8px", lineHeight: "1.3" }}>
                        Fase interferométrica cruda de $-\pi$ a $+\pi$ radianes. Muestra patrones cíclicos (franjas).
                      </p>
                    </div>

                    {/* Fase Desenvuelta */}
                    <div style={{ textAlign: "center" }}>
                      <div style={{ fontSize: "0.8rem", color: "#cbd5e1", marginBottom: "8px", fontWeight: 600 }}>
                        Fase Desenvuelta (Unwrapped)
                      </div>
                      <div style={{ background: "rgba(0,0,0,0.2)", borderRadius: "10px", padding: "8px", border: "1px solid rgba(255,255,255,0.05)" }}>
                        {results.phase_previews.unwrapped_url ? (
                          <>
                            <img 
                              src={`${API_URL}${results.phase_previews.unwrapped_url}`} 
                              alt="Fase Desenvuelta" 
                              style={{ width: "100%", height: "200px", objectFit: "contain", borderRadius: "6px" }} 
                            />
                            <a
                              href={`${API_URL}${results.phase_previews.unwrapped_url}`}
                              download={`fase_desenvuelta_${results.phase_previews.pair_label.replace(" → ", "_")}.png`}
                              target="_blank"
                              rel="noopener noreferrer"
                              style={{
                                display: "inline-block",
                                marginTop: "8px",
                                padding: "6px 12px",
                                background: "rgba(255, 255, 255, 0.08)",
                                border: "1px solid rgba(255, 255, 255, 0.15)",
                                borderRadius: "6px",
                                color: "#cbd5e1",
                                fontSize: "0.72rem",
                                fontWeight: 600,
                                textDecoration: "none",
                                transition: "all 0.2s",
                                cursor: "pointer",
                                width: "calc(100% - 24px)",
                                boxSizing: "border-box"
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = "rgba(255, 255, 255, 0.15)";
                                e.currentTarget.style.color = "white";
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = "rgba(255, 255, 255, 0.08)";
                                e.currentTarget.style.color = "#cbd5e1";
                              }}
                            >
                              ⬇️ Descargar Fase Desenvuelta
                            </a>
                          </>
                        ) : (
                          <div style={{ height: "200px", display: "flex", alignItems: "center", justifyContent: "center", color: "#64748b", fontSize: "0.75rem" }}>
                            No disponible
                          </div>
                        )}
                      </div>
                      <p style={{ color: "#64748b", fontSize: "0.7rem", marginTop: "8px", lineHeight: "1.3" }}>
                        Fase desenrollada de forma continua por SNAPHU (HyP3), representando distancia física y ruido.
                      </p>
                    </div>

                    {/* Fase Corregida */}
                    <div style={{ textAlign: "center" }}>
                      <div style={{ fontSize: "0.8rem", color: "#cbd5e1", marginBottom: "8px", fontWeight: 600 }}>
                        Fase Corregida (MintPy)
                      </div>
                      <div style={{ background: "rgba(0,0,0,0.2)", borderRadius: "10px", padding: "8px", border: "1px solid rgba(255,255,255,0.05)" }}>
                        {results.phase_previews.corrected_url ? (
                          <>
                            <img 
                              src={`${API_URL}${results.phase_previews.corrected_url}`} 
                              alt="Fase Corregida" 
                              style={{ width: "100%", height: "200px", objectFit: "contain", borderRadius: "6px" }} 
                            />
                            <a
                              href={`${API_URL}${results.phase_previews.corrected_url}`}
                              download={`fase_corregida_${results.phase_previews.pair_label.replace(" → ", "_")}.png`}
                              target="_blank"
                              rel="noopener noreferrer"
                              style={{
                                display: "inline-block",
                                marginTop: "8px",
                                padding: "6px 12px",
                                background: "rgba(255, 255, 255, 0.08)",
                                border: "1px solid rgba(255, 255, 255, 0.15)",
                                borderRadius: "6px",
                                color: "#cbd5e1",
                                fontSize: "0.72rem",
                                fontWeight: 600,
                                textDecoration: "none",
                                transition: "all 0.2s",
                                cursor: "pointer",
                                width: "calc(100% - 24px)",
                                boxSizing: "border-box"
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = "rgba(255, 255, 255, 0.15)";
                                e.currentTarget.style.color = "white";
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = "rgba(255, 255, 255, 0.08)";
                                e.currentTarget.style.color = "#cbd5e1";
                              }}
                            >
                              ⬇️ Descargar Fase Corregida
                            </a>
                          </>
                        ) : (
                          <div style={{ height: "200px", display: "flex", alignItems: "center", justifyContent: "center", color: "#64748b", fontSize: "0.75rem" }}>
                            No disponible
                          </div>
                        )}
                      </div>
                      <p style={{ color: "#64748b", fontSize: "0.7rem", marginTop: "8px", lineHeight: "1.3" }}>
                        Deformación del par limpia en milímetros tras remover atmósfera y rampas orbitales en MintPy.
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {/* Data table */}
              <div
                style={{
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: "14px",
                  padding: "20px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "14px",
                  }}
                >
                  <h3 style={{ fontSize: "0.9rem", color: "#e2e8f0", margin: 0 }}>
                    📋 Muestra de Resultados — {activeMethod === "hyp3" ? "Fase 1: Pre-inversión (HyP3)" : "Fase 2: Post-inversión (MintPy)"}
                    <span style={{ marginLeft: "10px", fontSize: "0.75rem", color: "#64748b", fontWeight: "normal" }}>
                      ({activeSample.length} de {((activeMethod === "hyp3" && hasHyp3 ? results!.hyp3!.stats.n_points : results?.stats?.n_points) || 0).toLocaleString()} puntos)
                    </span>
                  </h3>
                  <div style={{ display: "flex", gap: "8px" }}>
                    {activeMethod === "hyp3" && hasHyp3 ? (
                      <>
                        <a href={`${API_URL}/api/mintpy/export_xlsx_hyp3`} download
                          style={{ padding: "6px 14px", background: "linear-gradient(135deg, #f59e0b, #d97706)", color: "white", borderRadius: "8px", fontSize: "0.78rem", fontWeight: 600, textDecoration: "none" }}>
                          📊 XLSX Fase 1 (HyP3)
                        </a>
                        <a href={`${API_URL}/api/mintpy/export_csv_hyp3`} download
                          style={{ padding: "6px 14px", background: "linear-gradient(135deg, #ef4444, #dc2626)", color: "white", borderRadius: "8px", fontSize: "0.78rem", fontWeight: 600, textDecoration: "none" }}>
                          ⬇️ CSV Fase 1 (HyP3)
                        </a>
                      </>
                    ) : (
                      <>
                        <a href={`${API_URL}/api/mintpy/export_xlsx`} download
                          style={{ padding: "6px 14px", background: "linear-gradient(135deg, #10b981, #059669)", color: "white", borderRadius: "8px", fontSize: "0.78rem", fontWeight: 600, textDecoration: "none" }}>
                          📊 XLSX Fase 2 (MintPy)
                        </a>
                        <a href={`${API_URL}/api/mintpy/export_csv`} download
                          style={{ padding: "6px 14px", background: "linear-gradient(135deg, #6366f1, #4f46e5)", color: "white", borderRadius: "8px", fontSize: "0.78rem", fontWeight: 600, textDecoration: "none" }}>
                          ⬇️ CSV Fase 2 (MintPy)
                        </a>
                      </>
                    )}
                  </div>
                </div>

                <div style={{ overflowX: "auto", maxHeight: "340px", overflowY: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
                    <thead>
                      <tr style={{ background: "rgba(255,255,255,0.04)", position: "sticky", top: 0 }}>
                        {["#", "Latitud", "Longitud", "Velocidad (mm/año)"].map((h) => (
                          <th key={h} style={{ padding: "10px 12px", textAlign: "left", color: "#94a3b8", fontWeight: 600, fontSize: "0.78rem", borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {activeSample.map((p, idx) => {
                        const displayVal = activeMethod === "hyp3" || results?.mode !== "2D"
                          ? p.velocidad_mm_yr
                          : viewMode === "EW" ? (p.vel_ew_mm_yr || 0) : (p.vel_up_mm_yr || 0);
                        const color = velocityColor(displayVal, velMin, velMax);
                        return (
                          <tr
                            key={idx}
                            style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}
                          >
                            <td style={{ padding: "8px 12px", color: "#475569" }}>{idx + 1}</td>
                            <td style={{ padding: "8px 12px", color: "#94a3b8" }}>{p.lat.toFixed(6)}</td>
                            <td style={{ padding: "8px 12px", color: "#94a3b8" }}>{p.lon.toFixed(6)}</td>
                            <td style={{ padding: "8px 12px", fontWeight: 700, color }}>
                              {(displayVal > 0 ? "+" : "") + displayVal.toFixed(2)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {activeSample.length < (activeMethod === "hyp3" && hasHyp3 ? results!.hyp3!.stats.n_points : results?.stats?.n_points || 0) && (
                  <p style={{ textAlign: "center", color: "#475569", fontSize: "0.75rem", marginTop: "12px", fontStyle: "italic" }}>
                    Mostrando muestra de {activeSample.length} puntos. Descarga el CSV para el dataset completo.
                  </p>
                )}
              </div>

              {/* ── Resumen del procesamiento MintPy ─────────────────── */}
              {results && activeMethod === "mintpy" && (
                <div
                  style={{
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    borderRadius: "14px",
                    padding: "20px",
                  }}
                >
                  <h3 style={{ fontSize: "0.9rem", color: "#e2e8f0", marginBottom: "16px", display: "flex", alignItems: "center", gap: "8px" }}>
                    🔬 Resumen del Procesamiento MintPy
                  </h3>

                  {/* Two-column info grid */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "16px" }}>
                    {/* Tropo correction */}
                    <div style={{
                      padding: "12px 16px",
                      borderRadius: "10px",
                      background: results.stats.tropo_method === "ERA5"
                        ? "rgba(16,185,129,0.10)"
                        : "rgba(245,158,11,0.10)",
                      border: `1px solid ${
                        results.stats.tropo_method === "ERA5"
                          ? "rgba(16,185,129,0.3)"
                          : "rgba(245,158,11,0.3)"
                      }`,
                    }}>
                      <div style={{ fontSize: "0.72rem", color: "#64748b", marginBottom: "4px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                        Corrección Atmosférica
                      </div>
                      <div style={{
                        fontSize: "1rem",
                        fontWeight: 700,
                        color: results.stats.tropo_method === "ERA5" ? "#34d399" : "#fbbf24",
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                      }}>
                        {results.stats.tropo_method === "ERA5" ? "☁️" : "🌄"}
                        {results.stats.tropo_method === "ERA5"
                          ? "PyAPS / ERA5 (exitoso)"
                          : "height_correlation (fallback)"}
                      </div>
                      <div style={{ fontSize: "0.72rem", color: "#64748b", marginTop: "4px" }}>
                        {results.stats.tropo_method === "ERA5"
                          ? "Corrección troposférica con reanálisis ERA5 de ECMWF."
                          : "ERA5 falló o no disponible. Se usó correlación con altura del terreno."}
                      </div>
                    </div>

                    {/* Min coherence threshold */}
                    <div style={{
                      padding: "12px 16px",
                      borderRadius: "10px",
                      background: "rgba(99,102,241,0.10)",
                      border: "1px solid rgba(99,102,241,0.3)",
                    }}>
                      <div style={{ fontSize: "0.72rem", color: "#64748b", marginBottom: "4px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                        Umbral de Coherencia de Red
                      </div>
                      <div style={{ fontSize: "1rem", fontWeight: 700, color: "#a5b4fc" }}>
                        ≥ {(results.stats.min_coherence ?? 0.6).toFixed(2)}
                      </div>
                      <div style={{ fontSize: "0.72rem", color: "#64748b", marginTop: "4px" }}>
                        Interferogramas con coherencia espacial media inferior a este valor fueron excluidos de la red SBAS.
                      </div>
                    </div>
                  </div>

                  {/* Excluded interferograms list */}
                  {(() => {
                    const excl = results.stats.excluded_ifgrams ?? [];
                    return (
                      <div>
                        <div style={{
                          fontSize: "0.8rem",
                          fontWeight: 600,
                          color: excl.length > 0 ? "#fbbf24" : "#34d399",
                          marginBottom: "10px",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                        }}>
                          {excl.length > 0 ? "⚠️" : "✅"}
                          {excl.length > 0
                            ? `${excl.length} interferograma(s) descartado(s) por baja coherencia`
                            : "Todos los interferogramas fueron incluidos en la red SBAS"}
                        </div>
                        {excl.length > 0 && (
                          <div style={{
                            display: "flex",
                            flexDirection: "column",
                            gap: "6px",
                            maxHeight: "260px",
                            overflowY: "auto",
                            paddingRight: "4px",
                          }}>
                            {excl.map((ig, idx) => (
                              <div
                                key={idx}
                                style={{
                                  display: "grid",
                                  gridTemplateColumns: "auto 1fr auto",
                                  alignItems: "center",
                                  gap: "10px",
                                  padding: "8px 12px",
                                  background: "rgba(245,158,11,0.06)",
                                  border: "1px solid rgba(245,158,11,0.2)",
                                  borderRadius: "8px",
                                  fontSize: "0.78rem",
                                }}
                              >
                                <span style={{ color: "#f59e0b", fontWeight: 700, minWidth: "20px", textAlign: "center" }}>{idx + 1}</span>
                                <div>
                                  <div style={{ color: "#fbbf24", fontWeight: 600 }}>
                                    {ig.date1} → {ig.date2}
                                  </div>
                                  <div style={{ color: "#64748b", fontSize: "0.72rem", marginTop: "2px" }}>
                                    {ig.days} días · {ig.reason}
                                  </div>
                                </div>
                                <span style={{
                                  padding: "2px 8px",
                                  background: "rgba(239,68,68,0.15)",
                                  border: "1px solid rgba(239,68,68,0.3)",
                                  borderRadius: "4px",
                                  color: "#fca5a5",
                                  fontSize: "0.7rem",
                                  fontWeight: 600,
                                  whiteSpace: "nowrap",
                                }}>Descartado</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                </div>
              )}

              {/* Static Vector Map (Quiver) */}
              {results?.mode === "2D" && (
                <div
                  style={{
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    borderRadius: "14px",
                    padding: "20px",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      marginBottom: "14px",
                    }}
                  >
                    <h3 style={{ fontSize: "0.9rem", color: "#e2e8f0", margin: 0 }}>
                      🧭 Mapa Vectorial Estático (Cartopy Quiver)
                    </h3>
                    <a
                      href={`${API_URL}/api/mintpy/export_quiver_${viewMode.toLowerCase()}`}
                      download
                      style={{
                        padding: "6px 14px",
                        background: "linear-gradient(135deg, #8b5cf6, #7c3aed)",
                        color: "white",
                        borderRadius: "8px",
                        fontSize: "0.78rem",
                        fontWeight: 600,
                        textDecoration: "none",
                      }}
                    >
                      📸 Descargar Mapa
                    </a>
                  </div>
                  <div style={{ textAlign: "center", background: "rgba(0,0,0,0.2)", borderRadius: "10px", padding: "10px", border: "1px solid rgba(255,255,255,0.05)" }}>
                    <img 
                      src={`${API_URL}/api/mintpy/export_quiver_${viewMode.toLowerCase()}?t=${results?.stats?.date_end || ''}`} 
                      alt={`Mapa Vectorial ${viewMode}`} 
                      style={{ maxWidth: "100%", maxHeight: "600px", borderRadius: "6px", objectFit: "contain" }}
                    />
                    <p style={{ color: "#94a3b8", fontSize: "0.75rem", marginTop: "10px", fontStyle: "italic" }}>
                      Mostrando estadísticamente ~33% de los vectores calculados para preservar las direcciones y claridad visual (Proyección PlateCarree).
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Monitor de Recursos del Servidor en el Pie de Página */}
      {systemStatus && (
        <div
          style={{
            marginTop: "32px",
            padding: "16px 20px",
            background: "rgba(0, 0, 0, 0.25)",
            borderRadius: "14px",
            border: `1px solid ${
              systemStatus.ram.percent > 85 || systemStatus.disk.percent > 90
                ? "rgba(239, 68, 68, 0.4)"
                : "rgba(255, 255, 255, 0.06)"
            }`,
            width: "50%",
            maxWidth: "600px",
            boxSizing: "border-box",
            marginRight: "auto"
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "14px",
            }}
          >
            <span style={{ fontSize: "0.85rem", fontWeight: 700, color: "#cbd5e1", display: "flex", alignItems: "center", gap: "6px" }}>
              💻 Recursos del Servidor
            </span>
            <span
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                background: systemStatus.ram.percent > 85 ? "#ef4444" : "#10b981",
                boxShadow: `0 0 8px ${systemStatus.ram.percent > 85 ? "#ef4444" : "#10b981"}`,
                display: "inline-block",
              }}
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px" }}>
            {/* RAM stats */}
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: "#94a3b8", marginBottom: "6px" }}>
                <span>Memoria RAM</span>
                <span style={{ fontWeight: 600, color: systemStatus.ram.percent > 85 ? "#f87171" : "#cbd5e1" }}>
                  {systemStatus.ram.percent}% ({systemStatus.ram.available_gb} GB libres)
                </span>
              </div>
              <div style={{ height: "6px", background: "rgba(255,255,255,0.08)", borderRadius: "3px", overflow: "hidden" }}>
                <div
                  style={{
                    height: "100%",
                    width: `${systemStatus.ram.percent}%`,
                    background: systemStatus.ram.percent > 85 ? "#ef4444" : systemStatus.ram.percent > 70 ? "#f59e0b" : "#10b981",
                    transition: "width 0.5s ease",
                  }}
                />
              </div>
              {systemStatus.ram.percent > 85 && (
                <div style={{ marginTop: "6px", fontSize: "0.7rem", color: "#f87171", lineHeight: "1.3" }}>
                  ⚠️ <strong>RAM crítica:</strong> Windows podría terminar repentinamente la carga o ralentizarla severamente por paginación.
                </div>
              )}
            </div>

            {/* Disk stats */}
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: "#94a3b8", marginBottom: "6px" }}>
                <span>Almacenamiento Libre</span>
                <span style={{ fontWeight: 600, color: systemStatus.disk.free_gb < 5 ? "#f87171" : "#cbd5e1" }}>
                  {systemStatus.disk.free_gb} GB ({100 - systemStatus.disk.percent}% libre)
                </span>
              </div>
              <div style={{ height: "6px", background: "rgba(255,255,255,0.08)", borderRadius: "3px", overflow: "hidden" }}>
                <div
                  style={{
                    height: "100%",
                    width: `${systemStatus.disk.percent}%`,
                    background: systemStatus.disk.percent > 90 ? "#ef4444" : "#38bdf8",
                    transition: "width 0.5s ease",
                  }}
                />
              </div>
            </div>
          </div>
        </div>
      )}
      {/* ── Visualización Científica Paso a Paso ────────────────────────────────── */}
      <div style={{ marginTop: "32px" }}>
        <ScientificVisualizationPanel />
      </div>
    </div>
  );
}
