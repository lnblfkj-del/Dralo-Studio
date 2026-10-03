import type { CSSProperties } from "react";
import {
  ArrowLeft, ArrowRight, CalendarDays, Check, ChevronDown, ChevronRight,
  Clapperboard, Clock3, FileText, FolderOpen, Image, ListChecks, LockKeyhole, LogOut,
  PanelsTopLeft, PanelLeftClose, PanelLeftOpen, PenLine, Plus, RefreshCw,
  Search, Settings2, Trash2, Upload, Grid2X2, ServerCog, Bot, Sparkles,
  Palette, Activity, HardDrive, UsersRound, type LucideIcon, X,
} from "lucide-react";

const icons: Record<string, LucideIcon> = {
  spark: Clapperboard, film: Clapperboard, grid: Grid2X2,
  providers: ServerCog, models: ServerCog, agent: Bot, skills: Sparkles,
  palette: Palette, execution: Activity, storage: HardDrive, users: UsersRound,
  settings: Settings2, folder: FolderOpen, file: FileText,
  upload: Upload, plus: Plus, arrow: ArrowRight, back: ArrowLeft,
  chevron: ChevronRight, search: Search, pen: PenLine,
  lock: LockKeyhole, image: Image, clock: Clock3, close: X,
  check: Check, logout: LogOut, trash: Trash2, refresh: RefreshCw,
  list: ListChecks, calendar: CalendarDays, "chevron-down": ChevronDown,
  "panel-collapse": PanelLeftClose, "panel-expand": PanelLeftOpen,
};

export function Icon({ name, size = 20, style }: { name: string; size?: number; style?: CSSProperties }) {
  if (name === "grid") {
    return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.65} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" style={{ flexShrink: 0, ...style }}>
      <path d="M15.5 3.5H6a2.5 2.5 0 0 0-2.5 2.5v12A2.5 2.5 0 0 0 6 20.5h12a2.5 2.5 0 0 0 2.5-2.5V8.5M12 3.5v17M3.5 12h17M19 3.5h1.5V5" />
    </svg>;
  }
  const Component = icons[name] ?? PanelsTopLeft;
  return <Component size={size} strokeWidth={1.75} aria-hidden="true" focusable="false" style={{ flexShrink: 0, ...style }} />;
}
