import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { character, myCharacter, agentActivity, messageCharacter } from '../lib/agents';
import { RoomAgents } from '../components/agent-companions';

const tables = {
  my_agent_appearance: [{ variant: 'peach' }],
  room_members: [
    { id: 1n, agentId: 11n, displayName: 'Sam', active: true, isMe: true, agentVariant: 'peach' },
    { id: 2n, agentId: 12n, displayName: 'Sam', active: true, agentVariant: 'yellow' },
    { id: 3n, agentId: 13n, displayName: 'Former member', active: false, agentVariant: 'mint' },
  ],
  room_agent_appearances: [{ membershipId: 1n, variant: 'peach' }, { membershipId: 2n, variant: 'yellow' }],
  room_agents: [{ id: 10n, kind: 'MEDIATOR' }],
  room_presence: [{ membershipId: 2n, online: false }],
  room_agent_activity: [{ agentId: 12n, activity: 'SEARCHING' }],
};
describe('agent identity and activity', () => {
  it('uses a safe local character for absent or invalid data', () => {
    expect(character('https://untrusted.example/image')).toBe('lavender');
    expect(myCharacter({})).toBe('lavender');
    expect(myCharacter(tables)).toBe('peach');
  });
  it('resolves shared speakers by agent ID even with duplicate names', () => {
    expect(messageCharacter(tables, 12n)).toEqual({ variant: 'yellow', mediator: false });
    expect(messageCharacter(tables, undefined)).toEqual({ variant: 'peach', mediator: false });
    expect(messageCharacter(tables, 10n)).toEqual({ variant: 'mint', mediator: true });
  });
  it('separates an offline owner from a working agent, and clears live activity on disconnect', () => {
    expect(agentActivity(tables, 12n, true)).toBe('SEARCHING');
    expect(agentActivity(tables, 12n, false)).toBe('DISCONNECTED');
    expect(agentActivity(tables, 11n, true)).toBe('IDLE');
    expect(agentActivity({ room_agent_activity: [{ agentId: 12n, activity: 'UNRECOGNIZED' }] }, 12n, true)).toBe('IDLE');
    const html = renderToStaticMarkup(createElement(RoomAgents, { tables, connected: true }));
    expect(html).toContain('Exploring options');
    expect(html).toContain('away');
    expect(html).not.toContain('Former member');
    expect(html).toContain('Mediator');
  });
});
