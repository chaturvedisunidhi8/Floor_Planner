/**
 * Application shell: loads the wizard options, walks the user through the four
 * requirement steps, posts the brief and shows the resulting gallery.
 */

import { useCallback, useEffect, useState } from 'react';

import { api, FloorPlannerApiError } from '@/api/client';
import { LayoutGallery } from '@/components/gallery/LayoutGallery';
import { Button } from '@/components/ui/controls';
import { OVER_ALLOCATION_MESSAGE } from '@/components/wizard/RoomPlanner';
import {
  BathroomStep,
  ConfigurationStep,
  PlotStep,
  StyleStep,
  SummaryPanel,
  VastuStep,
} from '@/components/wizard/steps';
import { useRequirements } from '@/hooks/useRequirements';
import { cn } from '@/lib/cn';
import type { GeneratedLayout, GenerationResponse, OptionsResponse } from '@/types/api';

const STEPS = [
  { key: 'plot', title: 'Plot details', blurb: 'Tell us about the land you are building on.' },
  {
    key: 'vastu',
    title: 'Vastu preferences',
    blurb: 'Optional: have the plans follow traditional directional principles.',
  },
  {
    key: 'configuration',
    title: 'Configuration',
    blurb: 'How many bedrooms, and which rooms do you need?',
  },
  {
    key: 'bathrooms',
    title: 'Bathrooms & features',
    blurb: 'Set the bathroom mix and any extras.',
  },
  { key: 'style', title: 'Style', blurb: 'Pick the look and add any final notes.' },
] as const;

export default function App() {
  const [options, setOptions] = useState<OptionsResponse | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [result, setResult] = useState<GenerationResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const controller = useRequirements(options);
  // Rooms that do not fit the plot cannot be planned, so the wizard holds the
  // user on the configuration step until the budget balances.
  const isBlocked = controller.derived.isOverAllocated;

  useEffect(() => {
    let cancelled = false;
    api
      .options()
      .then((data) => {
        if (!cancelled) setOptions(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setOptionsError(
          err instanceof FloorPlannerApiError
            ? err.message
            : 'Could not load the planner options.',
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const generate = useCallback(async () => {
    setIsGenerating(true);
    setError(null);
    try {
      const response = await api.generate({ requirements: controller.requirements });
      setResult(response);
      setSelectedId(null);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err: unknown) {
      setError(
        err instanceof FloorPlannerApiError
          ? err.message
          : 'Something went wrong while generating your layouts.',
      );
    } finally {
      setIsGenerating(false);
    }
  }, [controller.requirements]);

  const selectLayout = useCallback(async (layout: GeneratedLayout) => {
    setSelectedId(layout.id);
    try {
      await api.selectLayout(layout.id);
    } catch {
      // The choice is already reflected in the UI; persisting it is best effort.
    }
  }, []);

  const restart = useCallback(() => {
    setResult(null);
    setSelectedId(null);
    setStep(0);
    controller.reset();
  }, [controller]);

  if (optionsError) {
    return <FullPageMessage title="Cannot reach the planner" detail={optionsError} />;
  }

  if (!options) {
    return <FullPageMessage title="Loading the planner" detail="Fetching your options…" />;
  }

  return (
    <div className="min-h-screen bg-ink-50">
      <Header />

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
        {result ? (
          <LayoutGallery
            result={result}
            selectedId={selectedId}
            onSelect={selectLayout}
            onRestart={restart}
            onRegenerate={generate}
          />
        ) : (
          <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
            <div className="space-y-6">
              <StepIndicator current={step} onJump={setStep} />

              <section className="card p-6 sm:p-8">
                <h2 className="text-xl font-semibold tracking-tight text-ink-900">
                  {STEPS[step].title}
                </h2>
                <p className="mt-1 text-sm text-ink-600">{STEPS[step].blurb}</p>

                <div className="mt-7">
                  {STEPS[step].key === 'plot' ? (
                    <PlotStep options={options} controller={controller} />
                  ) : null}
                  {STEPS[step].key === 'vastu' ? (
                    <VastuStep options={options} controller={controller} />
                  ) : null}
                  {STEPS[step].key === 'configuration' ? (
                    <ConfigurationStep options={options} controller={controller} />
                  ) : null}
                  {STEPS[step].key === 'bathrooms' ? (
                    <BathroomStep options={options} controller={controller} />
                  ) : null}
                  {STEPS[step].key === 'style' ? (
                    <StyleStep options={options} controller={controller} />
                  ) : null}
                </div>

                {/* The configuration step shows this beside its own budget. */}
                {isBlocked && STEPS[step].key !== 'configuration' ? (
                  <p
                    role="alert"
                    className="mt-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
                  >
                    {OVER_ALLOCATION_MESSAGE}
                  </p>
                ) : null}

                {error ? (
                  <p
                    role="alert"
                    className="mt-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
                  >
                    {error}
                  </p>
                ) : null}

                <div className="mt-8 flex items-center justify-between border-t border-ink-100 pt-6">
                  <Button
                    variant="ghost"
                    disabled={step === 0 || isGenerating}
                    onClick={() => setStep((value) => value - 1)}
                  >
                    Back
                  </Button>

                  {step < STEPS.length - 1 ? (
                    <Button
                      onClick={() => setStep((value) => value + 1)}
                      disabled={isBlocked}
                    >
                      Continue
                    </Button>
                  ) : (
                    <Button onClick={generate} disabled={isGenerating || isBlocked}>
                      {isGenerating ? (
                        <>
                          <Spinner />
                          Generating layouts…
                        </>
                      ) : (
                        'Generate my floor plans'
                      )}
                    </Button>
                  )}
                </div>
              </section>
            </div>

            <SummaryPanel controller={controller} vastuOptions={options.vastu_principles} />
          </div>
        )}
      </main>

      <div className="pb-10" />
    </div>
  );
}

function Header() {
  return (
    <header className="border-b border-ink-200/70 bg-white">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
        <div className="flex items-center gap-3">
          <span
            aria-hidden
            className="flex h-9 w-9 items-center justify-center rounded-lg bg-blueprint-700 text-sm font-bold text-white"
          >
            FP
          </span>
          <div>
            <h1 className="text-base font-semibold tracking-tight text-ink-900">Floor Planner</h1>
            <p className="text-xs text-ink-500">AI generated house layouts</p>
          </div>
        </div>
      </div>
    </header>
  );
}

function StepIndicator({
  current,
  onJump,
}: {
  current: number;
  onJump: (index: number) => void;
}) {
  return (
    <nav aria-label="Progress" className="flex gap-2">
      {STEPS.map((item, index) => {
        const state = index === current ? 'current' : index < current ? 'done' : 'upcoming';
        return (
          <button
            key={item.key}
            type="button"
            onClick={() => onJump(index)}
            aria-current={state === 'current' ? 'step' : undefined}
            className={cn(
              'flex-1 rounded-xl border px-3 py-2.5 text-left transition',
              state === 'current' && 'border-blueprint-600 bg-white ring-1 ring-blueprint-600',
              state === 'done' && 'border-blueprint-200 bg-blueprint-50 hover:bg-blueprint-100',
              state === 'upcoming' && 'border-ink-200 bg-white hover:bg-ink-50',
            )}
          >
            <span
              className={cn(
                'block text-[11px] font-medium uppercase tracking-wide',
                state === 'upcoming' ? 'text-ink-400' : 'text-blueprint-600',
              )}
            >
              Step {index + 1}
            </span>
            <span
              className={cn(
                'mt-0.5 block truncate text-sm font-semibold',
                state === 'upcoming' ? 'text-ink-500' : 'text-ink-900',
              )}
            >
              {item.title}
            </span>
          </button>
        );
      })}
    </nav>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden
      className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
    />
  );
}

function FullPageMessage({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-ink-50 px-4">
      <div className="card max-w-md p-8 text-center">
        <h1 className="text-lg font-semibold text-ink-900">{title}</h1>
        <p className="mt-2 text-sm text-ink-600">{detail}</p>
      </div>
    </div>
  );
}
