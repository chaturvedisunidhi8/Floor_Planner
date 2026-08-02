/**
 * Owns the wizard's form state.
 *
 * The BHK selection drives which bedrooms are ticked, and the two essential
 * rooms can never be unticked - both rules are enforced here so no step
 * component has to know about them.
 */

import { useCallback, useMemo, useState } from 'react';

import type {
  BHKType,
  Facing,
  FloorPlanRequirements,
  InteriorStyle,
  PlotShape,
} from '@/types/api';

export const ESSENTIAL_ROOMS = ['living_room', 'kitchen'] as const;

const BEDROOM_ROOMS = ['master_bedroom', 'children_bedroom', 'guest_bedroom'] as const;

const BEDROOMS_FOR: Record<BHKType, string[]> = {
  '1BHK': ['master_bedroom'],
  '2BHK': ['master_bedroom', 'children_bedroom'],
  '3BHK': ['master_bedroom', 'children_bedroom', 'guest_bedroom'],
  '4BHK': ['master_bedroom', 'children_bedroom', 'guest_bedroom'],
};

const INITIAL: FloorPlanRequirements = {
  plot: { width_ft: 30, length_ft: 45, shape: 'rectangle', facing: 'east' },
  bhk: '3BHK',
  rooms: ['living_room', 'dining_room', 'kitchen', ...BEDROOMS_FOR['3BHK']],
  bathrooms: { attached_count: 2, common_count: 1 },
  features: ['balcony', 'parking'],
  style: 'modern',
  notes: '',
};

export function useRequirements() {
  const [requirements, setRequirements] = useState<FloorPlanRequirements>(INITIAL);

  const setPlot = useCallback((patch: Partial<FloorPlanRequirements['plot']>) => {
    setRequirements((current) => {
      const plot = { ...current.plot, ...patch };
      // A square plot keeps both edges in step whichever slider moved.
      if (plot.shape === 'square') {
        const driver = patch.width_ft ?? patch.length_ft ?? plot.width_ft;
        plot.width_ft = driver;
        plot.length_ft = driver;
      }
      return { ...current, plot };
    });
  }, []);

  const setShape = useCallback((shape: PlotShape) => {
    setRequirements((current) => ({
      ...current,
      plot: {
        ...current.plot,
        shape,
        length_ft: shape === 'square' ? current.plot.width_ft : current.plot.length_ft,
      },
    }));
  }, []);

  const setFacing = useCallback((facing: Facing) => {
    setRequirements((current) => ({ ...current, plot: { ...current.plot, facing } }));
  }, []);

  const setBhk = useCallback((bhk: BHKType) => {
    setRequirements((current) => {
      const withoutBedrooms = current.rooms.filter(
        (room) => !BEDROOM_ROOMS.includes(room as (typeof BEDROOM_ROOMS)[number]),
      );
      const bedroomCount = Number(bhk[0]);
      return {
        ...current,
        bhk,
        rooms: [...withoutBedrooms, ...BEDROOMS_FOR[bhk]],
        bathrooms: {
          // Keep the bathroom mix plausible for the new bedroom count.
          attached_count: Math.min(current.bathrooms.attached_count, bedroomCount),
          common_count: current.bathrooms.common_count,
        },
      };
    });
  }, []);

  const toggleRoom = useCallback((room: string) => {
    if (ESSENTIAL_ROOMS.includes(room as (typeof ESSENTIAL_ROOMS)[number])) return;
    setRequirements((current) => ({
      ...current,
      rooms: current.rooms.includes(room)
        ? current.rooms.filter((r) => r !== room)
        : [...current.rooms, room],
    }));
  }, []);

  const toggleFeature = useCallback((feature: string) => {
    setRequirements((current) => ({
      ...current,
      features: current.features.includes(feature)
        ? current.features.filter((f) => f !== feature)
        : [...current.features, feature],
    }));
  }, []);

  const setBathrooms = useCallback((patch: Partial<FloorPlanRequirements['bathrooms']>) => {
    setRequirements((current) => ({
      ...current,
      bathrooms: { ...current.bathrooms, ...patch },
    }));
  }, []);

  const setStyle = useCallback((style: InteriorStyle) => {
    setRequirements((current) => ({ ...current, style }));
  }, []);

  const setNotes = useCallback((notes: string) => {
    setRequirements((current) => ({ ...current, notes: notes.slice(0, 500) }));
  }, []);

  const reset = useCallback(() => setRequirements(INITIAL), []);

  const derived = useMemo(
    () => ({
      areaSqft: Math.round(requirements.plot.width_ft * requirements.plot.length_ft),
      bedroomCount: Number(requirements.bhk[0]),
      totalBathrooms:
        requirements.bathrooms.attached_count + requirements.bathrooms.common_count,
      roomCount: requirements.rooms.length,
    }),
    [requirements],
  );

  return {
    requirements,
    derived,
    setPlot,
    setShape,
    setFacing,
    setBhk,
    toggleRoom,
    toggleFeature,
    setBathrooms,
    setStyle,
    setNotes,
    reset,
  };
}

export type RequirementsController = ReturnType<typeof useRequirements>;
