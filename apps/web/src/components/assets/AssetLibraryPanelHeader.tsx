import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

export function AssetLibraryPanelHeader({ collapsed, onToggle, title }: { collapsed: boolean; onToggle: () => void; title: string }) {
  return <header className="asset-library-panel-header"><strong hidden={collapsed}>{title}</strong><button type="button" title={collapsed ? "展开素材栏" : "收起素材栏"} aria-label={collapsed ? "展开素材栏" : "收起素材栏"} aria-expanded={!collapsed} onClick={onToggle}>{collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button></header>;
}
