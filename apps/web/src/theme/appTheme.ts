import type { ThemeConfig } from "antd";

/**
 * 全站浅色主题的唯一数值来源。
 *
 * 业务 CSS 继续通过 global.css 中的兼容别名读取旧变量；Ant Design
 * 则由同一组值生成 token。正式的主题切换会在后续阶段增加。
 */
export const appThemeValues = {
  color: {
    background: "#f5f5f7",
    surface: "#ffffff",
    input: "#fafafa",
    border: "#e9e9ee",
    text: "#242429",
    textMuted: "#94949e",
    accent: "#7b58d3",
    accentHover: "#3a3542",
    accentSoft: "#f3eefb",
    accentBorder: "#cdbbe4",
    danger: "#e5534b",
    dangerCreator: "#bf5257",
    success: "#3fb950",
    warning: "#d9912b",
    info: "#4f7fd8",
  },
  font: {
    family: 'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif',
    size: 14,
  },
  radius: {
    control: 8,
    surface: 12,
  },
  control: {
    compact: 32,
    default: 36,
    emphasized: 40,
  },
  space: {
    1: 4,
    2: 8,
    3: 12,
    4: 16,
    5: 24,
    6: 32,
  },
  motion: {
    fast: "0.2s",
    normal: "0.24s",
    slow: "0.28s",
  },
  layout: {
    contentMax: 1430,
    projectHeaderDesktop: 66,
    projectHeaderCompact: 104,
    layerHeader: 200,
    layerOverlay: 1000,
  },
} as const;

export const appThemeCssVariables = {
  "--app-color-bg-layout": appThemeValues.color.background,
  "--app-color-bg-surface": appThemeValues.color.surface,
  "--app-color-bg-container": appThemeValues.color.surface,
  "--app-color-bg-input": appThemeValues.color.input,
  "--app-color-border": appThemeValues.color.border,
  "--app-color-text": appThemeValues.color.text,
  "--app-color-text-muted": appThemeValues.color.textMuted,
  "--app-color-accent": appThemeValues.color.accent,
  "--app-color-accent-hover": appThemeValues.color.accentHover,
  "--app-color-accent-soft": appThemeValues.color.accentSoft,
  "--app-color-accent-border": appThemeValues.color.accentBorder,
  "--app-color-danger": appThemeValues.color.danger,
  "--app-color-danger-bg": "#fff2f3",
  "--app-color-danger-creator": appThemeValues.color.dangerCreator,
  "--app-color-success": appThemeValues.color.success,
  "--app-color-warning": appThemeValues.color.warning,
  "--app-color-info": appThemeValues.color.info,
  "--app-font-family": appThemeValues.font.family,
  "--app-font-size": `${appThemeValues.font.size}px`,
  "--app-radius-control": `${appThemeValues.radius.control}px`,
  "--app-radius-surface": `${appThemeValues.radius.surface}px`,
  "--app-control-compact": `${appThemeValues.control.compact}px`,
  "--app-control-default": `${appThemeValues.control.default}px`,
  "--app-control-emphasized": `${appThemeValues.control.emphasized}px`,
  "--app-space-1": `${appThemeValues.space[1]}px`,
  "--app-space-2": `${appThemeValues.space[2]}px`,
  "--app-space-3": `${appThemeValues.space[3]}px`,
  "--app-space-4": `${appThemeValues.space[4]}px`,
  "--app-space-5": `${appThemeValues.space[5]}px`,
  "--app-space-6": `${appThemeValues.space[6]}px`,
  "--app-motion-fast": appThemeValues.motion.fast,
  "--app-motion-normal": appThemeValues.motion.normal,
  "--app-motion-slow": appThemeValues.motion.slow,
  "--app-content-max-width": `${appThemeValues.layout.contentMax}px`,
  "--app-project-header-height": `${appThemeValues.layout.projectHeaderDesktop}px`,
  "--app-project-header-height-compact": `${appThemeValues.layout.projectHeaderCompact}px`,
  "--app-layer-header": String(appThemeValues.layout.layerHeader),
  "--app-layer-overlay": String(appThemeValues.layout.layerOverlay),
  "--app-shadow-popover": "0 14px 36px #2020241a",
} satisfies Record<`--app-${string}`, string>;

export function applyAppThemeCssVariables(target: HTMLElement): void {
  for (const [name, value] of Object.entries(appThemeCssVariables)) {
    target.style.setProperty(name, value);
  }
}

/** Ant Design 的派生结果在这里显式校准，避免改变现有页面背景。 */
export const appAntdTheme: ThemeConfig = {
  cssVar: { key: "ai-drama" },
  hashed: true,
  token: {
    colorPrimary: appThemeValues.color.accent,
    colorPrimaryHover: appThemeValues.color.accentHover,
    colorBgBase: appThemeValues.color.surface,
    colorBgLayout: appThemeValues.color.background,
    colorBgContainer: appThemeValues.color.surface,
    colorBgElevated: appThemeValues.color.surface,
    colorFillAlter: appThemeValues.color.input,
    colorBorder: appThemeValues.color.border,
    colorBorderSecondary: appThemeValues.color.border,
    colorText: appThemeValues.color.text,
    colorTextSecondary: appThemeValues.color.textMuted,
    colorError: appThemeValues.color.danger,
    colorSuccess: appThemeValues.color.success,
    colorWarning: appThemeValues.color.warning,
    colorInfo: appThemeValues.color.info,
    fontFamily: appThemeValues.font.family,
    fontSize: appThemeValues.font.size,
    borderRadius: appThemeValues.radius.control,
    borderRadiusLG: appThemeValues.radius.surface,
    controlHeight: appThemeValues.control.default,
    controlHeightSM: appThemeValues.control.compact,
    controlHeightLG: appThemeValues.control.emphasized,
    motionDurationMid: appThemeValues.motion.normal,
  },
  components: {
    Button: {
      defaultShadow: "none",
      primaryShadow: "none",
    },
    Modal: {
      borderRadiusLG: appThemeValues.radius.surface,
    },
  },
};
