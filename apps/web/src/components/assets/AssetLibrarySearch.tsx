import { Search } from "lucide-react";
import "@/styles/asset-library-search.css";

export function AssetLibrarySearch({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) {
  return <label className="asset-library-search"><Search size={15} aria-hidden="true" /><input type="search" aria-label={label} placeholder="搜索素材" value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}
