export type ThemeName = "studio" | "stage" | "paper";

export interface UiSettings {
  fontSize: number;
  lineHeight: number;
  scrollSpeed: number;
  contentWidth: number;
  theme: ThemeName;
  fontFamily: string;
  fontWeight: number;
  textColor: string;
  bgColor: string;
}

export interface ProgressResponse {
  matched_index: number;
  matched_preview: string;
  confidence: number;
}

export interface HealthResponse {
  status: string;
  model_size?: string;
  device?: string;
}
