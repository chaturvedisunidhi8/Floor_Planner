/**
 * Length units for the wizard's display layer.
 *
 * Feet are the base unit and the only one that is ever stored: every value in
 * the requirements object is `_ft`, the API speaks feet, and the generated
 * drawings are dimensioned in feet. Changing the unit therefore changes what
 * the user reads and types, never what the plot actually measures - which is
 * what keeps a lap through metres and back from shaving inches off the plot.
 */

export type UnitKey = 'ft' | 'm' | 'yd' | 'in' | 'cm';

export interface Unit {
  key: UnitKey;
  /** Long name, for the dropdown. */
  label: string;
  /** Short suffix shown beside a number. */
  abbr: string;
  /** How many of this unit make one foot. */
  perFoot: number;
  /** Increment for sliders and steppers, in this unit. */
  step: number;
  /** Decimal places to show. */
  decimals: number;
}

export const UNITS: Record<UnitKey, Unit> = {
  // A decimal place on the base unit is not fussiness: a plot set in inches
  // and read back in feet can land on 33.5, and rounding that to "34" would
  // disagree with the area shown right beside it. Whole feet still print whole.
  ft: { key: 'ft', label: 'Feet', abbr: 'ft', perFoot: 1, step: 1, decimals: 1 },
  m: { key: 'm', label: 'Meters', abbr: 'm', perFoot: 0.3048, step: 0.1, decimals: 2 },
  yd: { key: 'yd', label: 'Yards', abbr: 'yd', perFoot: 1 / 3, step: 0.5, decimals: 2 },
  in: { key: 'in', label: 'Inches', abbr: 'in', perFoot: 12, step: 6, decimals: 0 },
  cm: { key: 'cm', label: 'Centimeters', abbr: 'cm', perFoot: 30.48, step: 5, decimals: 0 },
};

export const UNIT_KEYS = Object.keys(UNITS) as UnitKey[];

export const BASE_UNIT: UnitKey = 'ft';

export const UNIT_OPTIONS = UNIT_KEYS.map((key) => ({
  value: key,
  label: `${UNITS[key].label} (${UNITS[key].abbr})`,
}));

export function unit(key: UnitKey): Unit {
  return UNITS[key] ?? UNITS.ft;
}

/** Feet -> the chosen unit. */
export function toUnit(feet: number, key: UnitKey): number {
  return feet * unit(key).perFoot;
}

/** The chosen unit -> feet. Everything written back to state goes through here. */
export function toFeet(value: number, key: UnitKey): number {
  return value / unit(key).perFoot;
}

/** Round to the unit's own increment, so a stepper never lands between notches. */
export function snapToStep(value: number, key: UnitKey): number {
  const { step } = unit(key);
  return Math.round(value / step) * step;
}

/**
 * A length for display, e.g. `9.14`.
 *
 * Trailing zeros are dropped - "9.1 m" reads better than "9.10 m" - but the
 * decimal cap stays, so a converted value never arrives as 9.144000000000001.
 */
export function formatLength(feet: number, key: UnitKey): string {
  return format(toUnit(feet, key), unit(key).decimals);
}

/** Square feet in the chosen unit. Areas convert by the square of the ratio. */
export function toAreaUnit(sqft: number, key: UnitKey): number {
  return sqft * unit(key).perFoot ** 2;
}

/** `1,350` in square feet, `125.42` in square metres. */
export function formatArea(sqft: number, key: UnitKey): string {
  // Small units make for absurdly large areas; no one wants 194,400.00 sq in.
  const decimals = key === 'ft' || key === 'in' || key === 'cm' ? 0 : 2;
  return format(toAreaUnit(sqft, key), decimals);
}

/** `sq m`, `sq ft` - the suffix that goes with {@link formatArea}. */
export function areaAbbr(key: UnitKey): string {
  return `sq ${unit(key).abbr}`;
}

/**
 * A feet range expressed in the chosen unit, on that unit's step grid.
 *
 * The bounds are pulled *inwards* to the nearest step so that neither end of a
 * slider can hand the backend a value outside the range it published.
 */
export function convertRange(
  range: { min: number; max: number },
  key: UnitKey,
): { min: number; max: number; step: number } {
  const { step } = unit(key);
  return {
    min: ceilTo(toUnit(range.min, key), step),
    max: floorTo(toUnit(range.max, key), step),
    step,
  };
}

function format(value: number, decimals: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

function ceilTo(value: number, step: number): number {
  return round(Math.ceil(value / step) * step);
}

function floorTo(value: number, step: number): number {
  return round(Math.floor(value / step) * step);
}

/** Kills the float dust that `x / 0.3048 * 0.3048` leaves behind. */
function round(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}
