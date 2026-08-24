import { useState, useRef, useEffect, useCallback } from "react";
import Navbar from "./components/Navbar";
import BarraSuperior from "./components/BarraSuperior";
import JobStatusPanel from "./components/JobStatusPanel";


import AlaskaSearch from "./pages/Alaska/AlaskaSearch";
import type { PathFrameOption } from "./pages/Alaska/MapComponent";
import SentinelDashboard from "./pages/Alaska/SentinelDashboard";
import DownloadFiles from "./pages/Alaska/DownloadFiles";
import SolicitarImagenesAutomatico from "./pages/SolicitarImagenesAutomatico/SolicitarImagenesAutomatico";

import MintPyAnalysis from "./pages/MintPy/MintPyAnalysis";
import EqInsarAnalysis from "./pages/EqInsar/EqInsarAnalysis";

import Inicio from "./pages/Home/Home";
import LoginPage from "./pages/Login/LoginPage";
import api, { API_URL } from "./services/api";
import type { Scene } from "./pages/Alaska/AlaskaContent";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { AlertTriangle, LogIn, X } from "lucide-react";

const SIDEBAR_WIDTH = 300;
const APP_BG = "var(--color-bg-main)";

const AUTH_REQUIRED_SECTIONS = new Set([
  "solicitud-imagenes",
  "solicitud-automatico",
  "descarga-imagenes",
  "alaska",
]);

const GUEST_PARTIAL_SECTIONS = new Set(["mintpy-analysis"]);

interface WarningBannerProps {
  message: string;
  onDismiss: () => void;
}

const WarningBanner: React.FC<WarningBannerProps> = ({ message, onDismiss }) => (
  <div
    style={{
      display: "flex",
      alignItems: "flex-start",
      gap: "0.75rem",
      background: "rgba(234, 179, 8, 0.1)",
      border: "1px solid rgba(234, 179, 8, 0.3)",
      borderRadius: "var(--radius-md)",
      padding: "0.9rem 1rem",
      marginBottom: "1.25rem",
      color: "#fde047",
      fontSize: "0.85rem",
      lineHeight: 1.5,
    }}
  >
    <AlertTriangle size={18} style={{ flexShrink: 0, marginTop: 1, color: "#facc15" }} />
    <span style={{ flex: 1 }}>{message}</span>
    <button
      onClick={onDismiss}
      style={{
        background: "none",
        border: "none",
        color: "#fde047",
        cursor: "pointer",
        padding: "2px",
        display: "flex",
        flexShrink: 0,
        opacity: 0.7,
      }}
      aria-label="Cerrar aviso"
    >
      <X size={16} />
    </button>
  </div>
);

const AuthBlocker: React.FC<{ onGoToLogin: () => void }> = ({ onGoToLogin }) => (
  <div
    style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      minHeight: "60vh",
      gap: "1.5rem",
      textAlign: "center",
      padding: "2rem",
    }}
  >
    <div
      style={{
        width: 72,
        height: 72,
        borderRadius: "var(--radius-lg)",
        background: "rgba(239, 68, 68, 0.1)",
        border: "1px solid rgba(239, 68, 68, 0.2)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <AlertTriangle size={36} color="#f87171" />
    </div>
    <div>
      <h2 style={{ margin: "0 0 0.5rem", color: "var(--color-text-main)", fontSize: "1.2rem" }}>
        Acceso restringido
      </h2>
      <p style={{ margin: 0, color: "var(--color-text-muted)", fontSize: "0.9rem", maxWidth: 380 }}>
        Esta sección requiere credenciales de <strong style={{ color: "var(--color-text-main)" }}>HyP3</strong>.
        Inicia sesión para continuar.
      </p>
    </div>
    <button
      id="auth-blocker-login-btn"
      onClick={onGoToLogin}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.6rem",
        padding: "0.7rem 1.5rem",
        background: "linear-gradient(135deg, var(--color-primary) 0%, #00b4d8 100%)",
        color: "#020617",
        border: "none",
        borderRadius: "var(--radius-md)",
        fontFamily: "var(--font-family)",
        fontSize: "0.9rem",
        fontWeight: 600,
        cursor: "pointer",
        boxShadow: "0 0 20px rgba(0, 229, 255, 0.25)",
        transition: "all 0.2s",
      }}
    >
      <LogIn size={18} />
      Iniciar Sesión
    </button>
  </div>
);

const AppInner: React.FC = () => {
  const { mode, isGuest } = useAuth();
  const [activeSection, setActiveSection] = useState<string>("inicio");
  const [, setActivePage] = useState<string>("");
  const [isHeaderHidden, setIsHeaderHidden] = useState<boolean>(false);
  const [showWarningBanner, setShowWarningBanner] = useState(false);

  const contentRef = useRef<HTMLDivElement | null>(null);
  const lastScrollTopRef = useRef<number>(0);

  const [polygonWKT, setPolygonWKT] = useState<string>("");
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2025-01-15");
  const [ruta, setRuta] = useState<number | null>(null);
  const [marco, setMarco] = useState<number | null>(null);
  const [flightDirection, setFlightDirection] = useState<"ASCENDING" | "DESCENDING" | "">("");
  const [polarization, setPolarization] = useState<string>("");
  const [dayInterval, setDayInterval] = useState<number>(12);
  const [pathFrameOptions, setPathFrameOptions] = useState<PathFrameOption[]>([]);
  const [pathFrameLoading, setPathFrameLoading] = useState(false);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [lastCount, setLastCount] = useState<number>(0);

  // ── Navigation with access control ─────────────────────────────────────────
  const handleChangeSection = (section: string) => {
    // Guest trying to access auth-required section → show blocker
    if (isGuest && AUTH_REQUIRED_SECTIONS.has(section)) {
      setActiveSection("login-redirect");
      setActivePage("");
      return;
    }

    // Guest accessing mintpy → show banner, allow access
    if (isGuest && GUEST_PARTIAL_SECTIONS.has(section)) {
      setShowWarningBanner(true);
    }

    setActiveSection(section);
    setActivePage("");
  };

  // ── Path/frame discovery ────────────────────────────────────────────────────
  const discoverPaths = useCallback(async (polygon: string) => {
    if (!polygon) {
      setPathFrameOptions([]);
      setRuta(null);
      setMarco(null);
      return;
    }
    try {
      setPathFrameLoading(true);
      setPathFrameOptions([]);
      setRuta(null);
      setMarco(null);
      const body = {
        polygon,
        start_date: `${startDate}T00:00:00Z`,
        end_date: `${endDate}T23:59:59Z`,
        beam_mode: "IW",
        processing_level: "SLC",
        flight_direction: flightDirection || undefined,
        polarization: polarization || undefined,
      };
      const res = await api.post<PathFrameOption[]>("/api/discover_paths", body);
      const options = res.data;
      setPathFrameOptions(options);
      const preferred = options.find((o) => o.is_preferred) ?? options[0];
      if (preferred) {
        setRuta(preferred.ruta);
        setMarco(preferred.marco);
      }
    } catch (e) {
      console.error("[App] discover_paths error:", e);
    } finally {
      setPathFrameLoading(false);
    }
  }, [startDate, endDate, flightDirection, polarization]);

  const handlePolygonChange = useCallback(
    (wkt: string) => {
      setPolygonWKT(wkt);
      discoverPaths(wkt);
    },
    [discoverPaths]
  );

  const handlePathFrameSelect = useCallback((r: number, m: number) => {
    setRuta(r);
    setMarco(m);
    setScenes([]);
    setLastCount(0);
  }, []);

  const resetDiscovery = useCallback(() => {
    setPathFrameOptions([]);
    setPathFrameLoading(false);
    setPolygonWKT("");
    setRuta(null);
    setMarco(null);
    setScenes([]);
    setLastCount(0);
    setError(null);
  }, []);

  const handleSetStartDate = useCallback((v: string) => { resetDiscovery(); setStartDate(v); }, [resetDiscovery]);
  const handleSetEndDate   = useCallback((v: string) => { resetDiscovery(); setEndDate(v); }, [resetDiscovery]);
  const handleSetFlightDirection = useCallback((v: "ASCENDING" | "DESCENDING" | "") => { resetDiscovery(); setFlightDirection(v); }, [resetDiscovery]);
  const handleSetPolarization    = useCallback((v: string) => { resetDiscovery(); setPolarization(v); }, [resetDiscovery]);
  const handleSetDayInterval     = useCallback((v: number) => { setDayInterval(v); }, []);

  const fieldsLocked    = pathFrameOptions.length > 0 || pathFrameLoading;
  const drawingEnabled  = !!flightDirection && !!polarization && !!startDate && !!endDate;

  useEffect(() => {
    const el = contentRef.current;
    if (!el) return;
    const handleScroll = () => {
      const currentScroll = el.scrollTop;
      if (currentScroll > lastScrollTopRef.current && currentScroll > 50) {
        setIsHeaderHidden(true);
      } else {
        setIsHeaderHidden(false);
      }
      lastScrollTopRef.current = currentScroll;
    };
    el.addEventListener("scroll", handleScroll);
    return () => el.removeEventListener("scroll", handleScroll);
  }, []);

  const searchScenes = async () => {
    try {
      setError(null);
      setLoading(true);
      if (!polygonWKT) throw new Error("Dibuja el área de interés en el mapa.");
      if (ruta == null || marco == null)
        throw new Error("Selecciona una ruta/marco en el mapa haciendo clic en uno de los rectángulos.");
      const body = {
        polygon: polygonWKT,
        start_date: `${startDate}T00:00:00Z`,
        end_date: `${endDate}T23:59:59Z`,
        ruta, marco,
        beam_mode: "IW",
        processing_level: "SLC",
        day_interval: dayInterval,
        same_platform: true,
        flight_direction: flightDirection || undefined,
        polarization: polarization || undefined,
      };
      const res = await api.post("/api/search", body);
      setScenes(res.data);
      setLastCount(res.data.length);
    } catch (e: unknown) {
      if (e instanceof Error) setError(e.message);
      else setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const renderContent = () => {
    if (activeSection === "login-redirect") {
      return <AuthBlocker onGoToLogin={() => setActiveSection("inicio")} />;
    }

    switch (activeSection) {
      case "inicio":
        return <Inicio onNavigate={handleChangeSection} />;

      case "alaska":
        if (isGuest) return <AuthBlocker onGoToLogin={() => setActiveSection("inicio")} />;
        return <div>Sección Alaska (elige una opción del submenú)</div>;

      case "solicitud-imagenes":
        if (isGuest) return <AuthBlocker onGoToLogin={() => setActiveSection("inicio")} />;
        return (
          <div>
            <AlaskaSearch
              polygonWKT={polygonWKT}
              setPolygonWKT={handlePolygonChange}
              startDate={startDate}
              endDate={endDate}
              setStartDate={handleSetStartDate}
              setEndDate={handleSetEndDate}
              ruta={ruta}
              marco={marco}
              flightDirection={flightDirection}
              setFlightDirection={handleSetFlightDirection}
              polarization={polarization}
              setPolarization={handleSetPolarization}
              dayInterval={dayInterval}
              setDayInterval={handleSetDayInterval}
              onSearch={searchScenes}
              loading={loading}
              error={error}
              lastCount={lastCount}
              pathFrameOptions={pathFrameOptions}
              pathFrameLoading={pathFrameLoading}
              onPathFrameSelect={handlePathFrameSelect}
              fieldsLocked={fieldsLocked}
              drawingEnabled={drawingEnabled}
              onResetDiscovery={resetDiscovery}
            />
            <SentinelDashboard
              scenes={scenes}
              backendUrl={API_URL}
              ruta={ruta ?? undefined}
              marco={marco ?? undefined}
              dayInterval={dayInterval}
            />
          </div>
        );

      case "descarga-imagenes":
        if (isGuest) return <AuthBlocker onGoToLogin={() => setActiveSection("inicio")} />;
        return <div><DownloadFiles /></div>;

      case "solicitud-automatico":
        if (isGuest) return <AuthBlocker onGoToLogin={() => setActiveSection("inicio")} />;
        return <div><SolicitarImagenesAutomatico /></div>;

      case "mintpy-analysis":
        return (
          <div style={{ height: "100%", overflowY: "auto" }}>
            {isGuest && showWarningBanner && (
              <WarningBanner
                message="Modo invitado: el paso de corrección troposférica con ERA5 no estará disponible. Para acceso completo, inicia sesión con tus credenciales."
                onDismiss={() => setShowWarningBanner(false)}
              />
            )}
            <MintPyAnalysis />
          </div>
        );

      case "eq-insar":
        // EQ-INSAR is always accessible (no external credentials needed)
        return (
          <div style={{ height: "100%", overflowY: "auto" }}>
            <EqInsarAnalysis />
          </div>
        );

      default:
        return <div>Selecciona una opción del menú.</div>;
    }
  };

  if (mode === "unauthenticated") {
    return <LoginPage />;
  }

  return (
    <div
      style={{
        height: "100vh",
        display: "grid",
        gridTemplateColumns: `${SIDEBAR_WIDTH}px minmax(0, 1fr)`,
        gridTemplateRows: "auto 1fr",
        overflow: "hidden",
        backgroundColor: APP_BG,
      }}
    >
      <div style={{ gridColumn: "1 / 2", gridRow: "1 / span 2" }}>
        <Navbar activeSection={activeSection} onChangeSection={handleChangeSection} />
      </div>

      <div style={{ gridColumn: "2 / 3", gridRow: "1 / 2" }}>
        <BarraSuperior isHidden={isHeaderHidden} />
      </div>

      <div
        ref={contentRef}
        style={{
          gridColumn: "2 / 3",
          gridRow: "2 / 3",
          overflowY: "auto",
          minHeight: 0,
          padding: activeSection === "inicio" ? "0" : "2rem",
          color: "var(--color-text-main)",
        }}
      >
        {activeSection !== "inicio" && activeSection !== "login-redirect" && (
          <h2 style={{ marginTop: 0, marginBottom: "1rem", opacity: 0.9 }}>
            {activeSection.charAt(0).toUpperCase() + activeSection.slice(1)}
          </h2>
        )}
        {renderContent()}
      </div>
    </div>
  );
};

const App: React.FC = () => (
  <AuthProvider>
    <AppInner />
    {/* Panel flotante de estado de tareas — visible globalmente en toda la app */}
    <JobStatusPanel />
  </AuthProvider>
);

export default App;
