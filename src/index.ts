/**
 * @rxdjango/react - Real-time state synchronization between Django and React.
 *
 * Provides WebSocket-based communication with Django ContextChannels,
 * automatic state reconstruction from flat instances, and React hooks
 * for seamless integration.
 *
 * @packageDocumentation
 *
 * @example
 * ```typescript
 * import { useChannelState } from '@rxdjango/react';
 * import { ProjectChannel } from './generated/channels';
 *
 * function App({ projectId }) {
 *   const channel = useMemo(() => new ProjectChannel(authToken), []);
 *   const { state, connected } = useChannelState(channel);
 *   // ...
 * }
 * ```
 */
export { default as ContextChannel } from './ContextChannel';
export * from './ContextChannel.interfaces'
export { useChannelState, useChannelInstance } from './hook';
