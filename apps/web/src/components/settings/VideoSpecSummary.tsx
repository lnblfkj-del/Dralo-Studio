import type {PlanningMode} from '@/api/planningCapabilities';
import {durationLabel, inputNames} from './videoSupport';

export function VideoSpecSummary({modes}: {modes: PlanningMode[]}) {
  const durations = [...new Set(modes.map(mode => durationLabel(mode.durations)))];
  const values = [
    ['生成方式', [...new Set(modes.map(mode => inputNames[mode.input_mode] ?? mode.input_mode))].join('、')],
    ['清晰度', [...new Set(modes.map(mode => mode.resolution))].join(' / ')],
    ['画幅', [...new Set(modes.map(mode => mode.aspect_ratio))].join(' / ')],
    ['时长', durations.length > 1 ? '不同规格的时长不同' : durations[0] ?? ''],
  ];
  return <section className="video-spec-summary" aria-label="视频规格摘要">
    <h3>视频规格 <small>{modes.length ? `${modes.length} 个已配置组合` : '待设置'}</small></h3>
    {modes.length ? <dl>{values.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl> : <p>请导入配置，或展开下方设置填写视频规格。</p>}
  </section>;
}
