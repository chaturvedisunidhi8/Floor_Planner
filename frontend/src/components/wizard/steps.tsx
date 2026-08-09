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
  Toggle,
} from '@/components/ui/controls';
import { AreaBudget, RoomPlanner } from '@/components/wizard/RoomPlanner';
import { ESSENTIAL_ROOMS, type RequirementsController } from '@/hooks/useRequirements';
import {
  UNIT_OPTIONS,
  areaAbbr,
  convertRange,
  formatArea,
  formatLength,
  toFeet,
  toUnit,
  unit as unitOf,
  type UnitKey,
} from '@/lib/units';
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
  const { requirements, derived, unit, setUnit, setPlot, setShape, setFacing } = controller;
  const isSquare = requirements.plot.shape === 'square';
  const { abbr } = unitOf(unit);

  // The ranges arrive in feet; the sliders work entirely in the chosen unit and
  // convert back on every change, so the stored plot never leaves feet.
  const widthRange = convertRange(options.plot_width_range, unit);
  const lengthRange = convertRange(options.plot_length_range, unit);
  const show = (value: number) => formatLength(toFeet(value, unit), unit);

  return (
    <div className="space-y-7">
      <div className="grid gap-6 sm:grid-cols-2">
        <Field label="Plot shape">
          <RadioGroup
            name="Plot shape"
            columns={2}
            options={options.plot_shapes}
            value={requirements.plot.shape}
            onChange={(value) => setShape(value as PlotShape)}
          />
        </Field>
        <Select
          label="Measurement unit"
          value={unit}
          options={UNIT_OPTIONS}
          onChange={(value) => setUnit(value as UnitKey)}
        />
      </div>

      <div className="grid gap-6 sm:grid-cols-2">
        <Slider
          label="Plot width"
          value={toUnit(requirements.plot.width_ft, unit)}
          min={widthRange.min}
          max={widthRange.max}
          step={widthRange.step}
          unit={abbr}
          format={show}
          onChange={(width) => setPlot({ width_ft: toFeet(width, unit) })}
        />
        <Slider
          label="Plot length"
          value={toUnit(requirements.plot.length_ft, unit)}
          min={lengthRange.min}
          max={lengthRange.max}
          step={lengthRange.step}
          unit={abbr}
          format={show}
          disabled={isSquare}
          onChange={(length) => setPlot({ length_ft: toFeet(length, unit) })}
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
              {formatArea(derived.areaSqft, unit)} {areaAbbr(unit)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export function VastuStep({ options, controller }: StepProps) {
  const { requirements, derived, setVastuEnabled, toggleVastuPrinciple } = controller;
  const { enabled, principles } = requirements.vastu;

  // Ticking "pooja room in the north-east" is a strong hint that the room
  // itself is wanted, and it is chosen two steps later - so say so here.
  const wantsPooja = principles.includes('pooja_northeast');
  const hasPooja = requirements.rooms.includes('pooja_room');

  return (
    <div className="space-y-7">
      <Toggle
        label="Follow Vastu principles"
        hint="Orients the generated plans so key rooms sit in their traditional compass zones."
        checked={enabled}
        onChange={setVastuEnabled}
      />

      {enabled ? (
        <Field
          label="Vastu preferences"
          hint={
            derived.isVastuActive
              ? 'Plans are drawn with north at the top. Preferences are applied where the layout allows.'
              : 'Pick at least one preference, or the plans will be generated without Vastu.'
          }
        >
          <CheckboxGrid
            options={options.vastu_principles}
            selected={principles}
            onToggle={toggleVastuPrinciple}
          />
        </Field>
      ) : (
        <p className="hint">
          Vastu is optional. Leave this off and the layouts are generated purely from your plot
          and room requirements.
        </p>
      )}

      {enabled && wantsPooja && !hasPooja ? (
        <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Add a pooja room in the next step for this preference to have anything to place.
        </p>
      ) : null}
    </div>
  );
}

export function ConfigurationStep({ options, controller }: StepProps) {
  const { requirements, derived, unit, setBhk, toggleRoom, setRoomDimensions } = controller;

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
          unit={unit}
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
          unit={unit}
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

export function SummaryPanel({
  controller,
  vastuOptions = [],
}: {
  controller: RequirementsController;
  /** Used to show each Vastu preference under the label the user picked it by. */
  vastuOptions?: OptionsResponse['vastu_principles'];
}) {
  const { requirements, derived, unit } = controller;
  const label = (value: string) =>
    value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

  const { abbr } = unitOf(unit);
  const length = (feet: number) => formatLength(feet, unit);
  const area = (sqft: number) => `${formatArea(sqft, unit)} ${areaAbbr(unit)}`;

  const entries: [string, string][] = [
    [
      'Plot',
      `${length(requirements.plot.width_ft)} × ${length(requirements.plot.length_ft)} ${abbr}`,
    ],
    ['Area', area(derived.areaSqft)],
    ['Allocated', area(derived.allocatedSqft)],
    ['Facing', label(requirements.plot.facing)],
    ['Configuration', requirements.bhk],
    ['Bathrooms', `${derived.totalBathrooms}`],
    ['Style', label(requirements.style)],
  ];

  const vastuLabel = (value: string) =>
    vastuOptions.find((option) => option.value === value)?.label ?? label(value);

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
                    {length(size.length_ft)}&times;{length(size.width_ft)}
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

      {derived.isVastuActive ? (
        <div className="mt-4 border-t border-ink-100 pt-4">
          <span className="text-xs font-medium uppercase tracking-wide text-ink-500">Vastu</span>
          <ul className="mt-2 space-y-1">
            {requirements.vastu.principles.map((principle) => (
              <li key={principle} className="flex gap-1.5 text-xs leading-snug text-ink-700">
                <span aria-hidden className="text-blueprint-600">
                  &#10003;
                </span>
                {vastuLabel(principle)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </aside>
  );
}
