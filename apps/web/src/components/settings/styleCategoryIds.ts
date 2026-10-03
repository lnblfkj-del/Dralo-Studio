export const styleCategoryIds = (style: { category_ids?: number[]; category_id?: number | null }) => style.category_ids?.length ? style.category_ids : (style.category_id ? [style.category_id] : []);
