import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Boxes,
  Box,
  CircleHelp,
  Clapperboard,
  FileText,
  FolderOpen,
  Frame,
  Image,
  Map,
  Music,
  Mic2,
  Package,
  Plus,
  Search,
  Shirt,
  Type,
  UserRound,
  Video,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import * as assetApi from "@/api/assets";
import * as mediaApi from "@/api/media";
import { attachCanvasMedia } from "@/api/canvas";
import { toErrorMessage } from "@/api/client";
import { useCanvasStore, type CanvasNodePayload } from "@/stores/canvasStore";
import type { CanvasNodeType, MediaFileItem } from "@/types/api";
import { CANVAS_ASSET_MIME, canvasAssetPlacement } from "./canvasAssetPlacement";

type Addition = { kind: CanvasNodeType; label: string; description: string; icon: typeof Type };

const ADDITION_GROUPS: Array<{ label: string; items: Addition[] }> = [
  {
    label: "故事结构",
    items: [
      { kind: "character", label: "角色", description: "人物设定与形象", icon: UserRound },
      { kind: "scene", label: "场景", description: "空间、时间与氛围", icon: Map },
      { kind: "costume", label: "服装 / 造型", description: "服装、发型、妆容与状态", icon: Shirt },
      { kind: "prop", label: "道具", description: "外观、持有人与剧情功能", icon: Package },
      { kind: "voice", label: "声音资产", description: "配音、配乐、环境声与音效", icon: Mic2 },
      { kind: "director", label: "3D 导演台", description: "人物摆位、机位构图与截图", icon: Box },
      { kind: "prompt", label: "镜头意图", description: "调度、动作与画面", icon: Clapperboard },
      { kind: "frame", label: "工作分组", description: "把同一段制作内容收拢", icon: Frame },
    ],
  },
  {
    label: "内容与素材",
    items: [
      { kind: "text", label: "创作文本", description: "台词、备注或改稿", icon: Type },
      { kind: "image", label: "图片节点", description: "参考图或生成图", icon: Image },
      { kind: "video", label: "视频节点", description: "视频素材与版本", icon: Video },
      { kind: "audio", label: "音频", description: "配乐、旁白与音效", icon: Music },
      { kind: "file", label: "文件", description: "文本与参考资料", icon: FileText },
    ],
  },
];

const ADDITIONS = ADDITION_GROUPS.flatMap((group) => group.items);

export function CanvasToolDock({
  projectId,
  onAdd,
}: {
  projectId: number;
  onAdd: (kind: CanvasNodeType, data?: Partial<CanvasNodePayload>) => void;
}) {
  const [attachmentError, setAttachmentError] = useState("");
  const uploadInput = useRef<HTMLInputElement>(null);
  const uploadPosition = useRef<{ x: number; y: number } | null>(null);
  const [panel, setPanel] = useState<"add" | "library" | null>(null);
  const [libraryTab, setLibraryTab] = useState<"canvas" | "assets" | "media">("assets");
  const [search, setSearch] = useState("");
  const [mediaPage, setMediaPage] = useState(1);
  const [targetNodeId, setTargetNodeId] = useState<string | null>(null);
  const assets = useQuery({
    queryKey: ["assets", projectId],
    queryFn: async () => {
      const [projectAssets, globalAssets] = await Promise.all([
        assetApi.listAssets(projectId),
        assetApi.listGlobalAssets(),
      ]);
      return [...new globalThis.Map([...projectAssets, ...globalAssets].map((asset) => [asset.id, asset])).values()];
    },
    enabled: panel === "library",
  });
  const media = useQuery({
    queryKey: ["media-library", "canvas-dock", projectId, mediaPage, search],
    queryFn: () => mediaApi.listMedia({ project_id: projectId, page_size: 40, page: mediaPage, keyword: search || undefined }),
    enabled: panel === "library",
  });
  const upload = useMutation({ mutationFn: (file: File) => mediaApi.uploadMedia(file, projectId) });

  useEffect(() => {
    const openAttachments = (event: Event) => {
      const detail = (event as CustomEvent<{ nodeId?: string; mode?: string; position?: { x: number; y: number } }>).detail;
      uploadPosition.current = detail?.position ?? null;
      setTargetNodeId(detail?.nodeId ?? null);
      if (detail?.mode === "upload") {
        uploadInput.current?.click();
        return;
      }
      setLibraryTab("media");
      setPanel("library");
    };
    window.addEventListener("canvas-node-attachment", openAttachments);
    return () => window.removeEventListener("canvas-node-attachment", openAttachments);
  }, []);

  const chooseMedia = async (item: MediaFileItem) => {
    const kind = item.kind === "video" ? "video" : item.kind === "image" ? "image" : item.kind === "audio" ? "audio" : "file";
    const data = { mediaId: item.id, title: item.original_name || `媒体 #${item.id}` };
    if (targetNodeId) {
      try {
        const state = useCanvasStore.getState();
        if (state.dirty) throw new Error("请等待画布保存后再绑定素材；已上传文件保留在素材库中");
        const snapshot = await attachCanvasMedia(projectId, targetNodeId, item.id, state.revision);
        state.mergeRuntime(snapshot);
        state.markSaved(snapshot.revision, state.changeVersion);
        setAttachmentError("");
      } catch (error) { setAttachmentError(toErrorMessage(error)); return; }
    }
    else if (uploadPosition.current) useCanvasStore.getState().addNode(kind, uploadPosition.current, data);
    else onAdd(kind, data);
    uploadPosition.current = null;
    setPanel(null);
    setTargetNodeId(null);
  };
  const query = search.trim().toLocaleLowerCase();

  return (
    <>
      {(attachmentError || upload.error) && <div className="canvas-attachment-error" role="alert">{attachmentError || toErrorMessage(upload.error)}<button onClick={() => { setAttachmentError(""); upload.reset(); }}>关闭</button></div>}
      <input
        ref={uploadInput}
        hidden
        type="file"
        accept="image/*,video/*,audio/*,.txt,.md,.docx,.pdf"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) upload.mutate(file, { onSuccess: chooseMedia });
          event.target.value = "";
        }}
      />
      <nav className="canvas-tool-dock" aria-label="画布工具">
        <button className="primary" title="添加生产单元" aria-label="打开添加生产单元" aria-pressed={panel === "add"} onClick={() => setPanel(panel === "add" ? null : "add")}>
          <Plus size={18} />
        </button>
        <button title="素材与资产" aria-label="打开素材与资产" aria-pressed={panel === "library"} onClick={() => { setTargetNodeId(null); setPanel(panel === "library" ? null : "library"); }}>
          <FolderOpen size={17} />
        </button>
        <button title="画布使用说明" aria-label="画布使用说明"><CircleHelp size={16} /></button>
      </nav>

      {panel && (
        <aside className="canvas-tool-panel" aria-label={panel === "add" ? "添加生产单元" : "素材与资产"}>
          <header>
            <div>
              <small>{panel === "add" ? "ADD TO WORKSPACE" : "TEAM LIBRARY"}</small>
              <h2>{panel === "add" ? "添加生产单元" : "素材与资产"}</h2>
            </div>
            <button aria-label="关闭画布工具面板" onClick={() => setPanel(null)}><X size={16} /></button>
          </header>

          {panel === "add" ? (
            <div className="canvas-add-groups">
              {ADDITION_GROUPS.map((group) => (
                <section key={group.label}>
                  <small>{group.label}</small>
                  <div className="canvas-add-grid">
                    {group.items.map(({ kind, label, description, icon: Icon }) => (
                      <button key={kind} onClick={() => { onAdd(kind); setPanel(null); }}>
                        <Icon size={17} />
                        <span><strong>{label}</strong><small>{description}</small></span>
                      </button>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          ) : (
            <>
              <nav className="canvas-library-tabs">
                <button className={libraryTab === "assets" ? "active" : ""} onClick={() => setLibraryTab("assets")}>团队资产</button>
                <button className={libraryTab === "media" ? "active" : ""} onClick={() => setLibraryTab("media")}>媒体素材</button>
                <button className={libraryTab === "canvas" ? "active" : ""} onClick={() => setLibraryTab("canvas")}>添加节点</button>
              </nav>
              <label className="canvas-library-search"><Search size={14} /><input aria-label="搜索素材与资产" value={search} onChange={(event) => {setSearch(event.target.value); setMediaPage(1);}} placeholder="搜索人物、场景、图片或视频" /></label>
              <div className="canvas-library-list">
                {libraryTab === "canvas" && ADDITIONS.filter((item) => !query || `${item.label} ${item.description}`.toLocaleLowerCase().includes(query)).map(({ kind, label, description, icon: Icon }) => (
                  <button key={kind} onClick={() => { onAdd(kind); setPanel(null); }}><Icon size={16} /><span><strong>{label}</strong><small>{description}</small></span></button>
                ))}
                {libraryTab === "assets" && assets.data?.filter((asset) => !query || `${asset.name} ${asset.slug}`.toLocaleLowerCase().includes(query)).map((asset) => (
                  <button key={asset.id} draggable onDragStart={(event) => {
                    event.dataTransfer.effectAllowed = "copy";
                    event.dataTransfer.setData(CANVAS_ASSET_MIME, JSON.stringify(asset));
                  }} onClick={() => { const placement = canvasAssetPlacement(asset); onAdd(placement.kind, placement.data); setPanel(null); }}><Boxes size={16} /><span><strong>{asset.name}</strong><small>@{asset.slug} · {asset.project_id === projectId ? "项目资产" : "共享资产"}</small></span></button>
                ))}
                {libraryTab === "media" && media.data?.items.filter((item) => !query || (item.original_name ?? "").toLocaleLowerCase().includes(query)).map((item) => (
                  <button key={item.id} onClick={() => chooseMedia(item)}>{item.kind === "video" ? <Video size={16} /> : item.kind === "image" ? <Image size={16} /> : <FileText size={16} />}<span><strong>{item.original_name || `媒体 #${item.id}`}</strong><small>{item.kind} · 添加到画布</small></span></button>
                ))}
                {libraryTab === "assets" && !assets.data?.length && <p className="canvas-library-empty">暂无团队资产。可先在资产中心沉淀角色、场景或道具。</p>}
                {libraryTab === "media" && !media.data?.items.length && <p className="canvas-library-empty">暂无媒体素材。可在 Agent 或节点中上传本地文件。</p>}
                {libraryTab === "media" && <div className="canvas-media-actions"><button disabled={mediaPage <= 1 || media.isFetching} onClick={() => setMediaPage(mediaPage - 1)}>上一页</button><small>第 {mediaPage} 页 · 共 {media.data?.total ?? 0} 项</small><button disabled={mediaPage * 40 >= (media.data?.total ?? 0) || media.isFetching} onClick={() => setMediaPage(mediaPage + 1)}>下一页</button></div>}
              </div>
            </>
          )}
        </aside>
      )}
    </>
  );
}
