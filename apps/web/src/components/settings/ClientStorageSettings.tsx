import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FolderOpen } from 'lucide-react';
import { Button } from '@/components/ui';
import { desktopClient, type ClientStorageAction } from '@/lib/desktopClient';

const formatSize = (bytes: number) => `${(bytes / 1024 ** 2).toFixed(1)} MB`;
export function ClientStorageSettings() {
  const client = desktopClient();
  const queries = useQueryClient();
  const query = useQuery({ queryKey: ['desktop-storage'], enabled: !!client, retry: false,
    queryFn: () => client!.storage(), refetchInterval: 15000 });
  const action = useMutation({ mutationFn: (value: ClientStorageAction) => client!.storage(value),
    onSuccess: value => queries.setQueryData(['desktop-storage'], value) });
  if (!client) return null;
  const data = query.data;
  const busy = action.isPending;
  const run = (type: ClientStorageAction['type']) => action.mutate({ type });
  return <>
    {(query.error || action.error) && <p role="alert">本机存储操作失败，请检查目录权限后重试。<Button onClick={() => { action.reset(); void query.refetch(); }}>重试</Button></p>}
    <section className="control-config-panel" aria-label="文件保存"><header className="control-config-header"><h2>文件保存</h2></header><div className="control-config-body">
      <div className="client-storage-row"><div><strong>默认下载位置</strong><code>{data?.downloadDirectory || '正在读取…'}</code></div><Button disabled={busy || !data} onClick={() => run('download-directory')}>更改</Button><Button disabled={busy || !data} icon={<FolderOpen size={16}/>} onClick={() => run('open-download')}>打开文件夹</Button></div>
    </div></section>
    <section className="control-config-panel" aria-label="本地缓存"><header className="control-config-header"><h2>本地缓存</h2></header><div className="control-config-body">
      <div className="client-storage-row"><div><strong>缓存位置</strong><code>{data?.cacheDirectory || '正在读取…'}</code></div><Button disabled={busy || !data} onClick={() => run('cache-directory')}>更改</Button><Button disabled={busy || !data} icon={<FolderOpen size={16}/>} onClick={() => run('open-cache')}>打开文件夹</Button></div>
      <div className="client-storage-row"><div><strong>已用大小</strong><p>{data ? formatSize(data.cacheUsed) : '正在读取…'}</p></div><Button disabled={busy || !data} onClick={() => run('clear-cache')}>清理缓存</Button></div>
    </div></section>
  </>;
}
