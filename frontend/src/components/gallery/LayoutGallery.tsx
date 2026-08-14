/**
 * Results gallery: view, compare and select a generated layout.
 */

import { useState } from 'react';

import { Button } from '@/components/ui/controls';
import { imageUrl } from '@/api/client';
import { cn } from '@/lib/cn';
import type { GeneratedLayout, GenerationResponse, TemplateMatch } from '@/types/api';

interface GalleryProps {
  result: GenerationResponse;
  selectedId: string | null;
  onSelect: (layout: GeneratedLayout) => void;
  onRestart: () => void;
  onRegenerate: () => void;
}

export function LayoutGallery({
  result,
  selectedId,
  onSelect,
  onRestart,
  onRegenerate,
}: GalleryProps) {
  const [preview, setPreview] = useState<GeneratedLayout | null>(null);
  const [compare, setCompare] = useState(false);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-2xl font-semibold tracking-tight text-ink-900">
            {result.layouts.length} layouts generated
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-ink-600">{result.requirement_summary}</p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button variant="secondary" onClick={() => setCompare((value) => !value)}>
            {compare ? 'Grid view' : 'Compare side by side'}
          </Button>
          <Button variant="secondary" onClick={onRegenerate}>
            Generate more
          </Button>
          <Button variant="ghost" onClick={onRestart}>
            Start over
          </Button>
        </div>
      </header>

      {result.warnings.length > 0 ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
          <ul className="space-y-1 text-sm text-amber-900">
            {result.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div
        className={cn(
          'grid gap-5',
          compare
            ? 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4'
            : 'grid-cols-1 md:grid-cols-2 xl:grid-cols-2',
        )}
      >
        {result.layouts.map((layout, index) => (
          <LayoutCard
            key={layout.id}
            layout={layout}
            index={index}
            compact={compare}
            isSelected={layout.id === selectedId}
            onSelect={() => onSelect(layout)}
            onPreview={() => setPreview(layout)}
          />
        ))}
      </div>

      <MatchPanel matches={result.matches} />

      {preview ? <PreviewDialog layout={preview} onClose={() => setPreview(null)} /> : null}
    </div>
  );
}

interface CardProps {
  layout: GeneratedLayout;
  index: number;
  compact: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onPreview: () => void;
}

function LayoutCard({ layout, index, compact, isSelected, onSelect, onPreview }: CardProps) {
  const infeasible = layout.status === 'infeasible';

  return (
    <article
      className={cn(
        'card animate-fade-up overflow-hidden transition',
        infeasible
          ? 'border-rose-200 bg-rose-50/50'
          : isSelected
            ? 'ring-2 ring-blueprint-600'
            : 'hover:shadow-lift',
      )}
      style={{ animationDelay: `${index * 60}ms` }}
    >
      {infeasible ? (
        <div className="flex aspect-square w-full flex-col items-center justify-center gap-3 bg-rose-50 p-6 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-rose-100 text-xl text-rose-600">
            !
          </span>
          <p className="text-sm font-semibold text-rose-900">No layout possible</p>
          <p className="max-w-[22rem] text-xs text-rose-700">
            {layout.infeasibility?.detail ?? layout.description}
          </p>
        </div>
      ) : (
        <button
          type="button"
          onClick={onPreview}
          className="group block w-full bg-ink-50"
          aria-label={`Enlarge ${layout.name}`}
        >
          <img
            src={imageUrl(layout)}
            alt={`Floor plan: ${layout.name}, ${layout.bhk} on a ${layout.plot_size_label} plot`}
            loading={index < 2 ? 'eager' : 'lazy'}
            className="aspect-square w-full object-contain transition duration-300 group-hover:scale-[1.02]"
          />
        </button>
      )}

      <div className={cn('space-y-3', compact ? 'p-4' : 'p-5')}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-base font-semibold text-ink-900">{layout.name}</h3>
            <p className="mt-0.5 text-xs text-ink-500">
              {infeasible ? (
                <span className="text-rose-700">Brief cannot be packed</span>
              ) : (
                <>
                  {layout.bhk} &middot; {layout.plot_size_label} &middot;{' '}
                  {layout.built_up_sqft.toLocaleString()} sq ft built-up
                </>
              )}
            </p>
          </div>
          {isSelected ? (
            <span className="shrink-0 rounded-full bg-blueprint-700 px-2.5 py-1 text-xs font-semibold text-white">
              Selected
            </span>
          ) : null}
        </div>

        {!compact && !infeasible ? (
          <p className="text-sm leading-relaxed text-ink-600">{layout.description}</p>
        ) : null}

        {infeasible && layout.infeasibility?.suggestions.length ? (
          <ul className="space-y-1 rounded-lg border border-rose-200 bg-white px-3 py-2">
            {layout.infeasibility.suggestions.map((suggestion) => (
              <li key={suggestion} className="text-xs leading-relaxed text-rose-800">
                &middot; {suggestion}
              </li>
            ))}
          </ul>
        ) : null}

        <div className="flex flex-wrap items-center gap-1.5">
          {infeasible ? (
            <Badge tone="rose">Infeasible</Badge>
          ) : (
            <>
              <Badge tone="blueprint">{Math.round(layout.match_score * 100)}% match</Badge>
              <Badge>{layout.rooms.length} spaces</Badge>
              <Badge>{layout.render_mode}</Badge>
              {layout.vastu_score !== null ? (
                <Badge tone="blueprint" title={layout.vastu_notes.join('\n')}>
                  {Math.round(layout.vastu_score * 100)}% vastu
                </Badge>
              ) : null}
            </>
          )}
        </div>

        <p className="text-xs text-ink-400">
          Based on {layout.source_template_id} &mdash; {layout.source_template_name}
        </p>

        <div className="flex gap-2 pt-1">
          {infeasible ? (
            <Button variant="secondary" disabled className="flex-1">
              Select this layout
            </Button>
          ) : (
            <>
              <Button
                variant={isSelected ? 'secondary' : 'primary'}
                onClick={onSelect}
                className="flex-1"
              >
                {isSelected ? 'Your pick' : 'Select this layout'}
              </Button>
              <Button variant="ghost" onClick={onPreview}>
                Enlarge
              </Button>
            </>
          )}
        </div>
      </div>
    </article>
  );
}

function Badge({
  children,
  tone = 'neutral',
  title,
}: {
  children: React.ReactNode;
  tone?: 'neutral' | 'blueprint' | 'rose';
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        'rounded-md px-2 py-0.5 text-xs font-medium capitalize',
        tone === 'blueprint'
          ? 'bg-blueprint-100 text-blueprint-800'
          : tone === 'rose'
            ? 'bg-rose-100 text-rose-800'
            : 'bg-ink-100 text-ink-600',
      )}
    >
      {children}
    </span>
  );
}

function MatchPanel({ matches }: { matches: TemplateMatch[] }) {
  const [open, setOpen] = useState(false);

  return (
    <section className="card overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between px-5 py-4 text-left transition hover:bg-ink-50"
        aria-expanded={open}
      >
        <div>
          <h3 className="text-sm font-semibold text-ink-900">
            How the AI chose these templates
          </h3>
          <p className="mt-0.5 text-xs text-ink-500">
            {matches.length} closest matches from the knowledge base, with scores
          </p>
        </div>
        <span className="text-ink-400">{open ? '−' : '+'}</span>
      </button>

      {open ? (
        <div className="border-t border-ink-100 px-5 py-4">
          <ol className="space-y-4">
            {matches.map((match) => (
              <li key={match.template.id}>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm font-medium text-ink-900">
                    {match.rank}. {match.template.name}
                  </span>
                  <span className="font-mono text-sm font-semibold text-blueprint-700">
                    {(match.score * 100).toFixed(0)}%
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-ink-500">
                  {match.template.id} &middot; {match.template.bhk} &middot;{' '}
                  {match.template.plot_width_ft}x{match.template.plot_length_ft} ft &middot;{' '}
                  {match.rationale}
                </p>
                <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
                  {Object.entries(match.breakdown).map(([criterion, value]) => (
                    <div key={criterion} className="flex items-center gap-2">
                      <span className="w-28 shrink-0 truncate text-[11px] capitalize text-ink-500">
                        {criterion.replace(/_/g, ' ')}
                      </span>
                      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-100">
                        <span
                          className="block h-full rounded-full bg-blueprint-500"
                          style={{ width: `${Math.round(value * 100)}%` }}
                        />
                      </span>
                    </div>
                  ))}
                </div>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  );
}

function PreviewDialog({ layout, onClose }: { layout: GeneratedLayout; onClose: () => void }) {
  if (layout.status === 'infeasible') {
    return null;
  }
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${layout.name} full size`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="max-h-full w-full max-w-4xl overflow-auto rounded-2xl bg-white p-4 shadow-lift"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-ink-900">{layout.name}</h3>
            <p className="text-sm text-ink-500">
              {layout.bhk} &middot; {layout.plot_size_label} &middot;{' '}
              {layout.built_up_sqft.toLocaleString()} sq ft built-up
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <a
              href={imageUrl(layout)}
              download={`${layout.name.replace(/\s+/g, '-').toLowerCase()}.png`}
              className="inline-flex items-center rounded-xl border border-ink-200 px-4 py-2 text-sm font-semibold text-ink-800 transition hover:bg-ink-50"
            >
              Download
            </a>
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>

        <img
          src={imageUrl(layout)}
          alt={`Full size floor plan: ${layout.name}`}
          className="w-full rounded-xl border border-ink-100"
        />

        <div className="mt-4">
          <h4 className="text-sm font-semibold text-ink-900">Room schedule</h4>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[420px] text-sm">
              <thead>
                <tr className="border-b border-ink-100 text-left text-xs uppercase tracking-wide text-ink-500">
                  <th className="py-2 pr-4 font-medium">Room</th>
                  <th className="py-2 pr-4 font-medium">Size</th>
                  <th className="py-2 font-medium text-right">Area</th>
                </tr>
              </thead>
              <tbody>
                {layout.rooms.map((room, index) => (
                  <tr key={`${room.name}-${index}`} className="border-b border-ink-50">
                    <td className="py-1.5 pr-4 text-ink-800">{room.name}</td>
                    <td className="py-1.5 pr-4 font-mono text-xs text-ink-600">
                      {room.width} x {room.height} ft
                    </td>
                    <td className="py-1.5 text-right font-mono text-xs text-ink-600">
                      {Math.round(room.width * room.height)} sq ft
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
