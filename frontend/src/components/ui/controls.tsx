/**
 * Form primitives for the requirement wizard.
 *
 * All of them are controlled, labelled and keyboard-operable; the wizard steps
 * only compose them, never re-implement input behaviour.
 */

import { useId, useState, type ReactNode } from 'react';

import { cn } from '@/lib/cn';

interface FieldProps {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}

export function Field({ label, hint, children, className }: FieldProps) {
  return (
    <div className={cn('space-y-2', className)}>
      <span className="field-label">{label}</span>
      {children}
      {hint ? <p className="hint">{hint}</p> : null}
    </div>
  );
}

interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange: (value: number) => void;
  disabled?: boolean;
}

export function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  unit = 'ft',
  onChange,
  disabled = false,
}: SliderProps) {
  const id = `slider-${label.replace(/\s+/g, '-').toLowerCase()}`;
  return (
    <div className={cn('space-y-2', disabled && 'opacity-60')}>
      <div className="flex items-baseline justify-between">
        <label htmlFor={id} className="field-label">
          {label}
        </label>
        <span className="font-mono text-sm font-semibold tabular-nums text-blueprint-700">
          {value}
          {unit ? ` ${unit}` : ''}
        </span>
      </div>
      <input
        id={id}
        type="range"
        className="slider"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <div className="flex justify-between text-[11px] text-ink-400">
        <span>
          {min} {unit}
        </span>
        <span>
          {max} {unit}
        </span>
      </div>
    </div>
  );
}

interface RadioOption {
  value: string;
  label: string;
  description?: string;
}

interface RadioGroupProps {
  name: string;
  options: RadioOption[];
  value: string;
  onChange: (value: string) => void;
  columns?: 2 | 3 | 4;
}

export function RadioGroup({ name, options, value, onChange, columns = 4 }: RadioGroupProps) {
  const gridClass = { 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-3', 4: 'sm:grid-cols-4' }[columns];

  return (
    <div role="radiogroup" aria-label={name} className={cn('grid grid-cols-2 gap-2', gridClass)}>
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.value)}
            className={cn(
              'rounded-xl border px-3 py-3 text-left transition',
              selected
                ? 'border-blueprint-600 bg-blueprint-50 ring-1 ring-blueprint-600'
                : 'border-ink-200 bg-white hover:border-ink-300 hover:bg-ink-50',
            )}
          >
            <span
              className={cn(
                'block text-sm font-semibold',
                selected ? 'text-blueprint-800' : 'text-ink-800',
              )}
            >
              {option.label}
            </span>
            {option.description ? (
              <span className="mt-0.5 block text-xs leading-snug text-ink-500">
                {option.description}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

interface CheckboxGridProps {
  options: RadioOption[];
  selected: string[];
  onToggle: (value: string) => void;
  locked?: string[];
  columns?: 2 | 3;
}

export function CheckboxGrid({
  options,
  selected,
  onToggle,
  locked = [],
  columns = 2,
}: CheckboxGridProps) {
  const gridClass = { 2: 'sm:grid-cols-2', 3: 'sm:grid-cols-3' }[columns];

  return (
    <div className={cn('grid gap-2', gridClass)}>
      {options.map((option) => {
        const isSelected = selected.includes(option.value);
        const isLocked = locked.includes(option.value);
        return (
          <label
            key={option.value}
            className={cn(
              'flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 transition',
              isSelected
                ? 'border-blueprint-600 bg-blueprint-50'
                : 'border-ink-200 bg-white hover:border-ink-300 hover:bg-ink-50',
              isLocked && 'cursor-not-allowed opacity-75',
            )}
          >
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0 rounded border-ink-300 text-blueprint-600 focus:ring-blueprint-500"
              checked={isSelected}
              disabled={isLocked}
              onChange={() => onToggle(option.value)}
            />
            <span className="min-w-0">
              <span className="block text-sm font-medium text-ink-800">
                {option.label}
                {isLocked ? <span className="ml-1.5 text-xs text-ink-400">(always on)</span> : null}
              </span>
              {option.description ? (
                <span className="mt-0.5 block text-xs leading-snug text-ink-500">
                  {option.description}
                </span>
              ) : null}
            </span>
          </label>
        );
      })}
    </div>
  );
}

interface SelectProps {
  label: string;
  value: string;
  options: RadioOption[];
  onChange: (value: string) => void;
}

export function Select({ label, value, options, onChange }: SelectProps) {
  const id = `select-${label.replace(/\s+/g, '-').toLowerCase()}`;
  return (
    <div className="space-y-2">
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-xl border border-ink-200 bg-white px-3 py-2.5 text-sm text-ink-800 transition hover:border-ink-300 focus:border-blueprint-500"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

interface CounterProps {
  label: string;
  value: number;
  min: number;
  max: number;
  hint?: string;
  onChange: (value: number) => void;
}

export function Counter({ label, value, min, max, hint, onChange }: CounterProps) {
  return (
    <div className="rounded-xl border border-ink-200 bg-white px-4 py-3">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <span className="block text-sm font-medium text-ink-800">{label}</span>
          {hint ? <span className="mt-0.5 block text-xs text-ink-500">{hint}</span> : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            aria-label={`Decrease ${label}`}
            disabled={value <= min}
            onClick={() => onChange(Math.max(min, value - 1))}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 text-lg leading-none text-ink-600 transition hover:bg-ink-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            &minus;
          </button>
          <span className="w-8 text-center font-mono text-sm font-semibold tabular-nums text-ink-900">
            {value}
          </span>
          <button
            type="button"
            aria-label={`Increase ${label}`}
            disabled={value >= max}
            onClick={() => onChange(Math.min(max, value + 1))}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 text-lg leading-none text-ink-600 transition hover:bg-ink-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            +
          </button>
        </div>
      </div>
    </div>
  );
}

interface NumberStepperProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  disabled?: boolean;
  onChange: (value: number) => void;
}

/**
 * A numeric field with minus/plus buttons.
 *
 * Typing is free-form until the field is left: the value is only pushed up
 * while it is a number inside the allowed range, so clearing the box to retype
 * "8" as "18" does not snap it to the minimum mid-keystroke.
 */
export function NumberStepper({
  label,
  value,
  min,
  max,
  step = 1,
  unit = 'ft',
  disabled = false,
  onChange,
}: NumberStepperProps) {
  const id = useId();
  const [draft, setDraft] = useState<string | null>(null);
  const clamp = (next: number) => Math.min(max, Math.max(min, Math.round(next / step) * step));

  const nudge = (delta: number) => {
    setDraft(null);
    onChange(clamp(value + delta));
  };

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-xs font-medium text-ink-600">
        {label}
      </label>
      <div
        className={cn(
          'flex items-center rounded-lg border border-ink-200 bg-white',
          disabled && 'opacity-50',
        )}
      >
        <StepperButton
          label={`Decrease ${label}`}
          disabled={disabled || value <= min}
          onClick={() => nudge(-step)}
        >
          &minus;
        </StepperButton>
        <div className="flex min-w-0 flex-1 items-baseline justify-center gap-1 px-1">
          <input
            id={id}
            type="number"
            inputMode="numeric"
            className="w-full min-w-0 border-0 bg-transparent p-0 text-center font-mono text-sm font-semibold tabular-nums text-ink-900 focus:outline-none focus:ring-0 disabled:cursor-not-allowed [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            min={min}
            max={max}
            step={step}
            disabled={disabled}
            value={draft ?? value}
            onChange={(event) => {
              const raw = event.target.value;
              setDraft(raw);
              const parsed = Number(raw);
              if (raw !== '' && Number.isFinite(parsed) && parsed >= min && parsed <= max) {
                onChange(clamp(parsed));
              }
            }}
            onBlur={() => {
              if (draft !== null && draft !== '') onChange(clamp(Number(draft) || value));
              setDraft(null);
            }}
          />
          {unit ? <span className="shrink-0 text-xs text-ink-500">{unit}</span> : null}
        </div>
        <StepperButton
          label={`Increase ${label}`}
          disabled={disabled || value >= max}
          onClick={() => nudge(step)}
        >
          +
        </StepperButton>
      </div>
    </div>
  );
}

function StepperButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-lg leading-none text-ink-600 transition hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-30"
    >
      {children}
    </button>
  );
}

interface ButtonProps {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'primary' | 'secondary' | 'ghost';
  disabled?: boolean;
  type?: 'button' | 'submit';
  className?: string;
}

export function Button({
  children,
  onClick,
  variant = 'primary',
  disabled = false,
  type = 'button',
  className,
}: ButtonProps) {
  const variants = {
    primary: 'bg-blueprint-700 text-white hover:bg-blueprint-800 shadow-sm',
    secondary: 'border border-ink-200 bg-white text-ink-800 hover:bg-ink-50',
    ghost: 'text-ink-600 hover:bg-ink-100',
  };

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-xl px-5 py-2.5 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50',
        variants[variant],
        className,
      )}
    >
      {children}
    </button>
  );
}
