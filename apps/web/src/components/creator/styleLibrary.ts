export const styleLibrary = [
  { id: "retro", name: "复古科幻原子朋克", category: "真人", palette: "retro", prompt: "复古未来主义，暖沙色与青绿色，胶片颗粒，机械细节。" },
  { id: "palace", name: "宫廷权谋冷峻", category: "真人", palette: "palace", prompt: "深色宫廷，低调光，金色细节，克制构图与人物对峙。" },
  { id: "noir", name: "悬疑冷调", category: "真人", palette: "noir", prompt: "冷青色调，高反差，雨夜街巷，阴影与紧张气氛。" },
  { id: "romance", name: "古偶唯美柔光", category: "真人", palette: "romance", prompt: "古风服饰，柔和逆光，浅粉与暖白，唯美留白。" },
  { id: "youth", name: "青春胶片", category: "真人", palette: "youth", prompt: "青春校园，自然光，清新绿色，细腻胶片质感。" },
  { id: "everyday", name: "都市生活写实", category: "真人", palette: "everyday", prompt: "日常生活，真实街景，自然肤色，温暖纪实摄影。" },
  { id: "ink", name: "东方水墨", category: "2D", palette: "ink", prompt: "水墨山水，黑白层次，纸张肌理，简洁线条与留白。" },
  { id: "anime", name: "清新动画", category: "2D", palette: "anime", prompt: "清晰二维线稿，明亮天空，干净色块，轻盈动画质感。" },
  { id: "storybook", name: "温暖绘本", category: "2D", palette: "storybook", prompt: "温暖手绘，纸张纹理，柔和色彩，童话叙事。" },
  { id: "clay", name: "黏土定格", category: "3D", palette: "clay", prompt: "手工黏土，圆润造型，微缩布景，柔软材质。" },
  { id: "fantasy", name: "奇幻冒险", category: "3D", palette: "fantasy", prompt: "立体幻想世界，层次丰富，宏大场景，梦幻光线。" },
  { id: "scifi", name: "未来科幻", category: "3D", palette: "scifi", prompt: "未来都市，几何建筑，冷色霓虹，金属与玻璃材质。" },
] as const;

export function styleName(id?: string) {
  return id === "custom" ? "自定义风格" : styleLibrary.find((style) => style.id === id)?.name ?? "默认风格";
}
