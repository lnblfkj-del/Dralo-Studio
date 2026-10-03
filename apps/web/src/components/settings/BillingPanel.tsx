import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleDollarSign } from "lucide-react";
import { addReceipt, getBilling, type BillingCall } from "@/api/billing";
import { toErrorMessage } from "@/api/client";
import { listProviders } from "@/api/providers";
import { Button, Dialog } from "@/components/ui";
import "@/styles/billing.css";

function ReceiptForm({ call, onDone }: {call: BillingCall; onDone: () => Promise<void>}) {
  const [reference, setReference] = useState(""), [amount, setAmount] = useState(""), [note, setNote] = useState("");
  const [kind, setKind] = useState<"charge" | "refund">("charge");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  return <form className="billing-receipt" onSubmit={async e => {
    e.preventDefault(); if(busy) return; setBusy(true);setError("");
    try { await addReceipt(call.id,{reference,amount,currency:call.currency,kind,note});await onDone();setReference("");setAmount("");setNote(""); }
    catch(e){setError(toErrorMessage(e));}finally{setBusy(false);}
  }}>
    <p>仅登记你已核实的渠道账单，不会支付或退款。不修改历史凭据；错误登记请用新编号录入冲销记录。</p>
    <label>账单唯一编号（含行号）<input required maxLength={200} value={reference} onChange={e=>setReference(e.target.value)} /></label>
    <label>类型<select value={kind} onChange={e=>setKind(e.target.value as "charge" | "refund")}><option value="charge">扣费</option><option value="refund">退款 / 冲销</option></select></label>
    <label>金额（{call.currency}）<input required type="number" min="0" step="any" value={amount} onChange={e=>setAmount(e.target.value)} /></label>
    <label>核对依据 / 备注<input required maxLength={1000} value={note} onChange={e=>setNote(e.target.value)} placeholder="渠道日志编号、核对日期等，不要填写密钥" /></label>
    {error && <p role="alert">{error}</p>}
    <Button type="submit" variant="primary" loading={busy} disabled={!reference.trim() || !amount || !note.trim()}>确认登记账单（人工）</Button>
  </form>;
}

function Ledger({ onClose }: {onClose: () => void}) {
  const client = useQueryClient();
  const [page, setPage] = useState(0), [since,setSince] = useState(""), [until,setUntil] = useState("");
  const [provider,setProvider] = useState(""), [project,setProject] = useState(""), [kind,setKind] = useState("");
  const providers = useQuery({queryKey:["providers"],queryFn:listProviders});
  const filters: Record<string,string|number> = {offset:page*20,limit:20};
  if(since)filters.since=new Date(`${since}T00:00:00`).toISOString();
  if(until){const end=new Date(`${until}T00:00:00`);end.setDate(end.getDate()+1);filters.until=end.toISOString();}
  if(provider)filters.provider_id=provider;
  if(project)filters.project_id=project;
  if(kind)filters.kind=kind;
  const data = useQuery({queryKey:["billing",filters],queryFn:()=>getBilling(filters),refetchInterval:15000});
  return <Dialog open className="billing-dialog" title="费用明细与账单核对" description="本地用量核算、渠道账单与人工核对记录。" size="large" onClose={onClose} footer={<><span className="billing-page-total">共 {data.data?.total ?? "—"} 次调用</span><Button disabled={!page} onClick={()=>setPage(p=>p-1)}>上一页</Button><span>第 {page+1} 页</span><Button disabled={!data.data || (page+1)*20>=data.data.total} onClick={()=>setPage(p=>p+1)}>下一页</Button></>}>
    <div className="billing-content">
      <p>本地用量核算 ≠ 渠道实际扣费。账单金额来自人工登记；数据缺失保留待核算，不计为零。以下按调用时间筛选。</p>
      <div className="billing-filters">
        <label>开始日期<input type="date" value={since} onChange={e=>{setSince(e.target.value);setPage(0);}} /></label>
        <label>结束日期<input type="date" value={until} onChange={e=>{setUntil(e.target.value);setPage(0);}} /></label>
        <label>渠道<select value={provider} onChange={e=>{setProvider(e.target.value);setPage(0);}}><option value="">全部渠道</option>{providers.data?.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label>项目 ID<input type="number" min="1" value={project} onChange={e=>{setProject(e.target.value);setPage(0);}} /></label>
        <label>类型<select value={kind} onChange={e=>{setKind(e.target.value);setPage(0);}}><option value="">全部类型</option>{Object.entries({text:"文本",text_test:"文本测试",image:"图片",video:"视频",tts:"配音"}).map(([v,t])=><option key={v} value={v}>{t}</option>)}</select></label>
      </div>
      {data.isError && <p role="alert">账本读取失败，请稍后重试。</p>}
      <div className="billing-totals">{Object.entries(data.data?.totals ?? {}).map(([currency,t])=><article key={currency}><strong>{currency} · 核算 {t.calculated}</strong><p>人工账单净额 {t.registered_bill}</p><small>已核算 {t.calculated_count} · 待核算 {t.pending_count} · 未登记账单 {t.unreconciled_count} · 有差异 {t.difference_count}</small></article>)}</div>
      <p>{data.data?.note}</p>
      {data.data?.total===0 && <p>暂无调用记录。旧任务不会凭空补算费用；新调用开始后会记录。</p>}
      {data.data?.items.map(call=><article key={call.id} className="billing-call">
        <header><strong>#{call.id} · {call.provider} / {call.model}</strong><small>{call.job_id ? `任务 #${call.job_id}` : "模型测试"} · {new Date(call.created_at).toLocaleString()}</small></header>
        <div className="billing-amounts"><span>用量核算：{call.amount===null?"待核算":`${call.currency} ${call.amount}`}</span><span>人工账单：{call.bill_amount===null?"待登记":`${call.currency} ${call.bill_amount}`}</span><span>差额：{call.difference===null?"待核对":`${call.currency} ${call.difference}`}</span></div>
        <p>{call.reason}</p>
        <details><summary>用量、价格快照与凭据</summary><pre>{JSON.stringify({用量:call.meter,提交时价格:call.snapshot},null,2)}</pre>{call.receipts.map(r=><p key={r.id}>{r.reference} · {r.kind==="refund"?"退款/冲销":"扣费"} {call.currency} {r.amount} · {r.note}</p>)}</details>
        <details><summary>登记已核实账单 / 退款</summary><ReceiptForm call={call} onDone={()=>client.invalidateQueries({queryKey:["billing"]})}/></details>
      </article>)}
    </div>
  </Dialog>;
}

export function BillingCard() {
  const [open,setOpen] = useState(false);
  const data = useQuery({queryKey:["billing","card"],queryFn:()=>getBilling({limit:1}),refetchInterval:15000});
  return <article><span className="task-stat-icon task-stat-icon--cost"><CircleDollarSign size={19}/></span><div><small>核算费用（累计）</small>
    <strong className="billing-card-amount">{Object.entries(data.data?.totals ?? {}).map(([currency,t])=><span key={currency}>{currency} {t.calculated}</span>)}{!Object.keys(data.data?.totals ?? {}).length && (data.isError?"读取失败":"暂无核算")}</strong>
    <button className="billing-open" onClick={()=>setOpen(true)}>费用明细 / 账单核对</button></div>{open&&<Ledger onClose={()=>setOpen(false)}/>}</article>;
}
