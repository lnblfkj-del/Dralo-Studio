export type PreviewCase = "35" | "45" | "empty" | "missing" | "failed" | "stale" | "long";
export type AssetKind = "角色" | "场景" | "道具" | "服装/造型" | "声音" | "视频" | "画布" | "参考图";
export const assetKinds: AssetKind[] = ["角色", "场景", "道具", "服装/造型", "声音", "视频", "画布", "参考图"];
export type PreviewAsset = { id: number; name: string; kind: AssetKind; note: string; episode: string; versions: number; adopted: number; state: string };
export const assetSeed: PreviewAsset[] = [
  { id: 1, name: "沈知微", kind: "角色", note: "主角 · 食品研发师 / 冷宫妃子", episode: "1,2,3,4,5", versions: 3, adopted: 1, state: "已采用" },
  { id: 2, name: "苏嬷嬷", kind: "角色", note: "配角 · 冷宫掌事", episode: "1,2,4,5", versions: 2, adopted: 1, state: "已采用" },
  { id: 3, name: "阿禾", kind: "角色", note: "配角 · 冷宫宫女", episode: "1,3,5", versions: 1, adopted: 0, state: "待采用" },
  { id: 4, name: "顾行舟", kind: "角色", note: "配角 · 户部年轻主事", episode: "4,5", versions: 0, adopted: 0, state: "待补素材" },
  { id: 5, name: "冷宫小厨房 · 清晨", kind: "场景", note: "木窗 / 暖晨光 / 石灶", episode: "1,2", versions: 2, adopted: 1, state: "已采用" },
  { id: 6, name: "旧粮袋", kind: "道具", note: "粗麻布袋，账面三十斤", episode: "1,2", versions: 1, adopted: 1, state: "已采用" },
  { id: 7, name: "沈知微 · 素色常服", kind: "服装/造型", note: "关联沈知微 · 淡青交领长衫", episode: "1,2,3", versions: 2, adopted: 1, state: "已采用" },
  { id: 8, name: "沈知微 · 基础声线", kind: "声音", note: "角色声音 · 清晰温和、中音、语速平稳", episode: "1,2,3,4,5", versions: 2, adopted: 1, state: "已采用" },
  { id: 9, name: "清晨炊烟", kind: "声音", note: "配乐 · 轻拨弦，克制而温暖 · 35秒", episode: "1,2", versions: 1, adopted: 1, state: "已采用" },
  { id: 10, name: "院落晨风", kind: "声音", note: "环境声 · 鸟鸣与风声 · 20秒", episode: "1", versions: 1, adopted: 0, state: "待采用" },
  { id: 11, name: "木门轻响", kind: "声音", note: "音效 · 开门 · 2秒", episode: "1", versions: 0, adopted: 0, state: "待补素材" },
  { id: 12, name: "第1集 · 片段01", kind: "视频", note: "片段视频 · 7秒 · 16:9", episode: "1", versions: 1, adopted: 1, state: "已采用" },
  { id: 13, name: "厨房场景规划", kind: "画布", note: "画布关联示意，非真实画布", episode: "1", versions: 0, adopted: 0, state: "资料就绪" },
  { id: 14, name: "木窗与灶台参考", kind: "参考图", note: "场景构图参考", episode: "1", versions: 1, adopted: 1, state: "已采用" },
];
export const characterFields = [
  ["name", "姓名"], ["role", "身份"], ["age", "年龄 / 年龄段"], ["description", "人物介绍"],
  ["goal", "人物目标"], ["conflict", "核心冲突"], ["arc", "成长弧线"],
  ["appearance", "样貌"], ["costume", "服装与造型"], ["voice", "声线与表达"],
] as const;
export type Person = Record<typeof characterFields[number][0], string>;
export const peopleSeed: Person[] = ["沈知微", "苏嬷嬷", "阿禾", "萧承晏", "陆明珠", "顾行舟"].map((name, index) => ({
  name, role: ["主角 · 食品研发师", "冷宫掌事", "冷宫宫女", "皇子", "宫中贵妃", "户部主事"][index]!,
  age: index === 0 ? "二十余岁（样稿建议，非原文事实）" : "原文未说明",
  description: index === 0 ? "带着现代食品研发知识，在有限食材和宫规约束下组织冷宫自救。" : "与冷宫食肆的生存、经营及粮账调查有联系。此处为排版样例。",
  goal: "保护身边的人，查明粮食供应中的疑点。", conflict: "个人处境与掌握资源者的利益产生冲突。", arc: "从独自应对困境，逐渐学会协作并承担责任。",
  appearance: index === 0 ? "清秀眉眼，发髻利落，目光沉静。（样稿建议）" : "待补充；不自动推断为正式设定。",
  costume: index === 0 ? "淡青交领长衫，袖口简洁，无繁复饰物。" : "按剧本已确认的身份与集场设定准备。",
  voice: index === 0 ? "中音，吐字清晰，语速平稳；强调事实时简短有力。" : "未绑定音色；声线资料可独立补充。",
}));
export type Segment = { id: number; seconds: number; text: string; candidate: number; adopted: number; stale: boolean; candidateText?: string; candidateSeconds?: number };
export function segmentSeed(long = false): Segment[] {
  return (long ? [10, 14, 11, 10] : [7, 8, 7, 7, 6]).map((seconds, index) => ({
    id: index + 1, seconds, candidate: 0, adopted: 0, stale: false,
    text: [
      "清晨，小厨房。远景缓缓推进到石灶，沈知微打开粮袋。\n她垂眼辨认粮食，表情专注。\n沈知微（平静）：先看看，还剩多少。\n声音：院落晨风，轻拨弦配乐淡入。",
      "中景，苏嬷嬷递过旧账。沈知微摸到袋底，抬眼看向账面。\n苏嬷嬷（迟疑）：这里写的是三十斤。\n声音：纸页翻动，配乐降低。",
      "特写，秤砣停住。沈知微皱眉，用手指按住账上的数字。\n沈知微：二十一斤不到。\n声音：秤杆轻响，音乐停顿。",
      "双人中景。苏嬷嬷想开口，沈知微轻轻摇头，把粮袋系好。\n沈知微（坚定）：先记下来，再找下一张账。\n声音：配乐恢复，情绪转为克制的紧张。",
      "近景，账本合上。窗外有人影经过，两人同时望向门口。\n声音：木门轻响，配乐收束。",
    ][index]!,
  }));
}
