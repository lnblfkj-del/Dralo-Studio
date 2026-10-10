import { http } from "@/api/client";
export interface Receipt { id: number; reference: string; amount: string; kind: string; note: string; created_at: string }
export interface BillingCall {
  quota_usage?: {unit:string;amount:string|null;reason:string} | null;
  id: number; job_id: number | null; project_id: number | null; provider_id: number;
  provider: string; model: string; kind: string; state: string; currency: string;
  amount: string | null; bill_amount: string | null; difference: string | null;
  reason: string; created_at: string; meter: Record<string, unknown>; snapshot: Record<string, unknown>; receipts: Receipt[];
}
export interface BillingReport { total: number; items: BillingCall[]; note: string; totals: Record<string, {calculated: string; registered_bill: string; calculated_count: number; pending_count: number; unreconciled_count: number; difference_count: number}> }
export async function getBilling(params: Record<string, string | number> = {}): Promise<BillingReport> {
  return (await http.get<BillingReport>("/jobs/billing", {params})).data;
}
export async function addReceipt(id: number, value: {reference: string; amount: string; currency: string; kind: "charge" | "refund"; note: string}) {
  return (await http.post(`/jobs/billing/${id}/receipts`, value)).data;
}
