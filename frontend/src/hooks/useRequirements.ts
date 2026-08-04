/**
 * Owns the wizard's form state.
 *
 * The BHK selection drives which bedrooms are ticked, the two essential rooms
 * can never be unticked, and every ticked room carries a length and a width -
 * all three rules are enforced here so no step component has to know about them.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type {
  BHKType,
  Facing,
  FloorPlanRequirements,
  InteriorStyle,
  OptionsResponse,
  PlotShape,
  RangeSpec,
  RoomDefaults,
  RoomDimensions,
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
  room_dimensions: {},
  bathrooms: { attached_count: 2, common_count: 1 },
  features: ['balcony', 'parking'],
  style: 'modern',
  notes: '',
};

/** Used only until `/options` answers, and for a room the backend forgot to size. */
const FALLBACK_DIMENSIONS: RoomDimensions = { length_ft: 10, width_ft: 10 };
const FALLBACK_RANGE: RangeSpec = { min: 5, max: 40, step: 1, default: 10 };

/**
 * Give every ticked room a size and forget the ones that were unticked.
 *
 * Called after any change to the room list, so the dimensions map and the
 * selection can never disagree - which is what lets the area budget simply add
 * up the map.
 */
function syncDimensions(
  requirements: FloorPlanRequirements,
  defaults: RoomDefaults,
): FloorPlanRequirements {
  const room_dimensions: Record<string, RoomDimensions> = {};
  for (const room of requirements.rooms) {
    room_dimensions[room] =
      requirements.room_dimensions[room] ?? defaults[room] ?? FALLBACK_DIMENSIONS;
  }
  return { ...requirements, room_dimensions };
}

export function useRequirements(options: OptionsResponse | null) {
  const [requirements, setRequirements] = useState<FloorPlanRequirements>(INITIAL);

  // Held in a ref so the setState callbacks below can reach the defaults
  // without every action depending on the options response.
  const defaultsRef = useRef<RoomDefaults>({});
  const rangeRef = useRef<RangeSpec>(FALLBACK_RANGE);

  useEffect(() => {
    if (!options) return;
    defaultsRef.current = options.room_defaults;
    rangeRef.current = options.room_dimension_range;
    // Seed the rooms that were ticked before the options arrived.
    setRequirements((current) => syncDimensions(current, options.room_defaults));
  }, [options]);

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
      return syncDimensions(
        {
          ...current,
          bhk,
          rooms: [...withoutBedrooms, ...BEDROOMS_FOR[bhk]],
          bathrooms: {
            // Keep the bathroom mix plausible for the new bedroom count.
            attached_count: Math.min(current.bathrooms.attached_count, bedroomCount),
            common_count: current.bathrooms.common_count,
          },
        },
        defaultsRef.current,
      );
    });
  }, []);

  const toggleRoom = useCallback((room: string) => {
    if (ESSENTIAL_ROOMS.includes(room as (typeof ESSENTIAL_ROOMS)[number])) return;
    setRequirements((current) =>
      syncDimensions(
        {
          ...current,
          rooms: current.rooms.includes(room)
            ? current.rooms.filter((r) => r !== room)
            : [...current.rooms, room],
        },
        defaultsRef.current,
      ),
    );
  }, []);

  const setRoomDimensions = useCallback((room: string, patch: Partial<RoomDimensions>) => {
    const { min, max } = rangeRef.current;
    const clamp = (value: number) => Math.min(max, Math.max(min, Math.round(value)));

    setRequirements((current) => {
      const existing =
        current.room_dimensions[room] ?? defaultsRef.current[room] ?? FALLBACK_DIMENSIONS;
      const next = { ...existing, ...patch };
      return {
        ...current,
        room_dimensions: {
          ...current.room_dimensions,
          [room]: { length_ft: clamp(next.length_ft), width_ft: clamp(next.width_ft) },
        },
      };
    });
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

  const reset = useCallback(
    () => setRequirements(syncDimensions(INITIAL, defaultsRef.current)),
    [],
  );

  const derived = useMemo(() => {
    const areaSqft = Math.round(requirements.plot.width_ft * requirements.plot.length_ft);
    const allocatedSqft = Math.round(
      requirements.rooms.reduce((total, room) => {
        const dimensions = requirements.room_dimensions[room];
        return total + (dimensions ? dimensions.length_ft * dimensions.width_ft : 0);
      }, 0),
    );

    return {
      areaSqft,
      allocatedSqft,
      remainingSqft: areaSqft - allocatedSqft,
      // The plot is the hard ceiling: walls and circulation come out of what
      // is left, so a plan that is already over cannot be built.
      isOverAllocated: allocatedSqft > areaSqft,
      bedroomCount: Number(requirements.bhk[0]),
      totalBathrooms:
        requirements.bathrooms.attached_count + requirements.bathrooms.common_count,
      roomCount: requirements.rooms.length,
    };
  }, [requirements]);

  return {
    requirements,
    derived,
    setPlot,
    setShape,
    setFacing,
    setBhk,
    toggleRoom,
    setRoomDimensions,
    toggleFeature,
    setBathrooms,
    setStyle,
    setNotes,
    reset,
  };
}

export type RequirementsController = ReturnType<typeof useRequirements>;
