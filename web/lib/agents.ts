import type { Row } from './state';
import type { Tables } from './planning';

export const characters = [
  { id: 'lavender', name: 'Lavender' },
  { id: 'peach', name: 'Peach' },
  { id: 'mint', name: 'Mint' },
  { id: 'yellow', name: 'Butter' },
] as const;
export type Character = typeof characters[number]['id'];
export function character(value: unknown): Character {
  return characters.find(c => c.id === value)?.id ?? 'lavender';
}
export function myCharacter(tables: Tables): Character {
  const member = tables.room_members?.find(m => m.isMe);
  return member ? memberCharacter(tables, member) : character(tables.my_agent_appearance?.[0]?.variant);
}
export function memberCharacter(tables: Tables, member?: Row): Character {
  if (!member) return 'lavender';
  // A room uses a stable, collision-free palette. Every participant sees the
  // same assignment, including when appearance views are not deployed yet.
  const members = [...(tables.room_members || [])].sort((a, b) => BigInt(a.id) < BigInt(b.id) ? -1 : 1);
  const used = new Set<Character>();
  for (const current of members) {
    const saved = tables.room_agent_appearances?.find(a => String(a.membershipId) === String(current.id))?.variant;
    const preferred = saved == null ? undefined : character(saved);
    const assigned = preferred && !used.has(preferred) ? preferred : characters.find(c => !used.has(c.id))?.id ?? characters[members.indexOf(current) % 4].id;
    used.add(assigned);
    if (String(current.id) === String(member.id)) return assigned;
  }
  return 'lavender';
}

export const activityLabels = {
  IDLE: 'Ready to help', THINKING: 'Thinking', SEARCHING: 'Exploring options',
  CHECKING: 'Checking the details', WRITING: 'Writing', WAITING_FOR_OWNER: 'Waiting for input',
  DISCONNECTED: 'Reconnecting',
} as const;
export type AgentActivity = keyof typeof activityLabels;

export function agentActivity(tables: Tables, agentId: unknown, connected: boolean): AgentActivity {
  if (!connected) return 'DISCONNECTED';
  const value = agentId == null ? undefined : tables.room_agent_activity?.find(a => String(a.agentId) === String(agentId))?.activity;
  return Object.hasOwn(activityLabels, value ?? '') && value !== 'DISCONNECTED' ? value as AgentActivity : 'IDLE';
}

// Message attribution uses agent IDs, never display names or list positions.
export function messageCharacter(tables: Tables, senderAgentId: unknown): { variant: Character; mediator: boolean } {
  const member: Row | undefined = tables.room_members?.find(m => senderAgentId != null && String(m.agentId) === String(senderAgentId));
  const mediator = tables.room_agents?.some(a => senderAgentId != null && String(a.id) === String(senderAgentId) && a.kind === 'MEDIATOR') ?? false;
  return { variant: mediator ? 'mint' : senderAgentId == null ? myCharacter(tables) : memberCharacter(tables, member), mediator };
}
