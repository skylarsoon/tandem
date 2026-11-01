export type TripView = 'personal' | 'discussion' | 'plan' | 'limits';
const views: TripView[] = ['personal', 'discussion', 'plan', 'limits'];
export function readTripLocation(search: string): { trip?: string; view: TripView } {
  const params = new URLSearchParams(search), id = params.get('trip');
  return { trip: id && /^[1-9]\d{0,19}$/.test(id) ? id : undefined,
    view: views.includes(params.get('view') as TripView) ? params.get('view') as TripView : 'personal' };
}
export function tripLocation(pathname: string, trip?: string, view: TripView = 'personal') {
  if (!trip) return pathname;
  const params = new URLSearchParams({ trip });
  if (view !== 'personal') params.set('view', view);
  return `${pathname}?${params}`;
}
