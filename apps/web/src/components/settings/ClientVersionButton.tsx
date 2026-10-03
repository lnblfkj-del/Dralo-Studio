import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Info } from 'lucide-react';
import { desktopClient } from '@/lib/desktopClient';
import '@/styles/client-version.css';

export function ClientVersionButton() {
  const client = desktopClient();
  const [failed, setFailed] = useState(false);
  const query = useQuery({ queryKey: ['desktop-version'], enabled: !!client, retry: false,
    queryFn: () => client!.version(), refetchInterval: 15000 });
  if (!client) return null;
  const available = ['available', 'ready', 'downloading'].includes(query.data?.status || '');
  return <><button className="client-version-button" type="button" title="版本信息" onClick={() => {
    setFailed(false); void client.showVersion().then(() => query.refetch()).catch(() => setFailed(true));
  }}><Info size={16}/><span>版本信息</span>{available && <small className="client-update-badge">{query.data?.status === 'ready' ? '重启更新' : '发现新版本'}</small>}</button>{failed && <small role="alert">版本信息暂时不可用</small>}</>;
}
