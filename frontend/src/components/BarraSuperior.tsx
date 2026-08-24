import React from "react";
import { Settings } from "lucide-react";

type BarraSuperiorProps = {
  isHidden?: boolean;
  onOpenSettings?: () => void;
};

const BarraSuperior: React.FC<BarraSuperiorProps> = ({ isHidden, onOpenSettings }) => {
  const logoSrc: string = `${import.meta.env.BASE_URL || "/"}imagenes/logo.png`;
  const logoAlt: string = "Logo Novalis Lab";

  return (
    <header className={`topbar ${isHidden ? "is-hidden" : ""}`}>
      <div className="topbar__brand">
        <img src={logoSrc} alt={logoAlt} />
        <span className="title">GeoDesk</span>
      </div>

      <span className="topbar__subtitle">
        Procesamiento de datos Alaska & ERA-5
      </span>

      <span className="topbar__badge">
        BETA SYSTEM
      </span>

      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "1rem", marginRight: "1rem" }}>
        {onOpenSettings && (
          <button 
            onClick={onOpenSettings}
            style={{
              background: "none",
              border: "none",
              color: "var(--color-text-main, #e2e8f0)",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "0.5rem",
              borderRadius: "50%",
              transition: "background 0.2s"
            }}
            title="Configuración de Credenciales"
          >
            <Settings size={20} />
          </button>
        )}
      </div>
    </header>
  );
};

export default BarraSuperior;