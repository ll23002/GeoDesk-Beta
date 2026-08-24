import React, { useState } from "react";
import { Settings, Save, X, Eye, EyeOff, LogOut } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import type { ExternalCredentials } from "../context/AuthContext";

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

const CredentialsSettingsModal: React.FC<Props> = ({ isOpen, onClose }) => {
  const { externalCreds, saveExternalCreds, clearExternalCreds, logout, username } = useAuth();

  const [hyp3Username, setHyp3Username] = useState(externalCreds?.hyp3Username ?? "");
  const [hyp3Password, setHyp3Password] = useState(externalCreds?.hyp3Password ?? "");
  const [era5Key, setEra5Key]           = useState(externalCreds?.era5Key ?? "");
  const [showPassword, setShowPassword] = useState(false);
  const [saved, setSaved]               = useState(false);

  if (!isOpen) return null;

  const handleSave = () => {
    const creds: ExternalCredentials = {
      hyp3Username: hyp3Username.trim(),
      hyp3Password: hyp3Password.trim(),
      era5Key: era5Key.trim(),
    };
    saveExternalCreds(creds);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleClear = () => {
    setHyp3Username("");
    setHyp3Password("");
    setEra5Key("");
    clearExternalCreds();
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,0.6)",
        backdropFilter: "blur(4px)",
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        style={{
          background: "var(--color-bg-card, #0f1729)",
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: "var(--radius-lg, 16px)",
          padding: "2rem",
          width: "100%",
          maxWidth: "480px",
          boxShadow: "0 25px 60px rgba(0,0,0,0.5)",
          color: "var(--color-text-main, #e2e8f0)",
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <Settings size={20} color="var(--color-primary, #00e5ff)" />
            <div>
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Configuración de Credenciales</h2>
              <p style={{ margin: 0, fontSize: "0.8rem", opacity: 0.6 }}>Sesión: {username}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", color: "inherit", cursor: "pointer", opacity: 0.6 }}
          >
            <X size={20} />
          </button>
        </div>

        {/* HyP3 Section */}
        <div style={{ marginBottom: "1.25rem" }}>
          <p style={{ margin: "0 0 0.75rem", fontSize: "0.8rem", opacity: 0.5, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            HyP3 — Alaska Satellite Facility (para análisis ad-hoc)
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
            <input
              type="text"
              placeholder="Usuario / Email de HyP3"
              value={hyp3Username}
              onChange={(e) => setHyp3Username(e.target.value)}
              style={inputStyle}
            />
            <div style={{ position: "relative" }}>
              <input
                type={showPassword ? "text" : "password"}
                placeholder="Contraseña de HyP3"
                value={hyp3Password}
                onChange={(e) => setHyp3Password(e.target.value)}
                style={{ ...inputStyle, paddingRight: "2.5rem" }}
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                style={{
                  position: "absolute", right: "0.75rem", top: "50%",
                  transform: "translateY(-50%)", background: "none",
                  border: "none", color: "inherit", cursor: "pointer", opacity: 0.5,
                }}
              >
                {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>
        </div>

        {/* ERA5 Section */}
        <div style={{ marginBottom: "1.5rem" }}>
          <p style={{ margin: "0 0 0.75rem", fontSize: "0.8rem", opacity: 0.5, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            ERA5 — Copernicus Climate (opcional, para corrección troposférica)
          </p>
          <input
            type="text"
            placeholder="UID:API_KEY de Copernicus"
            value={era5Key}
            onChange={(e) => setEra5Key(e.target.value)}
            style={{ ...inputStyle, fontFamily: "monospace" }}
          />
        </div>

        {/* Buttons */}
        <div style={{ display: "flex", gap: "0.75rem", justifyContent: "flex-end" }}>
          <button onClick={handleClear} style={secondaryBtnStyle}>
            Limpiar
          </button>
          <button onClick={handleSave} style={primaryBtnStyle}>
            {saved ? "✓ Guardado" : (
              <>
                <Save size={15} />
                Guardar
              </>
            )}
          </button>
        </div>

        <hr style={{ border: "none", borderTop: "1px solid rgba(255,255,255,0.08)", margin: "1.25rem 0" }} />

        {/* Logout */}
        <button
          onClick={logout}
          style={{
            display: "flex", alignItems: "center", gap: "0.5rem",
            background: "none", border: "1px solid rgba(239,68,68,0.3)",
            color: "#f87171", borderRadius: "var(--radius-md, 8px)",
            padding: "0.5rem 1rem", cursor: "pointer", fontSize: "0.85rem", width: "100%",
            justifyContent: "center",
          }}
        >
          <LogOut size={15} />
          Cerrar Sesión
        </button>
      </div>
    </div>
  );
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "0.6rem 0.85rem",
  background: "rgba(255,255,255,0.05)",
  border: "1px solid rgba(255,255,255,0.1)",
  borderRadius: "var(--radius-md, 8px)",
  color: "var(--color-text-main, #e2e8f0)",
  fontSize: "0.9rem",
  outline: "none",
  boxSizing: "border-box",
};

const primaryBtnStyle: React.CSSProperties = {
  display: "flex", alignItems: "center", gap: "0.4rem",
  padding: "0.55rem 1.25rem",
  background: "linear-gradient(135deg, var(--color-primary, #00e5ff) 0%, #00b4d8 100%)",
  color: "#020617", border: "none",
  borderRadius: "var(--radius-md, 8px)",
  fontWeight: 600, cursor: "pointer", fontSize: "0.9rem",
};

const secondaryBtnStyle: React.CSSProperties = {
  padding: "0.55rem 1rem",
  background: "rgba(255,255,255,0.05)",
  border: "1px solid rgba(255,255,255,0.1)",
  color: "var(--color-text-main, #e2e8f0)",
  borderRadius: "var(--radius-md, 8px)",
  cursor: "pointer", fontSize: "0.9rem",
};

export default CredentialsSettingsModal;
