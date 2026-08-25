import { useState, useRef, useEffect, useCallback } from "react";
import Navbar from "./components/Navbar";
import BarraSuperior from "./components/BarraSuperior";
import JobStatusPanel from "./components/JobStatusPanel";
import CredentialsSettingsModal from "./components/CredentialsSettingsModal";

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

const SIDEBAR_WIDTH = 300;
const APP_BG = "var(--color-bg-main)";



const AppInner: React.FC = () => {
  const { isAuthenticated, externalCreds } = useAuth();
  const [activeSection, setActiveSection] = useState<string>("inicio");
  const [, setActivePage] = useState<string>("");
  const [isHeaderHidden, setIsHeaderHidden] = useState<boolean>(false);
  const [showCredsModal, setShowCredsModal] = useState(false);

  const contentRef = useRef<HTMLDivElement | null>(null);
  const lastScrollTopRef = useRef<number>(0);

  // Auto-open credentials modal if missing after login
  useEffect(() => {
    if (isAuthenticated && !externalCreds) {
      setShowCredsModal(true);
    }
  }, [isAuthenticated, externalCreds]);

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

  // ── Navigation ──────────────────────────────────────────────────────────────
  const handleChangeSection = (section: string) => {
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
    switch (activeSection) {
      case "inicio":
        return <Inicio onNavigate={handleChangeSection} />;

      case "alaska":
        return <div>Sección Alaska (elige una opción del submenú)</div>;

      case "solicitud-imagenes":
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
        return <div><DownloadFiles /></div>;

      case "solicitud-automatico":
        return <div><SolicitarImagenesAutomatico /></div>;

      case "mintpy-analysis":
        return (
          <div style={{ height: "100%", overflowY: "auto" }}>
            <MintPyAnalysis />
          </div>
        );

      case "eq-insar":
        return (
          <div style={{ height: "100%", overflowY: "auto" }}>
            <EqInsarAnalysis />
          </div>
        );

      default:
        return <div>Selecciona una opción del menú.</div>;
    }
  };

  if (!isAuthenticated) {
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
        <BarraSuperior isHidden={isHeaderHidden} onOpenSettings={() => setShowCredsModal(true)} />
      </div>

      <CredentialsSettingsModal isOpen={showCredsModal} onClose={() => setShowCredsModal(false)} />

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
