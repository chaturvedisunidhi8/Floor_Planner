/**
 * Mirrors the backend Pydantic schemas. Kept hand-written rather than
 * generated so the shapes stay readable; `/openapi.json` is the source of
 * truth if they ever drift.
 */

export type PlotShape = 'rectangle' | 'square';
export type BHKType = '1BHK' | '2BHK' | '3BHK' | '4BHK';
export type InteriorStyle = 'modern' | 'minimal' | 'luxury' | 'traditional';
export type Facing = 'north' | 'south' | 'east' | 'west' | 'any';

export type VastuPrinciple =
  | 'pooja_northeast'
  | 'kitchen_southeast'
  | 'master_bedroom_southwest'
  | 'living_northeast'
  | 'bathrooms_northwest'
  | 'storage_southwest';

export interface OptionItem {
  value: string;
  label: string;
  description: string;
}

export interface RangeSpec {
  min: number;
  max: number;
  step: number;
  default: number;
}

/** Length and width of a single room, in feet. */
export interface RoomDimensions {
  length_ft: number;
  width_ft: number;
}

/** Default room sizes keyed by room value, e.g. `living_room`. */
export type RoomDefaults = Record<string, RoomDimensions>;

export interface OptionsResponse {
  plot_shapes: OptionItem[];
  bhk_types: OptionItem[];
  rooms: OptionItem[];
  features: OptionItem[];
  styles: OptionItem[];
  facings: OptionItem[];
  vastu_principles: OptionItem[];
  plot_width_range: RangeSpec;
  plot_length_range: RangeSpec;
  room_defaults: RoomDefaults;
  room_dimension_range: RangeSpec;
  max_attached_bathrooms: number;
  max_common_bathrooms: number;
}

export interface PlotDetails {
  width_ft: number;
  length_ft: number;
  shape: PlotShape;
  facing: Facing;
}

export interface BathroomRequirements {
  attached_count: number;
  common_count: number;
}

/** Optional directional constraints. `principles` is ignored while disabled. */
export interface VastuPreferences {
  enabled: boolean;
  principles: VastuPrinciple[];
}

export interface FloorPlanRequirements {
  plot: PlotDetails;
  bhk: BHKType;
  rooms: string[];
  /** Per-room size targets, keyed by room value. Only selected rooms appear. */
  room_dimensions: Record<string, RoomDimensions>;
  bathrooms: BathroomRequirements;
  features: string[];
  vastu: VastuPreferences;
  style: InteriorStyle;
  notes: string;
}

export interface GenerationRequest {
  requirements: FloorPlanRequirements;
  variants?: number | null;
  seed?: number | null;
}

export interface ScoreBreakdown {
  semantic: number;
  plot_dimensions: number;
  bedrooms: number;
  required_rooms: number;
  bathrooms: number;
  parking: number;
  balcony: number;
  style: number;
  layout_compatibility: number;
}

export interface TemplateSummary {
  id: string;
  name: string;
  bhk: BHKType;
  plot_width_ft: number;
  plot_length_ft: number;
  style: InteriorStyle;
  room_count: number;
  description: string;
}

export interface TemplateMatch {
  template: TemplateSummary;
  score: number;
  breakdown: ScoreBreakdown;
  rank: number;
  rationale: string;
}

export interface LayoutRoom {
  type: string;
  name: string;
  x: number;
  y: number;
  /** Feet. Area is derived on the client rather than sent over the wire. */
  width: number;
  height: number;
}

export type LayoutStatus = 'feasible' | 'infeasible';

export interface InfeasibilityDetail {
  stage: string;
  reason: string;
  detail: string;
  suggestions: string[];
}

export interface GeneratedLayout {
  id: string;
  name: string;
  bhk: BHKType;
  style: InteriorStyle;
  plot_width_ft: number;
  plot_length_ft: number;
  plot_size_label: string;
  built_up_sqft: number;
  description: string;
  rooms: LayoutRoom[];
  image_url: string;
  thumbnail_url: string | null;
  source_template_id: string;
  source_template_name: string;
  match_score: number;
  render_mode: string;
  validation_warnings: string[];
  seed: number;
  /** "feasible" for a usable layout, "infeasible" when the solver refused the brief. */
  status: LayoutStatus;
  /** 0..100 from the geometry engine's scoring pass; null for the legacy engine. */
  quality_score: number | null;
  /** Populated only when status is "infeasible". */
  infeasibility: InfeasibilityDetail | null;
  /** Null unless the brief asked for Vastu compliance. */
  vastu_score: number | null;
  vastu_notes: string[];
}

export interface GenerationResponse {
  session_id: string;
  created_at: string;
  requirement_summary: string;
  analysis: Record<string, unknown>;
  matches: TemplateMatch[];
  layouts: GeneratedLayout[];
  warnings: string[];
}

export interface ApiError {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}
