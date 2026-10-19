import {useState} from 'react';
import {FolderOpen, Upload, Settings2} from 'lucide-react';
import {Button} from '@/components/ui';
import type {VideoPackage} from '@/api/videoTools';
import {VideoPackageSummary, VideoPresetPicker} from './VideoPresetPicker';
import {VideoConfigImportDialog} from './VideoConfigImportDialog';

export function VideoRegistrationConfig({providerId, identifier, value, onChange, onIdentifier, disabled}: {
  providerId: number; identifier: string; value: VideoPackage | null;
  onChange: (value: VideoPackage | null, matchedIdentifier?: boolean) => void; disabled: boolean;
  onIdentifier?: (value: string) => void;
}) {
  const [picker, setPicker] = useState(false), [importOpen, setImportOpen] = useState(false);
  return <div className="video-registration-config" data-local-tools>
    <div className="video-tools-actions">
      <Button icon={<FolderOpen size={15} />} disabled={disabled} onClick={() => setPicker(true)}>选择已有配置</Button>
      <Button icon={<Upload size={15} />} disabled={disabled} onClick={() => setImportOpen(true)}>导入配置</Button>
      <Button icon={<Settings2 size={15} />} disabled={disabled} onClick={() => onChange(null)}>手动配置</Button>
    </div>
    {value && <div className="video-preset-preview"><strong>{value.name}</strong><VideoPackageSummary value={value} /></div>}
    {picker && <VideoPresetPicker providerId={providerId} identifier={identifier.trim()} onIdentifier={onIdentifier} onApply={value => onChange(value, true)} onClose={() => setPicker(false)} />}
    {importOpen && <VideoConfigImportDialog providerId={providerId} identifier={identifier} current={value ? () => value : undefined} onUse={onChange} onClose={() => setImportOpen(false)} />}
  </div>;
}
