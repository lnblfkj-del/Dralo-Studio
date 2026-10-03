import { http } from "@/api/client";

export interface StyleCategory { id: number; name: string; position: number; count: number }
const path = "/agent-config/styles/categories";
export const listStyleCategories = async (): Promise<StyleCategory[]> => (await http.get<StyleCategory[]>(path)).data;
export const addStyleCategory = async (name: string): Promise<StyleCategory> => (await http.post<StyleCategory>(path, { name })).data;
export const renameStyleCategory = async (id: number, name: string) => http.patch(`${path}/${id}`, { name });
export const orderStyleCategories = async (ids: number[]) => http.put(`${path}/order`, { ids });
export const deleteStyleCategory = async (id: number, targetId: number | null) => http.delete(`${path}/${id}`, { params: { target_id: targetId ?? undefined } });
