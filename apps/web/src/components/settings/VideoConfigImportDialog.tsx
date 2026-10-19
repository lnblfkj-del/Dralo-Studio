import {useRef, useState} from 'react';
import {Clipboard, Copy, Download, FileUp, Link2} from 'lucide-react';
import {Button, Dialog} from '@/components/ui';
import {toErrorMessage} from '@/api/client';
import * as api from '@/api/videoTools';
import {VideoPackageSummary} from './VideoPresetPicker';
import {downloadVideoJSON, parseVideoPackageText, videoPackageChanges, VIDEO_PACKAGE_MAX_BYTES} from './videoConfigImport';
import '@/styles/video-config-import.css';

export function VideoConfigImportDialog({providerId, identifier, current, onUse, onClose}: {
  providerId: number; identifier: string; current?: () => api.VideoPackage;
  onUse: (value: api.VideoPackage) => void; onClose: () => void;
}) {
  const [mode, setMode] = useState<'file' | 'paste'>('file');
  const [text, setText] = useState('');
  const [link, setLink] = useState('');
  const [preview, setPreview] = useState<api.VideoPackage | null>(null);
  const [source, setSource] = useState('');
  const [trust, setTrust] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [helpError, setHelpError] = useState('');
  const [prompt, setPrompt] = useState('');
  const [showPrompt, setShowPrompt] = useState(false);
  const [copied, setCopied] = useState(false);
  const [template, setTemplate] = useState<'json' | 'service'>('json');
  const fileInput = useRef<HTMLInputElement>(null);
  const inFlight = useRef(false), revision = useRef(0), installed = useRef(false);
  let before: api.VideoPackage | null = null;
  try {before = current?.() ?? null;} catch { /* Incomplete settings may be repaired by an import. */ }
  const invalidate = () => {revision.current++; setPreview(null); setTrust(false); installed.current = false; setError('');};
  const act = async (task: () => Promise<void>, help = false) => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); help ? setHelpError('') : setError('');
    try {await task();} catch (reason) {(help ? setHelpError : setError)(toErrorMessage(reason));}
    finally {inFlight.current = false; setBusy(false);}
  };
  const read = (load: () => Promise<api.VideoPackage>, label: string) => {
    invalidate(); const expected = revision.current;
    return act(async () => {
      const value = await load();
      if (revision.current !== expected) return;
      setPreview(value); setSource(label);
    });
  };
  const loadPrompt = async () => {
    if (prompt) return prompt;
    const value = await api.getVideoImportAssistance(); setPrompt(value.prompt); return value.prompt;
  };
  const service = !!preview?.video_config.integration?.service_adapter;
  const changes = preview ? videoPackageChanges(before, preview) : [];
  return <Dialog open title="导入视频配置" className="video-config-import-dialog" size="medium" busy={busy}
    dirty={!!text.trim() || !!link.trim()} onClose={onClose} footer={requestClose => <>
      <Button disabled={busy} onClick={requestClose}>取消</Button>
      <Button variant="primary" disabled={busy || !preview || (service && !trust)} onClick={() => void act(async () => {
        if (!preview) return;
        if (service && !installed.current) {await api.installVideoAdapter(providerId, preview); installed.current = true;}
        onUse(structuredClone(preview)); onClose();
      })}>使用这份配置</Button>
    </>}>
    <div className="video-config-import" data-local-tools>
      <p className="video-import-format">本系统 .json 配置文件，最大 100KB。不接受接口文档、.ts、ZIP 或安装程序。</p>
      <div className="video-import-tabs" role="tablist" aria-label="配置导入方式">
        <button type="button" role="tab" aria-selected={mode === 'file'} disabled={busy} onClick={() => setMode('file')}><FileUp size={15} />选择文件</button>
        <button type="button" role="tab" aria-selected={mode === 'paste'} disabled={busy} onClick={() => setMode('paste')}><Clipboard size={15} />粘贴配置</button>
      </div>
      {mode === 'file' ? <div className="video-import-file"><FileUp size={24} /><span>{source && preview ? source : '选择渠道提供的视频配置'}</span><Button disabled={busy} icon={<FileUp size={15} />} onClick={() => fileInput.current?.click()}>选择 JSON 文件</Button></div> : <label>配置内容<textarea aria-label="粘贴视频配置" value={text} spellCheck={false} disabled={busy} onChange={e => {invalidate(); setText(e.target.value);}} /></label>}
      <input ref={fileInput} type="file" hidden accept="application/json,.json" aria-label="视频配置文件" disabled={busy} onChange={e => {
        const file = e.target.files?.[0]; e.target.value = '';
        if (!file || inFlight.current) return;
        void read(async () => {
          if (!file.name.toLowerCase().endsWith('.json')) throw new Error('请选择 .json 配置文件；.ts 代码、ZIP 和接口文档不能直接导入。');
          if (file.size > VIDEO_PACKAGE_MAX_BYTES) throw new Error('配置不能超过 100KB');
          return api.importVideoPackage(parseVideoPackageText(await file.text()));
        }, file.name);
      }} />
      {mode === 'paste' && <Button disabled={busy || !text.trim()} onClick={() => void read(async () => api.importVideoPackage(parseVideoPackageText(text)), '粘贴的配置')}>读取配置</Button>}
      <details className="video-import-more"><summary>更多导入方式</summary><label>JSON 文件链接<input type="url" aria-label="视频配置链接" value={link} disabled={busy} placeholder="https://…/video-config.json" onChange={e => {invalidate(); setLink(e.target.value);}} /></label><Button icon={<Link2 size={15} />} disabled={busy || !link.trim()} onClick={() => void read(() => api.importVideoLink(link.trim()), '链接配置')}>读取链接</Button></details>
      {error && <p role="alert" className="video-check-error">{error}</p>}
      {preview && <section className="video-import-result" aria-label="配置预览">
        <h3>{preview.name}</h3><dl className="video-package-summary"><dt>来源</dt><dd>{source}</dd><dt>格式版本</dt><dd>{preview.schema_version}</dd><dt>供应商版本</dt><dd>文件未声明</dd><dt>适用模型</dt><dd>{identifier || '待填写模型标识'} · 文件未声明适用模型清单，须按渠道资料核对</dd></dl>
        <VideoPackageSummary value={preview} />
        <p>{before ? `将替换：${changes.join('、') || '配置内容相同'}。` : '将带入接入方式、接口映射、支持范围、默认设置、脚本偏好和附加参数。'}使用后仍须{current ? '保存模型' : '添加模型'}；不修改渠道密钥和连接地址。</p>
        {service && <div className="video-import-service"><strong>这份配置需要独立适配服务</strong><p>服务必须事先部署；文件不会部署或执行程序。服务将接收提示词和素材，版本必须保留旧任务接口。点击“使用这份配置”将安装服务配置记录，再加入模型编辑稿。</p><label><input type="checkbox" checked={trust} disabled={busy} onChange={e => setTrust(e.target.checked)} />确认服务由我方部署或信任，并同意安装这份服务配置</label></div>}
        <details><summary>查看完整配置</summary><pre>{JSON.stringify(preview, null, 2)}</pre></details>
      </section>}
      <section className="video-import-help" aria-label="AI接入帮助"><h3>没有配置文件？让AI帮你准备</h3>
        <p>把接入助手提示词和已脱敏的接口资料交给你使用的 AI，拿到 JSON 文件后回来导入。复制提示词不调用 AI、不上传资料，也不产生费用。</p>
        <Button icon={<Copy size={15} />} disabled={busy} onClick={() => void act(async () => {
          setCopied(false);
          const value = await loadPrompt();
          try {await navigator.clipboard.writeText(value); setCopied(true);} catch {setShowPrompt(true); throw new Error('未能复制到剪贴板，完整提示词已展开。');}
        }, true)}>复制接入助手提示词</Button>{copied && <small role="status">已复制</small>}
        <details open={showPrompt} onToggle={e => setShowPrompt(e.currentTarget.open)}><summary onClick={() => {if (!showPrompt) void act(async () => {await loadPrompt();}, true);}}>查看完整提示词</summary>{prompt ? <pre tabIndex={0}>{prompt}</pre> : <small>正在读取提示词…</small>}</details>
        <details><summary>下载配置示例</summary><label>示例类型<select aria-label="配置示例类型" value={template} disabled={busy} onChange={e => setTemplate(e.target.value as 'json' | 'service')}><option value="json">普通接口配置</option><option value="service">独立适配服务配置</option></select></label><p>示例值仅演示结构，不代表当前模型支持；请先按真实接口资料修改。</p><Button icon={<Download size={15} />} disabled={busy} onClick={() => void act(async () => downloadVideoJSON(await api.downloadVideoTemplate(template), `video-${template}-example.json`), true)}>下载 JSON 示例</Button></details>
        {helpError && <p role="alert" className="video-check-error">{helpError}</p>}
      </section>
    </div>
  </Dialog>;
}
