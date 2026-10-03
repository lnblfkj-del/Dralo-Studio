import { useState } from "react";
import { ArrowUp, ChevronRight, Folder, FolderOpen, HardDrive, Plus } from "lucide-react";

import { Button, Dialog } from "@/components/ui";

export interface StorageDirectoryListing {
  path: string;
  parent: string | null;
  directories: string[];
  truncated: boolean;
}

export function StorageDirectoryDialog({
  path,
  pathInput,
  listing,
  loading,
  error,
  onPathInput,
  onBrowse,
  onCreateFolder,
  onChoose,
  onClose,
}: {
  path: string;
  pathInput: string;
  listing?: StorageDirectoryListing;
  loading: boolean;
  error?: string;
  onPathInput: (value: string) => void;
  onBrowse: (value: string) => void;
  onCreateFolder: (name: string) => Promise<void>;
  onChoose: (value: string) => void;
  onClose: () => void;
}) {
  const selectedPath = listing?.path || path;
  const [newFolderName, setNewFolderName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string>();
  const createFolder = async () => {
    const name = newFolderName.trim();
    if (!name) {
      setCreateError("请输入文件夹名称");
      return;
    }
    setCreating(true);
    setCreateError(undefined);
    try {
      await onCreateFolder(name);
      setNewFolderName("");
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : "新文件夹创建失败");
    } finally {
      setCreating(false);
    }
  };
  return <Dialog
    open
    className="storage-directory-dialog"
    title="选择存储文件夹"
    description="浏览运行服务的电脑，选择用于保存素材的空文件夹。"
    size="large"
    onClose={onClose}
    footer={<>
      <div className="storage-directory-selection"><small>当前文件夹</small><code>{selectedPath || "请选择磁盘或文件夹"}</code><p>确认只填入路径，不立即迁移文件。</p></div>
      <Button onClick={onClose}>取消</Button>
      <Button variant="primary" disabled={loading || !!error || !listing?.path} onClick={() => listing?.path && onChoose(listing.path)}>选择此文件夹</Button>
    </>}
  >
    <form className="storage-directory-toolbar" onSubmit={event => { event.preventDefault(); onBrowse(pathInput.trim()); }}>
      <Button icon={<HardDrive size={16} />} onClick={() => onBrowse("")}>磁盘</Button>
      <Button aria-label="上一级" disabled={!path || loading} icon={<ArrowUp size={16} />} onClick={() => onBrowse(listing?.parent ?? "")} />
      <input aria-label="目录路径" placeholder="输入绝对路径，按回车打开" value={pathInput} onChange={event => onPathInput(event.target.value)} />
      <Button type="submit">打开</Button>
    </form>
    <div className="storage-directory-create">
      <input aria-label="新文件夹名称" placeholder="新文件夹名称" value={newFolderName} onChange={event => setNewFolderName(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); void createFolder(); } }} />
      <Button type="button" icon={<Plus size={16} />} disabled={loading || creating || !listing?.path} loading={creating} onClick={() => void createFolder()}>新建文件夹</Button>
      {createError && <span role="alert">{createError}</span>}
    </div>
    <div className="storage-directory-list" aria-busy={loading}>
      {loading && <p role="status">正在读取文件夹…</p>}
      {error && <p role="alert">{error}。可返回磁盘列表重新选择。</p>}
      {!loading && !error && listing && <>
        <ul>{listing.directories.map(item => <li key={item}><button type="button" onClick={() => onBrowse(item)}><Folder size={19}/><span>{item.split(/[\\/]/).filter(Boolean).at(-1) || item}</span><ChevronRight size={15}/></button></li>)}</ul>
        {!listing.directories.length && <div className="storage-directory-empty"><FolderOpen size={32}/><p>此目录没有子文件夹</p><small>是否为空及能否迁移，将在保存设置时校验。</small></div>}
        {listing.truncated && <p>仅显示前 500 个目录，其他目录可在上方输入路径。</p>}
      </>}
    </div>
  </Dialog>;
}
