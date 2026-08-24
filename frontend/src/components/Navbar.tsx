import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Home,
  Map,
  ChevronDown,
  Layers,
  Activity,
  Zap,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";

const cx = (...classes: (string | undefined | null | false)[]) => classes.filter(Boolean).join(" ");

type NavbarProps = {
  activeSection: string;
  onChangeSection: (section: string) => void;
};

type SubmenuItem = {
  id: string;
  label: string;
};

type ItemProps = {
  id: string;
  label: string;
  icon: React.ReactNode;
  hasSubmenu: boolean;
  isOpen?: boolean;
  toggleSubmenu?: () => void;
  submenuItems?: SubmenuItem[];
  onChangeSection?: (section: string) => void;
  activeSection: string;
};

export default function Navbar({ activeSection, onChangeSection }: NavbarProps) {
  const [isAlaskaOpen, setIsAlaskaOpen] = useState(false);
  const { isAuthenticated, username, logout } = useAuth();

  const handleKey = (section: string, toggleSubmenu?: () => void) => (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (toggleSubmenu) toggleSubmenu();
      else onChangeSection(section);
    }
  };

  const Item = ({
    id,
    label,
    icon,
    hasSubmenu,
    isOpen,
    toggleSubmenu,
    submenuItems,
    activeSection,
  }: ItemProps) => {
    const isActive = activeSection === id || submenuItems?.some((s) => s.id === activeSection);

    return (
      <li className={cx("sidebar__item", isActive ? "is-active" : undefined)}>
        <div
          role="button"
          tabIndex={0}
          className="sidebar__item-header"
          onClick={() => {
            if (hasSubmenu && toggleSubmenu) toggleSubmenu();
            else onChangeSection?.(id);
          }}
          onKeyDown={handleKey(id, toggleSubmenu)}
          aria-expanded={isOpen}
        >
          <div className="item-content">
            {icon}
            <span>{label}</span>
          </div>
          {hasSubmenu && (
            <ChevronDown
              className="chevron"
              style={{ transform: isOpen ? "rotate(180deg)" : "rotate(0deg)" }}
            />
          )}
        </div>

        <AnimatePresence initial={false}>
          {hasSubmenu && isOpen && submenuItems && (
            <motion.ul
              className="submenu"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2, ease: "easeInOut" }}
            >
              {submenuItems.map((sub: SubmenuItem) => (
                <li
                  key={sub.id}
                  onClick={() => onChangeSection(sub.id)}
                  className={cx(
                    "submenu__item",
                    activeSection === sub.id ? "is-active" : undefined
                  )}
                >
                  {sub.label}
                </li>
              ))}
            </motion.ul>
          )}
        </AnimatePresence>
      </li>
    );
  };

  return (
    <aside className="sidebar" aria-label="Barra lateral de navegación">
      <div className="sidebar__brand">
        <div className="icon-wrapper">
          <Layers size={22} />
        </div>
        <span>GeoDesk Beta</span>
      </div>

      <h3 className="sidebar__title">Navegación Principal</h3>

      <nav style={{ flex: 1 }}>
        <ul className="sidebar__list">
          <Item
            id="inicio"
            label="Inicio"
            icon={<Home size={18} />}
            hasSubmenu={false}
            onChangeSection={onChangeSection}
            activeSection={activeSection}
          />

          <Item
            id="alaska"
            label="Alaska SAR"
            icon={<Map size={18} />}
            hasSubmenu={true}
            isOpen={isAlaskaOpen}
            toggleSubmenu={() => setIsAlaskaOpen(!isAlaskaOpen)}
            submenuItems={[
              { id: "solicitud-imagenes", label: "Solicitud manual" },
              { id: "solicitud-automatico", label: "Solicitud automático" },
              { id: "descarga-imagenes", label: "Descarga de imágenes" },
            ]}
            activeSection={activeSection}
          />

          {/* Spacer */}
          <div style={{ margin: "16px 0" }} />
          <h3 className="sidebar__title">Herramientas</h3>

          <Item
            id="mintpy-analysis"
            label="Análisis InSAR (MintPy)"
            icon={<Activity size={18} />}
            hasSubmenu={false}
            onChangeSection={onChangeSection}
            activeSection={activeSection}
          />

          <Item
            id="eq-insar"
            label="EQ-INSAR Sintético"
            icon={<Zap size={18} />}
            hasSubmenu={false}
            onChangeSection={onChangeSection}
            activeSection={activeSection}
          />
        </ul>
      </nav>

      {/* ── Session status panel ─────────────────────────────────────────── */}
      <div className="sidebar__session">
        {isAuthenticated ? (
          <>
            <div className="sidebar__session-row">
              <ShieldCheck size={15} className="sidebar__session-icon sidebar__session-icon--ok" />
              <div className="sidebar__session-info">
                <span className="sidebar__session-label">Sesión activa</span>
                <span className="sidebar__session-user" title={username ?? ""}>
                  {username}
                </span>
              </div>
            </div>
            <button
              id="navbar-logout-btn"
              className="sidebar__session-btn sidebar__session-btn--logout"
              onClick={logout}
              aria-label="Cerrar sesión"
            >
              <LogOut size={14} />
              Cerrar sesión
            </button>
          </>
        ) : null}
      </div>
    </aside>
  );
}
