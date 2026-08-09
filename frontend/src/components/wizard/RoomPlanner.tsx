/**
 * The room selection grid and its running area budget.
 *
 * Every ticked room contributes `length x width` to the allocated total; the
 * wizard refuses to move on while that total is over the plot.
 */

import { RoomDimensionCard } from '@/components/wizard/RoomDimensionCard';
import { cn } from '@/lib/cn';
import { BASE_UNIT, areaAbbr, formatArea, type UnitKey } from '@/lib/units';
import type { OptionsResponse, RoomDimensions } from '@/types/api';

export const OVER_ALLOCATION_MESSAGE =
  'Selected room dimensions exceed the available plot area. Please reduce one or more room sizes.';

interface RoomPlannerProps {
  options: OptionsResponse;
  selected: string[];
  locked: readonly string[];
  dimensions: Record<string, RoomDimensions>;
  /** The unit the sizes are shown and edited in; they are stored in feet. */
  unit?: UnitKey;
  onToggle: (room: string) => void;
  onDimensionsChange: (room: string, patch: Partial<RoomDimensions>) => void;
}

export function RoomPlanner({
  options,
  selected,
  locked,
  dimensions,
  unit = BASE_UNIT,
  onToggle,
  onDimensionsChange,
}: RoomPlannerProps) {
  return (
    <div className="grid items-start gap-2 sm:grid-cols-2">
      {options.rooms.map((option) => (
        <RoomDimensionCard
          key={option.value}
          option={option}
          selected={selected.includes(option.value)}
          locked={locked.includes(option.value)}
          dimensions={
            dimensions[option.value] ??
            options.room_defaults[option.value] ?? { length_ft: 10, width_ft: 10 }
          }
          range={options.room_dimension_range}
          unit={unit}
          onToggle={onToggle}
          onDimensionsChange={onDimensionsChange}
        />
      ))}
    </div>
  );
}

interface AreaBudgetProps {
  availableSqft: number;
  allocatedSqft: number;
  remainingSqft: number;
  isOverAllocated: boolean;
  unit?: UnitKey;
}

export function AreaBudget({
  availableSqft,
  allocatedSqft,
  remainingSqft,
  isOverAllocated,
  unit = BASE_UNIT,
}: AreaBudgetProps) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Available area" value={availableSqft} unit={unit} />
        <Stat label="Allocated" value={allocatedSqft} unit={unit} />
        <Stat label="Remaining" value={remainingSqft} unit={unit} negative={isOverAllocated} />
      </div>

      {isOverAllocated ? (
        <p
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {OVER_ALLOCATION_MESSAGE}
        </p>
      ) : null}
    </div>
  );
}

function Stat({
  label,
  value,
  unit,
  negative = false,
}: {
  label: string;
  value: number;
  unit: UnitKey;
  negative?: boolean;
}) {
  return (
    <div
      className={cn(
        'rounded-xl border px-3 py-2.5',
        negative ? 'border-red-200 bg-red-50' : 'border-blueprint-200 bg-blueprint-50',
      )}
    >
      <span
        className={cn(
          'block text-[11px] font-medium uppercase tracking-wide',
          negative ? 'text-red-700' : 'text-blueprint-700',
        )}
      >
        {label}
      </span>
      <span
        className={cn(
          'mt-0.5 block font-mono text-lg font-semibold tabular-nums',
          negative ? 'text-red-800' : 'text-blueprint-900',
        )}
      >
        {formatArea(value, unit)}
        <span className="ml-1 text-xs font-medium">{areaAbbr(unit)}</span>
      </span>
    </div>
  );
}
