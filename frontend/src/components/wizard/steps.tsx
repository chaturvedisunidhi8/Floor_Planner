/**
 * The four wizard steps.
 *
 * Each one is a pure presentation component over the shared requirements
 * controller - there is no per-step state, so moving back and forth never
 * loses a selection.
 */

import {
  CheckboxGrid,
  Counter,
  Field,
  RadioGroup,
  Select,
  Slider,
} from '@/components/ui/controls';
import { AreaBudget, RoomPlanner } from '@/components/wizard/RoomPlanner';
import { ESSENTIAL_ROOMS, type RequirementsController } from '@/hooks/useRequirements';
import type {
  BHKType,
  Facing,
  InteriorStyle,
  OptionsResponse,
  PlotShape,
} from '@/types/api';

interface StepProps {
  options: OptionsResponse;
  controller: RequirementsController;
}

export function PlotStep({ options, controller }: StepProps) {
  const { requirements, derived, setPlot, setShape, setFacing } = controller;
  const isSquare = requirements.plot.shape === 'square';

  return (
    <div className="space-y-7">
      <Field label="Plot shape">
        <RadioGroup
          name="Plot shape"
          columns={2}
          options={options.plot_shapes}
          value={requirements.plot.shape}
          onChange={(value) => setShape(value as PlotShape)}
        />
      </Field>

      <div className="grid gap-6 sm:grid-cols-2">
        <Slider
          label="Plot width"
          value={requirements.plot.width_ft}
          min={options.plot_width_range.min}
          max={options.plot_width_range.max}
          step={options.plot_width_range.step}
          onChange={(width_ft) => setPlot({ width_ft })}
        />
        <Slider
          label="Plot length"
          value={requirements.plot.length_ft}
          min={options.plot_length_range.min}
          max={options.plot_length_range.max}
          step={options.plot_length_range.step}
          disabled={isSquare}
          onChange={(length_ft) => setPlot({ length_ft })}
        />
      </div>

      {isSquare ? (
        <p className="hint -mt-3">A square plot keeps both sides equal to the width.</p>
      ) : null}

      <div className="grid gap-6 sm:grid-cols-2">
        <Select
          label="Plot facing"
          value={requirements.plot.facing}
          options={options.facings}
          onChange={(value) => setFacing(value as Facing)}
        />
        <div className="flex items-end">
          <div className="w-full rounded-xl border border-blueprint-200 bg-blueprint-50 px-4 py-3">
            <span className="block text-xs font-medium uppercase tracking-wide text-blueprint-700">
              Total plot area
            </span>
            <span className="mt-0.5 block font-mono text-xl font-semibold text-blueprint-900">
              {derived.areaSqft.toLocaleString()} sq ft
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ConfigurationStep({ options, controller }: StepProps) {
  const { requirements, derived, setBhk, toggleRoom, setRoomDimensions } = controller;

  return (
    <div className="space-y-7">
      <Field
        label="House configuration"
        hint="Bedrooms are ticked for you below to match this choice."
      >
        <RadioGroup
          name="Configuration"
          options={options.bhk_types}
          value={requirements.bhk}
          onChange={(value) => setBhk(value as BHKType)}
        />
      </Field>

      <Field label="Area budget" hint="Walls and circulation come out of the remaining area.">
        <AreaBudget
          availableSqft={derived.areaSqft}
          allocatedSqft={derived.allocatedSqft}
          remainingSqft={derived.remainingSqft}
          isOverAllocated={derived.isOverAllocated}
        />
      </Field>

      <Field
        label="Required rooms and sizes"
        hint={`${derived.roomCount} rooms selected. The living room and kitchen are always included.`}
      >
        <RoomPlanner
          options={options}
          selected={requirements.rooms}
          locked={ESSENTIAL_ROOMS}
          dimensions={requirements.room_dimensions}
          onToggle={toggleRoom}
          onDimensionsChange={setRoomDimensions}
        />
      </Field>
    </div>
  );
}

export function BathroomStep({ options, controller }: StepProps) {
  const { requirements, derived, setBathrooms, toggleFeature } = controller;

  return (
    <div className="space-y-7">
      <Field
        label="Bathrooms"
        hint={`${derived.totalBathrooms} bathrooms in total for a ${requirements.bhk} home.`}
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <Counter
            label="Attached bathrooms"
            hint="Inside a bedroom"
            value={requirements.bathrooms.attached_count}
            min={0}
            max={Math.min(options.max_attached_bathrooms, derived.bedroomCount)}
            onChange={(attached_count) => setBathrooms({ attached_count })}
          />
          <Counter
            label="Common bathrooms"
            hint="Shared by the household"
            value={requirements.bathrooms.common_count}
            min={0}
            max={options.max_common_bathrooms}
            onChange={(common_count) => setBathrooms({ common_count })}
          />
        </div>
      </Field>

      <Field label="Additional features" hint="Optional outdoor and service spaces.">
        <CheckboxGrid
          options={options.features}
          selected={requirements.features}
          onToggle={toggleFeature}
        />
      </Field>
    </div>
  );
}

export function StyleStep({ options, controller }: StepProps) {
  const { requirements, setStyle, setNotes } = controller;

  return (
    <div className="space-y-7">
      <Field label="Interior style">
        <RadioGroup
          name="Interior style"
          columns={2}
          options={options.styles}
          value={requirements.style}
          onChange={(value) => setStyle(value as InteriorStyle)}
        />
      </Field>

      <div className="space-y-2">
        <label htmlFor="notes" className="field-label">
          Anything else? <span className="font-normal text-ink-400">(optional)</span>
        </label>
        <textarea
          id="notes"
          rows={3}
          maxLength={500}
          value={requirements.notes}
          placeholder="e.g. keep the kitchen away from the entrance, we need a large store room"
          onChange={(event) => setNotes(event.target.value)}
          className="w-full resize-none rounded-xl border border-ink-200 bg-white px-3 py-2.5 text-sm text-ink-800 placeholder:text-ink-400 focus:border-blueprint-500"
        />
        <p className="hint text-right">{requirements.notes.length} / 500</p>
      </div>
    </div>
  );
}

export function SummaryPanel({ controller }: { controller: RequirementsController }) {
  const { requirements, derived } = controller;
  const label = (value: string) =>
    value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

  const entries: [string, string][] = [
    ['Plot', `${requirements.plot.width_ft} x ${requirements.plot.length_ft} ft`],
    ['Area', `${derived.areaSqft.toLocaleString()} sq ft`],
    ['Allocated', `${derived.allocatedSqft.toLocaleString()} sq ft`],
    ['Facing', label(requirements.plot.facing)],
    ['Configuration', requirements.bhk],
    ['Bathrooms', `${derived.totalBathrooms}`],
    ['Style', label(requirements.style)],
  ];

  return (
    <aside className="card sticky top-6 p-5">
      <h3 className="text-sm font-semibold uppercase tracking-wide text-ink-500">Your brief</h3>
      <dl className="mt-4 space-y-2.5">
        {entries.map(([key, value]) => (
          <div key={key} className="flex items-baseline justify-between gap-3 text-sm">
            <dt className="text-ink-500">{key}</dt>
            <dd className="text-right font-medium text-ink-900">{value}</dd>
          </div>
        ))}
      </dl>

      <div className="mt-4 border-t border-ink-100 pt-4">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-500">Rooms</span>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {requirements.rooms.map((room) => {
            const size = requirements.room_dimensions[room];
            return (
              <span
                key={room}
                className="rounded-md bg-ink-100 px-2 py-1 text-xs font-medium text-ink-700"
              >
                {label(room)}
                {size ? (
                  <span className="ml-1 font-mono text-ink-500">
                    {size.length_ft}&times;{size.width_ft}
                  </span>
                ) : null}
              </span>
            );
          })}
        </div>
      </div>

      {requirements.features.length > 0 ? (
        <div className="mt-4 border-t border-ink-100 pt-4">
          <span className="text-xs font-medium uppercase tracking-wide text-ink-500">Features</span>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {requirements.features.map((feature) => (
              <span
                key={feature}
                className="rounded-md bg-blueprint-100 px-2 py-1 text-xs font-medium text-blueprint-800"
              >
                {label(feature)}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </aside>
  );
}
