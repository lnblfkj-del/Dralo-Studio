import {useEffect, useRef, useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {CheckCircle2, Download, FlaskConical, Upload, FolderOpen, Save, Settings2} from 'lucide-react';
import {Button} from '@/components/ui';
import {toErrorMessage} from '@/api/client';
import * as api from '@/api/videoTools';
import type {Provider, ProviderModel, ProviderProtocol, VideoConfigInput} from '@/types/provider';
import '@/styles/video-connection-tools.css';
import {VideoVersionTools} from './VideoVersionTools';
import {SaveVideoPreset, VideoPresetPicker} from './VideoPresetPicker';
import {downloadVideoJSON} from './videoConfigImport';

export function VideoConnectionTools({provider, model, protocol, baseUrl, identifier, integrationJson,
  onProtocol, onIntegration, getConfig, onImport, onImportRequest, canManage, onTrial, trialDisabled, onRestored, versionsDisabled = false}: {
  provider: Provider; model: ProviderModel; protocol: ProviderProtocol | ''; baseUrl: string; identifier: string;
  integrationJson: string; onProtocol: (value: ProviderProtocol | '') => void; onIntegration: (value: string) => void;
  getConfig: () => VideoConfigInput; onImport: (value: api.VideoPackage) => void; onImportRequest: () => void;
  canManage: boolean; onTrial: () => void; trialDisabled: boolean; onRestored?: () => Promise<void>; versionsDisabled?: boolean;
}) {
  const templates = useQuery({queryKey: ['video-connection-templates'], queryFn: api.getVideoTemplates});
  const [result, setResult] = useState<api.VideoCheck | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [pickerOpen, setPickerOpen] = useState(false), [savePresetOpen, setSavePresetOpen] = useState(false), [manualOpen, setManualOpen] = useState(false);
  const draftPackage = (): api.VideoPackage => ({schema_version: 'video-package.v1', name: (model.name || identifier).slice(0, 80), protocol: protocol || provider.protocol, video_config: getConfig()});
  let draftKey = 'invalid';
  try {draftKey = JSON.stringify([protocol, baseUrl, identifier, getConfig()]);} catch { /* An incomplete draft is not checked. */ }
  const currentDraft = useRef(draftKey); currentDraft.current = draftKey;
  useEffect(() => {setResult(null);}, [draftKey]);
  const act = async (task: () => Promise<void>) => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(''); setResult(null);
    try {await task();} catch (reason) {setError(toErrorMessage(reason));} finally {setBusy(false); inFlight.current = false;}
  };
  const select = (value: ProviderProtocol | '') => {
    onProtocol(value); setResult(null);
    if ((value || provider.protocol) === 'custom_video' && integrationJson === 'null' && templates.data) onIntegration(JSON.stringify(templates.data.custom_example, null, 2));
  };
  return <fieldset className="video-connection-tools" data-local-tools><legend>视频接入</legend>
    <div className="video-tools-actions">
      <Button icon={<FolderOpen size={15} />} disabled={!canManage || busy || !identifier.trim()} onClick={() => setPickerOpen(true)}>选择已有配置</Button>
      <Button data-video-import-trigger icon={<Upload size={15} />} disabled={!canManage || busy} onClick={onImportRequest}>导入配置</Button>
      <Button icon={<Settings2 size={15} />} disabled={!canManage || busy} onClick={() => setManualOpen(!manualOpen)} aria-expanded={manualOpen}>手动配置</Button>
    </div>
    {pickerOpen && <VideoPresetPicker providerId={provider.id} identifier={identifier.trim()} current={draftPackage} onApply={onImport} onClose={() => setPickerOpen(false)} />}
    {savePresetOpen && <SaveVideoPreset identifier={identifier} getPackage={draftPackage} onClose={() => setSavePresetOpen(false)} />}
    {manualOpen && <label>接入方式<select aria-label="视频接入方式" disabled={!canManage || busy} value={protocol} onChange={event => select(event.target.value as ProviderProtocol | '')}><option value="">继承渠道设置</option>{templates.data?.items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}{!templates.data?.items.some(item => item.id === protocol) && protocol && <option value={protocol}>{protocol}</option>}</select></label>}
    {manualOpen && (protocol || provider.protocol) === 'custom_video' && <label>接口映射（JSON）<textarea aria-label="自定义视频接法 JSON" spellCheck={false} rows={12} value={integrationJson} disabled={!canManage || busy} onChange={event => {onIntegration(event.target.value); setResult(null);}} /></label>}
    <div className="video-tools-actions">
      <Button icon={<CheckCircle2 size={15} />} disabled={!canManage || busy} onClick={() => void act(async () => {
        const checkedDraft = currentDraft.current;
        const checked = await api.checkVideoConfig(provider.id, model.id, {api_protocol: protocol || null, api_base_url: baseUrl.trim() || null, model_id: identifier.trim(), video_config: getConfig()});
        if (currentDraft.current !== checkedDraft) throw new Error('配置已修改，请重新检查');
        setResult(checked);
      })}>检查配置</Button>
      <Button icon={<FlaskConical size={15} />} title={trialDisabled ? '请先保存当前修改' : '确认参数及费用后，只创建一次视频任务'} disabled={!canManage || busy || trialDisabled} onClick={onTrial}>试生成视频</Button>
    </div>
    <details><summary>更多配置工具</summary><div className="video-tools-actions">
      <Button icon={<Save size={15} />} disabled={!canManage || busy} onClick={() => setSavePresetOpen(true)}>另存为配置</Button>
      <Button icon={<Download size={15} />} disabled={busy} onClick={() => void act(async () => downloadVideoJSON(await api.importVideoPackage(draftPackage()), 'video-connection.json'))}>导出配置</Button>
    </div></details>
    {result && <div role="status" className={result.ready ? 'video-check-ok' : 'video-check-error'}>{result.ready ? `本地检查通过 · ${result.modes.length}组规格 · 未试生成` : result.issues.join('；')}</div>}
    {error && <div role="alert" className="video-check-error">{error}</div>}
    {onRestored && <VideoVersionTools providerId={provider.id} modelId={model.id} disabled={!canManage || busy || versionsDisabled} onRestored={onRestored} />}
  </fieldset>;
}
