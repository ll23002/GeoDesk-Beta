import React, { useState } from "react";
import { Layers, LogIn, Eye, EyeOff, AlertTriangle, Loader2 } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import "./LoginPage.scss";

const LoginPage: React.FC = () => {
  const { login } = useAuth();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      setError("El usuario y contraseña son obligatorios.");
      return;
    }
    try {
      setLoading(true);
      setError(null);
      await login(username, password);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Credenciales incorrectas o servidor no disponible.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      {/* Background decorative circles */}
      <div className="login-page__bg-blob login-page__bg-blob--1" />
      <div className="login-page__bg-blob login-page__bg-blob--2" />

      <div className="login-card">
        {/* Brand header */}
        <div className="login-card__brand">
          <div className="login-card__icon">
            <Layers size={28} />
          </div>
          <div>
            <h1 className="login-card__title">GeoDesk Beta</h1>
            <p className="login-card__subtitle">Plataforma de Análisis InSAR</p>
          </div>
        </div>

        <div className="login-card__divider" />

        <p className="login-card__description">
          Ingresa tus credenciales de <strong>GeoDesk</strong> para acceder a la plataforma.
        </p>

        {/* Error banner */}
        {error && (
          <div className="login-card__error">
            <AlertTriangle size={16} />
            <span>{error}</span>
          </div>
        )}

        {/* Login form */}
        <form className="login-form" onSubmit={handleLogin} noValidate>
          <fieldset className="login-form__section">
            <legend className="login-form__section-title">
              <span className="login-form__badge login-form__badge--hyp3">GeoDesk</span>
              Acceso a la plataforma
            </legend>

            <div className="login-form__field">
              <label htmlFor="geodesk-username" className="login-form__label">
                Usuario
              </label>
              <input
                id="geodesk-username"
                type="text"
                className="login-form__input"
                placeholder="nombre de usuario"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={loading}
              />
            </div>

            <div className="login-form__field">
              <label htmlFor="geodesk-password" className="login-form__label">
                Contraseña
              </label>
              <div className="login-form__input-wrap">
                <input
                  id="geodesk-password"
                  type={showPassword ? "text" : "password"}
                  className="login-form__input login-form__input--password"
                  placeholder="••••••••••"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={loading}
                />
                <button
                  type="button"
                  className="login-form__eye-btn"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Ocultar contraseña" : "Mostrar contraseña"}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
          </fieldset>

          <button
            id="login-submit-btn"
            type="submit"
            className="login-btn login-btn--primary"
            disabled={loading}
          >
            {loading ? (
              <>
                <Loader2 size={18} className="login-btn__spinner" />
                Verificando credenciales…
              </>
            ) : (
              <>
                <LogIn size={18} />
                Iniciar Sesión
              </>
            )}
          </button>
        </form>

        <p className="login-card__guest-note" style={{ marginTop: "1.25rem" }}>
          ¿No tienes cuenta? Solicita acceso al administrador de GeoDesk.
        </p>
      </div>
    </div>
  );
};

export default LoginPage;
