import ContextChannel from './ContextChannel';
/**
 * React hook for subscribing to ContextChannel state.
 *
 * Manages the full connection lifecycle: subscribes on mount, unsubscribes on
 * unmount, and tracks connection status, errors, and empty states.
 *
 * @template T - The type of the channel state
 * @template Y - The type of the runtime state (optional)
 * @param channel - The ContextChannel instance to subscribe to, or undefined
 * @returns Object with `state`, `connected`, `no_connection_since`, `runtimeState`, `empty`, and `error`
 *
 * @example
 * ```tsx
 * function ProjectView({ projectId }: { projectId: number }) {
 *   const channel = useMemo(() => new ProjectChannel(authToken), []);
 *   const { state: project, connected, error } = useChannelState(channel);
 *
 *   if (!connected) return <LoadingSpinner />;
 *   if (error) return <ErrorMessage error={error} />;
 *   return <h1>{project.name}</h1>;
 * }
 * ```
 */
export declare const useChannelState: <T, Y = unknown>(channel: ContextChannel<T> | undefined) => {
    state: T | undefined;
    connected: boolean;
    no_connection_since: Date | undefined;
    runtimeState: Y | undefined;
    empty: boolean;
    error: Error | undefined;
};
/**
 * React hook for subscribing to a specific instance within a channel's state.
 *
 * @template T - The type of the channel state
 * @template Y - The type of the instance
 * @param channel - The ContextChannel instance
 * @param instance_type - The `_instance_type` string of the instance to watch
 * @param instance_id - The ID of the instance, or undefined
 * @returns The instance object, or null if not available
 */
export declare const useChannelInstance: <T, Y>(channel: ContextChannel<T> | undefined, instance_type: string, instance_id: number | undefined) => Y | null | undefined;
