/**
 * One selectable room, with its own length/width controls.
 *
 * Ticking the room expands the card to reveal the two steppers; the controls
 * stay mounted so the reveal can be animated, and are disabled while collapsed
 * so they never take keyboard focus.
 */

import { NumberStepper } from '@/components/ui/controls';
import { cn } from '@/lib/cn';
import type { OptionItem, RangeSpec, RoomDimensions } from '@/types/api';

export interface RoomDimensionCardProps {
  option: OptionItem;
  selected: boolean;
  /** Rooms the house cannot do without - tickable off is not an option. */
  locked?: boolean;
  dimensions: RoomDimensions;
  range: RangeSpec;
  onToggle: (room: string) => void;
  onDimensionsChange: (room: string, patch: Partial<RoomDimensions>) => void;
}

export function RoomDimensionCard({
  option,
  selected,
  locked = false,
  dimensions,
  range,
  onToggle,
  onDimensionsChange,
}: RoomDimensionCardProps) {
  const area = Math.round(dimensions.length_ft * dimensions.width_ft);
  const panelId = `room-dimensions-${option.value}`;

  return (
    <div
      className={cn(
        'rounded-xl border transition-colors',
        selected ? 'border-blueprint-600 bg-blueprint-50' : 'border-ink-200 bg-white',
        !selected && !locked && 'hover:border-ink-300 hover:bg-ink-50',
      )}
    >
      <label
        className={cn(
          'flex cursor-pointer items-start gap-3 px-3 py-2.5',
          locked && 'cursor-not-allowed opacity-75',
        )}
      >
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4 shrink-0 rounded border-ink-300 text-blueprint-600 focus:ring-blueprint-500"
          checked={selected}
          disabled={locked}
          aria-controls={panelId}
          aria-expanded={selected}
          onChange={() => onToggle(option.value)}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span className="text-sm font-medium text-ink-800">
              {option.label}
              {locked ? <span className="ml-1.5 text-xs text-ink-400">(always on)</span> : null}
            </span>
            {selected ? (
              <span className="shrink-0 font-mono text-xs font-semibold tabular-nums text-blueprint-700">
                {area.toLocaleString()} sq ft
              </span>
            ) : null}
          </span>
          {option.description ? (
            <span className="mt-0.5 block text-xs leading-snug text-ink-500">
              {option.description}
            </span>
          ) : null}
        </span>
      </label>

      {/* 0fr -> 1fr animates to the panel's natural height without measuring it. */}
      <div
        id={panelId}
        aria-hidden={!selected}
        className={cn(
          'grid transition-[grid-template-rows] duration-300 ease-out',
          selected ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
        )}
      >
        <div className="overflow-hidden">
          <div className="grid grid-cols-2 gap-3 border-t border-blueprint-200/70 px-3 py-3">
            <NumberStepper
              label="Length"
              value={dimensions.length_ft}
              min={range.min}
              max={range.max}
              step={range.step}
              disabled={!selected}
              onChange={(length_ft) => onDimensionsChange(option.value, { length_ft })}
            />
            <NumberStepper
              label="Width"
              value={dimensions.width_ft}
              min={range.min}
              max={range.max}
              step={range.step}
              disabled={!selected}
              onChange={(width_ft) => onDimensionsChange(option.value, { width_ft })}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
