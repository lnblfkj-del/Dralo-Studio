/**
 * 与后端契约对应的类型定义。
 *
 * 字段名与 apps/server/app/schemas 保持一致，后端改动时需同步更新。
 *
 * 本文件为 barrel，按业务域拆分到同目录下的模块中：
 *   base.ts       基础/通用类型
 *   project.ts    项目与剧本
 *   production.ts 分集生产（分镜/分段/导演计划）
 *   job.ts        任务与执行状态
 *   provider.ts   模型供应商
 *   market.ts     市场调研
 *   agent.ts      智能体与技能
 *   creation.ts   创作会话与产物
 *   canvas.ts     画布
 *   asset.ts      资产与媒体
 */

export * from "./base";
export * from "./project";
export * from "./production";
export * from "./job";
export * from "./provider";
export * from "./market";
export * from "./agent";
export * from "./creation";
export * from "./canvas";
export * from "./asset";
