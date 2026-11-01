import { describe,it,expect } from 'vitest';
import { readTripLocation,tripLocation } from '../lib/trip-location';
describe('trip navigation',()=>{
  it('restores the exact trip and view without accepting arbitrary state',()=>{
    expect(readTripLocation('?trip=34&view=discussion')).toEqual({trip:'34',view:'discussion'});
    expect(readTripLocation('?trip=0 OR 1=1&view=private-admin')).toEqual({trip:undefined,view:'personal'});
    expect(readTripLocation('?trip=-1')).toEqual({trip:undefined,view:'personal'});
  });
  it('includes only navigation state in links',()=>{
    expect(tripLocation('/app','34','plan')).toBe('/app?trip=34&view=plan');
    expect(tripLocation('/app')).toBe('/app');
  });
});
